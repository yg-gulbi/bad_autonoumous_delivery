"""Motion and progress observations on a single active leg."""
from collections import deque
from math import dist


class Stall:
    def __init__(self, policy):
        self.policy = policy
        self.reset()

    def reset(self):
        self.samples = deque()
        self.last_progress = None
        self.seconds = 0.0
        self.short = self.long = False

    def tick(self, now, pose, percent):
        if pose is None:
            self.reset()
            return None
        if self.last_progress is None:
            self.last_progress = now
        # Keep the sample at the leading edge of the configured observation window.
        while len(self.samples) > 1 and self.samples[1][0] <= now - self.policy.window_s:
            self.samples.popleft()
        moved = any(dist(pose, xy) >= self.policy.move_m or
                    (percent is not None and previous is not None and percent > previous)
                    for _, xy, previous in self.samples)
        self.samples.append((now, pose, percent))
        if moved:
            was_long = self.long
            self.reset()
            self.samples.append((now, pose, percent))
            self.last_progress = now
            return "UNBLOCKED" if was_long else None
        elapsed = now - self.last_progress
        self.seconds = elapsed if elapsed >= self.policy.window_s else 0.0
        if not self.long and self.seconds >= self.policy.long_s:
            self.short = self.long = True
            return "STALL_LONG"
        if not self.short and self.seconds >= self.policy.short_s:
            self.short = True
            return "STALL_SHORT"
        return None
