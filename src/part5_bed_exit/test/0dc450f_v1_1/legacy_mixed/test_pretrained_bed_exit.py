"""``bed_exit_core.py``와 오프라인 평가 계층의 상세 행위 테스트.

ROI 기하, 환자 선택, Detection·Segmentation·Pose 판정 차이, 판단 불가 계약,
이벤트 게이트·지표·매니페스트 검증과 외부 모델 어댑터를 폭넓게 확인한다.
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from part5_bed_exit.methods.bed_exit_core import (BedExitMethod,Candidate,COCO_COMMON,
                          COMMON_LANDMARKS,MP_COMMON,
                          DetectionROI,PatientSelector,PolygonROI,
                          SegmentationROI,YoloPoseROI,pose_decision,JudgmentUnavailable)
from research.evaluation.monitoring import (
    BedExitMonitor, ConsecutiveExitAnalysis, EventGate, State,
)
from research.dataset_download_label.downloads import download_file,save_json,sha256
from research.evaluation.evaluation import (benchmark_clip,event_metrics,run_benchmark,target_checks,
                        tracking_durations,validate_manifest,video_frames)


@pytest.fixture
def roi():
    return PolygonROI([[.2,.2],[.8,.2],[.8,.8],[.2,.8]])


def test_geometry_uses_polygon_not_its_bounding_rectangle():
    roi=PolygonROI([[0,0],[1,0],[0,1]])
    assert roi.contains((10,10),(100,100))
    assert not roi.contains((90,90),(100,100))
    assert roi.box_outside([80,80,95,95],(100,100))==1


@pytest.mark.parametrize("vertices",[
    [[0,0],[1,1],[0,1],[1,0]],[[0,0],[0,0],[1,1]],
    [[0,0],[1.1,0],[0,1]],[[0,0],[float("nan"),1],[1,0]],
])
def test_bad_roi_rejected(vertices):
    with pytest.raises(ValueError): PolygonROI(vertices)


def test_three_of_five_and_exactly_one_event_until_safe_recovery():
    gate=EventGate()
    outputs=[gate.update(x) for x in [True,False,True,False,True]+[True]*50]
    assert [r.event for r in outputs[:5]]==[False,False,False,False,True]
    assert sum(r.event for r in outputs)==1
    for _ in range(4): gate.update(False)
    assert gate.update(False).state==State.SAFE
    assert not gate.update(True).event
    assert not gate.update(True).event
    assert gate.update(True).event  # Complete rolling window already available.


def test_three_consecutive_analysis_is_separate_from_runtime_vote():
    analysis=ConsecutiveExitAnalysis()
    state=EventGate()
    sequence=[True,True,False,True,True]
    assert [analysis.update(value) for value in sequence]==[False]*5
    assert [state.update(value).state for value in sequence][-1]==State.EXIT
    assert analysis.update(True) is True
    assert analysis.update(None,valid=False) is None
    assert analysis.update(True) is False


def test_invalid_frames_cannot_rearm_or_bridge_separated_risk_votes():
    gate=EventGate()
    gate.update(True); gate.update(True)
    gate.update(False,valid=False)
    assert not gate.update(True).event
    assert not gate.update(True).event
    assert not gate.update(True).event
    assert not gate.update(True).event
    assert gate.update(True).event
    for _ in range(20):
        result=gate.update(False,valid=False)
        assert not result.event and not result.valid
    assert not gate.update(True).event


def test_patient_not_replaced_by_person_elsewhere(roi):
    selector=PatientSelector(roi)
    patient=Candidate(np.array([30,30,60,70]),.9)
    caregiver=Candidate(np.array([85,10,99,90]),.99)
    assert selector.select([caregiver,patient],(100,100))[0] is patient
    assert selector.select([caregiver],(100,100))[0] is None


def test_ambiguous_people_do_not_create_false_patient_identity(roi):
    s=PatientSelector(roi)
    people=[Candidate(np.array([25,25,45,70]),.99),Candidate(np.array([50,25,75,70]),.7)]
    assert s.select(people,(100,100))[1]=="ambiguous_people"


def test_detection_mask_and_pose_are_distinct_geometry_proxies(roi):
    candidate=Candidate(np.array([10,30,60,70]),.9)
    detection=DetectionROI(roi,"unused.pt",model=object())
    assert detection.decide(candidate,(100,100))[0]
    mask=np.zeros((100,100)); mask[30:70,30:60]=1
    candidate.mask=mask
    seg=SegmentationROI(roi,"unused.pt",model=object())
    assert seg.decide(candidate,(100,100))[0] is False
    candidate.mask[:]=0
    assert seg.decide(candidate,(100,100))[0] is None


def test_any_body_policy_catches_arm_and_does_not_hide_missing_limbs(roi):
    points=np.tile([50,50,.99],(17,1))
    args=(roi,(100,100),COCO_COMMON,(5,6,11,12))
    assert pose_decision(points,*args)[0] is False
    points[9,:2]=[95,50]
    assert pose_decision(points,*args)[0] is True
    points[9,2]=.1
    assert pose_decision(points,*args)[0] is None
    assert pose_decision(points,*args,policy="torso")[0] is False


def test_common_pose_landmark_indices_stay_name_aligned():
    assert len(COMMON_LANDMARKS)==len(COCO_COMMON)==len(MP_COMMON)==13
    assert [name for name,_,_ in COMMON_LANDMARKS]==[
        "nose","left_shoulder","right_shoulder","left_elbow","right_elbow",
        "left_wrist","right_wrist","left_hip","right_hip","left_knee",
        "right_knee","left_ankle","right_ankle",
    ]
    assert tuple(coco for _,coco,_ in COMMON_LANDMARKS)==COCO_COMMON
    assert tuple(mp for _,_,mp in COMMON_LANDMARKS)==MP_COMMON


class PixelMethod(BedExitMethod):
    def candidates(self,frame):
        return [Candidate(np.array([5.,5.,25.,25.]),.9,
                          points=np.array([[float(frame.mean()),0,1]]))]

    def decide(self,candidate,shape):
        return bool(candidate.points[0,0]>100),float(candidate.points[0,0]),"test_pixel"


def test_bool_contract_raises_on_input_failure_instead_of_holding_a_decision():
    roi=PolygonROI([[0,0],[1,0],[1,1],[0,1]])
    method=PixelMethod(roi)
    assert method.judge(np.full((32,32,3),255,np.uint8)) is True
    with pytest.raises(JudgmentUnavailable):
        method.judge(None)
    assert not method.last.valid
    method.reset()
    assert method.judge(np.zeros((32,32,3),np.uint8)) is False


def test_composed_monitor_keeps_bool_api_and_separate_event_pulse():
    roi=PolygonROI([[0,0],[1,0],[1,1],[0,1]])
    monitor=BedExitMonitor(PixelMethod(roi))
    frame=np.full((32,32,3),255,np.uint8)
    assert [monitor.judge(frame) for _ in range(5)]==[False,False,False,False,True]
    assert monitor.event
    assert monitor.judge(frame) is True and not monitor.event
    with pytest.raises(JudgmentUnavailable):
        monitor.judge(None)
    assert not monitor.valid


def event(eid,start,boundary,end):
    return {"event_id":eid,"risk_start_s":start,"boundary_cross_s":boundary,"end_s":end}


def test_event_metrics_one_to_one_duplicates_and_misses():
    events=[event("e1",2,3,4),event("e2",6,7,8),event("e3",9,9.2,10)]
    alarms=[{"media_s":t,"available_s":t} for t in [1,2.5,2.8,7.5]]
    m=event_metrics(events,alarms,12)
    assert (m["tp"],m["fp"],m["fn"])==(2,2,1)
    assert m["event_precision"]==.5
    assert m["event_recall"]==pytest.approx(2/3)
    assert m["early_warning_recall"]==pytest.approx(1/3)
    assert m["warning_lead_median_s"]==0
    assert m["false_alarms_per_10m"]==100


def test_queued_old_frame_does_not_match_an_unrelated_new_event():
    m=event_metrics([event("e1",1,2,3),event("e2",5,6,7)],
                    [{"media_s":1.5,"available_s":5.5}],8)
    assert (m["tp"],m["fp"],m["fn"])==(0,1,2)


def test_boundary_warning_is_not_early_and_undefined_metrics_are_null():
    m=event_metrics([event("e",1,2,3)],[{"media_s":2,"available_s":2}],4)
    assert m["early_warning_recall"]==0
    empty=event_metrics([],[],10)
    assert empty["event_recall"] is None and empty["event_precision"] is None
    assert target_checks(empty)["event_precision"]["pass"] is None


def test_tracking_loss_is_time_weighted_for_variable_frame_rate():
    rows=[{"media_s":0.,"valid":True},{"media_s":.1,"valid":False},
          {"media_s":.9,"valid":True}]
    lost,present=tracking_durations(rows,[[0,1]],1)
    assert lost==pytest.approx(.8)
    assert present==1


def test_manifest_rejects_subject_leakage_before_inference(tmp_path):
    m={"schema_version":1,"policy":"any_body","clips":[
        {"clip_id":"a","subject_id":"p1","session_id":"s1","split":"validation"},
        {"clip_id":"b","subject_id":"p1","session_id":"s2","split":"test"}]}
    with pytest.raises(ValueError,match="leaked"):
        validate_manifest(m,tmp_path)


def test_manifest_cannot_treat_unreviewed_template_as_ground_truth(tmp_path):
    m={"schema_version":1,"policy":"any_body","clips":[
        {"clip_id":"a","subject_id":"p1","session_id":"s1","split":"test",
         "labels_reviewed":False}]}
    with pytest.raises(ValueError,match="labels_reviewed"):
        validate_manifest(m,tmp_path)


def test_end_to_end_original_pts_to_episode_report(tmp_path):
    pytest.importorskip('av', reason='Install Part 5 requirements for the optional video benchmark')
    video=tmp_path/"clip.mp4"
    writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*"mp4v"),10,(32,32))
    assert writer.isOpened()
    for i in range(30):
        writer.write(np.full((32,32,3),255 if 5<=i<10 or 20<=i<25 else 0,np.uint8))
    writer.release()
    roi=[[0,0],[1,0],[1,1],[0,1]]
    clip={"clip_id":"logic_only","subject_id":"p1","session_id":"s1","split":"test",
          "video":"clip.mp4","video_sha256":sha256(video),"duration_s":3.0,
          "labels_reviewed":True,"roi_reviewed":True,"roi_version":"logic_v1",
          "roi":roi,"patient_present_intervals_s":[[0,3]],
          "events":[event("a",.5,.9,1.1),event("b",2.,2.4,2.6)],
          "conditions":{"case_id":"synthetic_logic_only"}}
    manifest=tmp_path/"testbed.json"
    save_json(manifest,{"schema_version":1,"policy":"any_body","clips":[clip]})
    factory=lambda name,roi,models,**kwargs:PixelMethod(roi)
    result=run_benchmark(manifest,tmp_path,tmp_path/"results",methods=["detection"],factory=factory)
    assert result[0]["tp"]==2 and result[0]["fp"]==0 and result[0]["fn"]==0
    assert result[0]["signal_only"]["early_warning_recall"]==1
    assert json.loads((tmp_path/"results/run.json").read_text())["status"]=="complete"
    assert (tmp_path/"results/detection/logic_only/frames.csv").is_file()


EXTERNAL_BENCHMARK_ADAPTERS = (
    "vitpose",
    "aotgan_vitpose",
    "mae_vitpose",
    "yolo_classification",
    "pose_lstm",
    "pose_tcn",
    "pose_stgcn",
    "fastsam",
    "sam2",
)


@pytest.mark.parametrize("adapter_name", EXTERNAL_BENCHMARK_ADAPTERS)
def test_each_external_model_can_replace_the_benchmark_factory(tmp_path, adapter_name):
    """Run one adapter at a time without registering it as a built-in model.

    PixelMethod is a deterministic contract double.  Real adapters replace it
    with their own backend while preserving judge/reset/close/last behavior.
    This test verifies benchmark plumbing, not model accuracy.
    """
    pytest.importorskip('av', reason='Install Part 5 requirements for the optional video benchmark')
    video=tmp_path/"clip.mp4"
    writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*"mp4v"),10,(32,32))
    assert writer.isOpened()
    for i in range(10):
        writer.write(np.full((32,32,3),255 if 2<=i<7 else 0,np.uint8))
    writer.release()
    clip={"clip_id":"factory_contract","subject_id":"p1","session_id":"s1",
          "split":"test","video":"clip.mp4","video_sha256":sha256(video),
          "duration_s":1.0,"labels_reviewed":True,"roi_reviewed":True,
          "roi_version":"logic_v1","roi":[[0,0],[1,0],[1,1],[0,1]],
          "patient_present_intervals_s":[[0,1]],
          "events":[event("exit",.2,.4,.8)],
          "conditions":{"case_id":"synthetic_factory_contract"}}
    manifest=tmp_path/"testbed.json"
    save_json(manifest,{"schema_version":1,"policy":"any_body","clips":[clip]})
    calls=[]

    def factory(name,roi,models,**kwargs):
        calls.append(name)
        assert name == adapter_name
        return PixelMethod(roi)

    output=tmp_path/"results"
    result=run_benchmark(manifest,tmp_path,output,methods=[adapter_name],factory=factory)

    assert calls == [adapter_name]
    assert result[0]["method"] == adapter_name
    assert result[0]["tp"] == 1
    assert json.loads((output/"run.json").read_text())["status"] == "complete"
    assert (output/adapter_name/"aggregate.json").is_file()
    assert (output/adapter_name/"factory_contract"/"frames.csv").is_file()
    assert (output/"comparison.csv").is_file()


def test_html_download_is_rejected_and_partial_file_removed(tmp_path,monkeypatch):
    class Response:
        headers={"Content-Type":"text/html"}
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def raise_for_status(self): pass
    import research.dataset_download_label.downloads as downloads
    monkeypatch.setattr(downloads.requests,"get",lambda *args,**kwargs:Response())
    with pytest.raises(RuntimeError,match="HTML"):
        download_file("https://example.com/data.zip",tmp_path/"data.zip")
    assert not (tmp_path/"data.zip").exists()
    assert not (tmp_path/"data.zip.part").exists()
