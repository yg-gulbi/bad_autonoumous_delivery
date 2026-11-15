"""Common event-level benchmark using original video presentation timestamps."""
from __future__ import annotations

import csv
import gc
import importlib.metadata
import json
import math
import platform
import time
from pathlib import Path

import numpy as np

from part5_bed_exit.methods.bed_exit_core import (
    EventGate, JudgmentUnavailable, PolygonROI, make_method,
)
from part5_bed_exit.downloads import save_json, sha256


def video_frames(path):
    """PyAV PTS handles VFR too. Missing/non-monotonic PTS fails explicitly."""
    import av
    with av.open(str(path)) as container:
        stream=container.streams.video[0]
        first=None
        previous=-1.0
        for index,frame in enumerate(container.decode(stream)):
            if frame.pts is None or frame.time_base is None:
                raise ValueError("Missing video PTS; remux and re-annotate before benchmarking")
            pts=float(frame.pts*frame.time_base)
            if first is None:
                first=pts
            t=pts-first
            if t<=previous:
                raise ValueError("Video PTS must strictly increase")
            previous=t
            yield index,t,frame.to_ndarray(format="bgr24")


def _finite(value,name):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def validate_events(events,duration):
    previous_end=0.0
    seen=set()
    for e in sorted(events,key=lambda e:e["risk_start_s"]):
        if not e.get("event_id") or e["event_id"] in seen:
            raise ValueError("event_id must be unique and nonempty")
        seen.add(e["event_id"])
        start=_finite(e["risk_start_s"],"risk_start_s")
        boundary=_finite(e["boundary_cross_s"],"boundary_cross_s")
        end=_finite(e["end_s"],"end_s")
        if not 0<=start<=boundary<end<=duration+1e-6 or start<previous_end:
            raise ValueError("Require non-overlapping 0<=risk_start<=boundary_cross<end<=duration")
        previous_end=end


def validate_manifest(manifest,base_dir,split="test"):
    if manifest.get("schema_version")!=1:
        raise ValueError("Unsupported manifest schema_version")
    if manifest.get("policy")!="any_body":
        raise ValueError("This benchmark manifest defines any_body; do not mix torso-policy results")
    EventGate(**manifest.get("gate", {}))
    clips=manifest.get("clips",[])
    if not clips:
        raise ValueError("No clips in testbed manifest")
    ids=set()
    subjects,sessions={},{}
    for c in clips:
        cid=c.get("clip_id")
        if not cid or cid in ids:
            raise ValueError("clip_id must be unique and nonempty")
        ids.add(cid)
        if c.get("split") not in ("validation","test"):
            raise ValueError("split must be validation or test; no training in this project")
        for key,registry in [("subject_id",subjects),("session_id",sessions)]:
            value=c.get(key)
            if not value or value=="EDIT_ME":
                raise ValueError(f"Fill {key} using pseudonymous IDs")
            if value in registry and registry[value]!=c["split"]:
                raise ValueError(f"{key} leaked across validation/test: {value}")
            registry[value]=c["split"]
        if c["split"]!=split:
            continue
        for key in ("labels_reviewed","roi_reviewed"):
            if c.get(key) is not True:
                raise ValueError(f"{cid}: {key}=true required after human review")
        path=(Path(base_dir)/c["video"]).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        if c.get("video_sha256")!=sha256(path):
            raise ValueError(f"{cid}: video hash missing/mismatched; labels must refer to this exact file")
        duration=_finite(c["duration_s"],"duration_s")
        if duration<=0:
            raise ValueError("duration_s must be positive")
        bed,safe=PolygonROI(c["bed_roi"]),PolygonROI(c["safe_roi"])
        # Raster inclusion also catches safe polygon edges crossing a concave bed.
        if np.any(safe.mask((1000,1000)) & ~bed.mask((1000,1000))):
            raise ValueError("Safe ROI must be inside Bed ROI")
        if not c.get("roi_version"):
            raise ValueError("roi_version required")
        if "events" not in c:
            raise ValueError("Explicit events list required; [] means reviewed negative clip")
        validate_events(c["events"],duration)
        intervals=c.get("patient_present_intervals_s")
        if intervals is None:
            raise ValueError("Annotate patient-present intervals for tracking-lost denominator")
        previous=0.0
        for start,end in intervals:
            start,end=_finite(start,"presence start"),_finite(end,"presence end")
            if not 0<=start<end<=duration+1e-6 or start<previous:
                raise ValueError("Patient-present intervals must be ordered and non-overlapping")
            previous=end
    selected=[c for c in clips if c["split"]==split]
    if not selected:
        raise ValueError(f"No {split} clips")
    return selected


def _ratio(a,b):
    return float(a/b) if b else None


def _percentile(values,q):
    return float(np.percentile(values,q)) if len(values) else None


def event_metrics(events,alarms,duration):
    """One-to-one events; duplicates, early unrelated and late alerts are FP.

    Both source frame and available warning must belong to the SAME event
    window [risk_start, end), preventing queued stale frames matching a later
    event. All geometric exits are targets, including authorized exits.
    """
    validate_events(events,duration)
    events=sorted(events,key=lambda x:x["risk_start_s"])
    matched={}
    false=[]
    for alarm in sorted(alarms,key=lambda x:x["available_s"]):
        media=_finite(alarm["media_s"],"alarm media_s")
        available=_finite(alarm["available_s"],"alarm available_s")
        if not 0<=media<=available:
            raise ValueError("Alarm availability cannot precede its source frame")
        candidates=[e for e in events if e["event_id"] not in matched
                    and e["risk_start_s"]<=media<e["end_s"]
                    and e["risk_start_s"]<=available<e["end_s"]]
        if candidates:
            matched[candidates[0]["event_id"]]=alarm
        else:
            false.append(alarm)
    details=[]
    leads,delays=[],[]
    early=0
    for e in events:
        alarm=matched.get(e["event_id"])
        lead=None if alarm is None else float(e["boundary_cross_s"]-alarm["available_s"])
        delay=None if alarm is None else float(alarm["available_s"]-e["risk_start_s"])
        if lead is not None:
            leads.append(lead)
            delays.append(delay)
            early+=int(lead>0)
        details.append({"event_id":e["event_id"],"matched":alarm is not None,
                        "warning_s":None if alarm is None else alarm["available_s"],
                        "lead_s":lead,"delay_s":delay})
    tp,fp,fn=len(matched),len(false),len(events)-len(matched)
    safe_duration=duration-sum(e["end_s"]-e["risk_start_s"] for e in events)
    return {"tp":tp,"fp":fp,"fn":fn,"event_precision":_ratio(tp,tp+fp),
            "event_recall":_ratio(tp,tp+fn),"early_warning_recall":_ratio(early,len(events)),
            "false_alarms_per_hour":_ratio(fp*3600,duration),
            "false_alarms_per_10m":_ratio(fp*600,duration),
            "false_alarms_per_safe_hour":_ratio(fp*3600,safe_duration),
            "duration_s":duration,"safe_duration_s":safe_duration,"early_tp":early,
            "warning_lead_median_s":_percentile(leads,50),
            "warning_lead_p05_s":_percentile(leads,5),
            "detection_delay_p95_s":_percentile(delays,95),
            "lead_values_s":leads,"delay_values_s":delays,"event_details":details,
            "false_alarm_details":false}


def tracking_durations(rows,present_intervals,duration):
    """Integrate sample-and-hold validity over PTS durations, not frame counts."""
    lost=present=0.0
    for i,row in enumerate(rows):
        start=row["media_s"]
        end=rows[i+1]["media_s"] if i+1<len(rows) else duration
        for a,b in present_intervals:
            dt=max(0.0,min(end,b)-max(start,a))
            present+=dt
            if not row["valid"]:
                lost+=dt
    return lost,present


def _sync(method):
    if str(getattr(method,"device","cpu"))=="cpu":
        return
    import torch
    if torch.cuda.is_available():
        device=str(method.device)
        torch.cuda.synchronize("cuda:"+device if device.isdigit() else device)


def benchmark_clip(method,clip,base_dir,output_dir,warmup=3,gate_kwargs=None):
    path=(Path(base_dir)/clip["video"]).resolve()
    out=Path(output_dir)
    out.mkdir(parents=True,exist_ok=True)
    if warmup:
        first=next(video_frames(path))[2]
        for _ in range(warmup):
            try:
                method.judge(first)
            except JudgmentUnavailable:
                pass  # A warm-up frame need not contain observable joints.
        _sync(method)
    method.reset()
    gate=EventGate(**(gate_kwargs or {}))
    rows=[]
    alarms=[]
    finish_s=0.0
    iterator=iter(video_frames(path))
    start_wall=time.perf_counter()
    while True:
        step_start=time.perf_counter()
        try:
            index,t,frame=next(iterator)
        except StopIteration:
            break
        _sync(method)
        before=time.perf_counter()
        error=None
        try:
            raw=method.judge(frame)
            if type(raw) is not bool:
                raise TypeError('The method must return a Python bool')
            valid=method.last.valid
            reason=method.last.reason
        except Exception as exc:
            raw=None
            valid=False
            reason=str(exc) if isinstance(exc, JudgmentUnavailable) else 'method_exception'
            error=f'{type(exc).__name__}: {exc}'
        _sync(method)
        inference_end=time.perf_counter()
        result=gate.update(raw,valid)
        gate_end=time.perf_counter()
        judge_ms=(inference_end-before)*1000
        gate_ms=(gate_end-inference_end)*1000
        # Single FIFO consumer, no frame dropping. Model loads and warm-up are
        # excluded. Slower-than-source inference accumulates real-time backlog.
        step_s=gate_end-step_start
        finish_s=max(t,finish_s)+step_s
        if result.event:
            alarms.append({"media_s":t,"available_s":finish_s,"frame_index":index})
        rows.append({"frame_index":index,"media_s":t,"raw_exit":raw,
                     "state":result.state.value if valid else None,"event":result.event,
                     "valid":valid,"reason":reason,"error":error,
                     "score":method.last.score if valid else None,"judge_ms":judge_ms,"gate_ms":gate_ms,
                     "step_ms":step_s*1000,"fifo_available_s":finish_s})
    wall_s=time.perf_counter()-start_wall
    if not rows:
        raise ValueError("Empty video")
    duration=clip["duration_s"]
    if rows[-1]["media_s"]>=duration:
        raise ValueError("Annotation duration does not include final video frame")
    steps=np.diff([r["media_s"] for r in rows])
    last_interval=float(np.median(steps)) if len(steps) else duration
    if abs(duration-(rows[-1]["media_s"]+last_interval))>max(0.1,last_interval*2):
        raise ValueError("Annotation duration disagrees with decoded video PTS")
    measured=event_metrics(clip["events"],alarms,duration)
    signal_alarms=[{**a,"available_s":a["media_s"]} for a in alarms]
    signal=event_metrics(clip["events"],signal_alarms,duration)
    lost,present=tracking_durations(rows,clip["patient_present_intervals_s"],duration)
    summary={"clip_id":clip["clip_id"],"frames":len(rows),"wall_s":wall_s,
             "valid_frames":sum(r['valid'] for r in rows),
             "invalid_frames":sum(not r['valid'] for r in rows),
             "fps":len(rows)/wall_s,"source_fps":len(rows)/duration,
             "judge_latency_p95_ms":_percentile([r["judge_ms"] for r in rows],95),
             "step_latency_p95_ms":_percentile([r["step_ms"] for r in rows],95),
             "tracking_lost_s":lost,"patient_present_s":present,
             "tracking_lost_rate":_ratio(lost,present),
             "fifo_backlog_end_s":max(0.0,finish_s-duration),
             "events":measured,"signal_events":signal,"alarms":alarms,
             "conditions":clip.get("conditions",{}),
             "timing":"original PTS; availability simulated single-consumer FIFO, no frame drops"}
    with (out/"frames.csv").open("w",newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    save_json(out/"summary.json",summary)
    return summary,rows


def aggregate_summaries(summaries,rows_by_clip,metric_key="events"):
    events=[s[metric_key] for s in summaries]
    tp=sum(x["tp"] for x in events)
    fp=sum(x["fp"] for x in events)
    fn=sum(x["fn"] for x in events)
    early=sum(x["early_tp"] for x in events)
    duration=sum(x["duration_s"] for x in events)
    safe=sum(x["safe_duration_s"] for x in events)
    delays=[v for e in events for v in e["delay_values_s"]]
    leads=[v for e in events for v in e["lead_values_s"]]
    rows=[r for clip_rows in rows_by_clip for r in clip_rows]
    lost=sum(s["tracking_lost_s"] for s in summaries)
    present=sum(s["patient_present_s"] for s in summaries)
    return {"clips":len(summaries),"events":tp+fn,"tp":tp,"fp":fp,"fn":fn,
            "valid_frames":sum(s['valid_frames'] for s in summaries),
            "invalid_frames":sum(s['invalid_frames'] for s in summaries),
            "event_precision":_ratio(tp,tp+fp),"event_recall":_ratio(tp,tp+fn),
            "early_warning_recall":_ratio(early,tp+fn),
            "false_alarms_per_hour":_ratio(fp*3600,duration),
            "false_alarms_per_10m":_ratio(fp*600,duration),
            "false_alarms_per_safe_hour":_ratio(fp*3600,safe),
            "duration_s":duration,"safe_duration_s":safe,
            "warning_lead_median_s":_percentile(leads,50),"warning_lead_p05_s":_percentile(leads,5),
            "detection_delay_p95_s":_percentile(delays,95),
            "fps":len(rows)/sum(s["wall_s"] for s in summaries),
            "judge_latency_p95_ms":_percentile([r["judge_ms"] for r in rows],95),
            "tracking_lost_rate":_ratio(lost,present),"tracking_lost_s":lost,"patient_present_s":present}


def target_checks(metrics):
    """Notion proposals, not achieved claims. Undefined metrics stay unevaluated."""
    specs={"event_precision":(">=",0.90),"false_alarms_per_10m":("<=",0.25),
           "warning_lead_median_s":(">=",0.30),"detection_delay_p95_s":("<=",0.10),
           "fps":(">=",5.0),"judge_latency_p95_ms":("<=",200.0),
           "early_warning_recall":(">=",0.95)}
    results={}
    for key,(op,limit) in specs.items():
        value=metrics.get(key)
        results[key]={"measured":value,"operator":op,"proposal":limit,
                      "pass":None if value is None else value>=limit if op==">=" else value<=limit}
    return results


def environment_record():
    packages={}
    for p in ("numpy","ultralytics","mediapipe","av","torch","opencv-python"):
        try:
            packages[p]=importlib.metadata.version(p)
        except importlib.metadata.PackageNotFoundError:
            packages[p]=None
    import torch
    return {"python":platform.python_version(),"platform":platform.platform(),
            "processor":platform.processor(),"packages":packages,
            "cuda":torch.cuda.is_available(),
            "gpu":torch.cuda.get_device_name() if torch.cuda.is_available() else None}


def run_benchmark(manifest_path,model_dir,output_dir,methods=None,device="cpu",split="test",factory=None):
    manifest_path=Path(manifest_path).resolve()
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    clips=validate_manifest(manifest,manifest_path.parent,split)
    settings=manifest.get("method_settings",{})
    gate_kwargs=manifest.get("gate",{"k":3,"n":5,"rearm_safe_frames":5})
    if settings.get("policy","any_body")!="any_body":
        raise ValueError("Method policy must agree with manifest policy")
    methods=methods or ["detection","segmentation","yolo_pose","mediapipe_pose"]
    factory=factory or make_method
    output_dir=Path(output_dir)
    if (output_dir/"run.json").exists():
        raise FileExistsError("Choose a new run directory to preserve prior results")
    output_dir.mkdir(parents=True,exist_ok=True)
    from part5_bed_exit.methods.bed_exit_core import MODEL_FILES
    weight_hashes={n:sha256(Path(model_dir)/MODEL_FILES[n])
                   for n in methods if (Path(model_dir)/MODEL_FILES[n]).is_file()}
    save_json(output_dir/"run.json",{"status":"running","environment":environment_record(),
              "manifest_sha256":sha256(manifest_path),"manifest":manifest,
              "weight_sha256":weight_hashes,"device":device,"split":split,
              "note":"sequential offline replay; real-time availability is FIFO simulation"})
    comparison=[]
    try:
        for name in methods:
            summaries,all_rows=[],[]
            for clip in clips:
                method=factory(name,PolygonROI(clip["bed_roi"]),PolygonROI(clip["safe_roi"]),
                               model_dir,device=device,initial_bbox=clip.get("initial_patient_bbox"),**settings)
                try:
                    summary,rows=benchmark_clip(method,clip,manifest_path.parent,
                                               output_dir/name/clip["clip_id"],gate_kwargs=gate_kwargs)
                    summaries.append(summary)
                    all_rows.append(rows)
                finally:
                    method.close()
                    del method
                    gc.collect()
            aggregate=aggregate_summaries(summaries,all_rows)
            aggregate["method"]=name
            aggregate["signal_only"]=aggregate_summaries(summaries,all_rows,"signal_events")
            aggregate["proposal_checks"]=target_checks(aggregate)
            grouped={}
            for key in ("case_id","blanket","lighting","bed_motion","source_domain"):
                for value in sorted({str(s["conditions"].get(key,"unspecified")) for s in summaries}):
                    indices=[i for i,s in enumerate(summaries) if str(s["conditions"].get(key,"unspecified"))==value]
                    grouped[f"{key}={value}"]=aggregate_summaries([summaries[i] for i in indices],[all_rows[i] for i in indices])
            save_json(output_dir/name/"conditions.json",grouped)
            save_json(output_dir/name/"aggregate.json",aggregate)
            comparison.append(aggregate)
        save_json(output_dir/"comparison.json",comparison)
        flat=[{k:v for k,v in m.items() if not isinstance(v,(dict,list))} for m in comparison]
        with (output_dir/"comparison.csv").open("w",newline="",encoding="utf-8") as f:
            writer=csv.DictWriter(f,fieldnames=list(flat[0]))
            writer.writeheader()
            writer.writerows(flat)
    except Exception as e:
        record=json.loads((output_dir/"run.json").read_text())
        record.update(status="failed",error_type=type(e).__name__,error=str(e))
        save_json(output_dir/"run.json",record)
        raise
    record=json.loads((output_dir/"run.json").read_text())
    record["status"]="complete"
    save_json(output_dir/"run.json",record)
    return comparison
