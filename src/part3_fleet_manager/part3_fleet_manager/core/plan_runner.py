"""When the plan's search runs (4O 정리): outside the tick, its result used in the next tick.

The search takes up to plan_limit_ms (200 ms). Run inside the 10 Hz tick it held everything else up, the
proximity stop included. Now the tick only takes the robots' states (``make_job``) and hands the search to one
planning thread; a later tick takes the result in (``apply_job``). Meanwhile:

- robots keep the order of the plan they have (stop lines, PAUSE), so nothing unsafe follows from waiting;
- no robot starts a MOVE, turns or goes on (RESUME) until the new plan is in (PlanExec.execute ``starts``);
- a result made before a later change (a new leg, a cancel, a link loss: anything that marks the plan dirty) is
  dropped, and the search runs again from the robots' states of then.

``threaded=False`` (tests, the kinematic mock) runs the search at once in the tick but still uses its result
only in the next tick, so the tests see the same order of events as the node, without timing noise.
"""

from concurrent.futures import ThreadPoolExecutor


class PlanRunner:
    def __init__(self, manager, *, threaded=False):
        self.pm = manager
        self.threaded = threaded
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="plan") if threaded else None
        self._job = None          # job made in an earlier tick (inline: already searched)
        self._future = None
        self.dropped = 0          # results dropped because the plan changed while they were made

    @property
    def busy(self):
        return self._job is not None

    def tick(self, now) -> bool:
        """Take a finished result in, start a new search when due. True when the plan in use is current (no
        search in flight), so robots may start and go on."""
        pm = self.pm
        if self._job is not None:
            if self._future is not None and not self._future.done():
                return False
            job = self._future.result() if self._future is not None else self._job
            self._job = self._future = None
            if job.seq == pm._marks:
                pm.apply_job(job, now)
            else:
                self.dropped += 1         # made before a later change: plan again from now
        reason = pm.replan_due(now)
        if reason is None:
            return True
        job = pm.make_job(now, reason)
        self._job = job
        if self._pool is not None:
            self._future = self._pool.submit(pm.run_job, job)
        else:
            pm.run_job(job)
        return False

    def close(self):
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=True)
