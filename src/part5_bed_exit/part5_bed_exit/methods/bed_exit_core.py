"""Pretrained RGB bed-exit strategies. OpenCV BGR uint8 in; Python bool out.

No training, ROS publication, or medical authorization is performed here.
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from part5_bed_exit.bed_exit_method import BedExitMethod as FrameMethod


class JudgmentUnavailable(RuntimeError):
    """No supported SAFE/EXIT observation; reset votes and publish nothing."""


@dataclass
class Diagnostics:
    valid: bool = False
    reason: str = "not_observed"
    score: float | None = None  # geometry score, NOT a calibrated probability
    bbox: tuple[float, float, float, float] | None = None
    points: list = field(default_factory=list)


@dataclass
class Candidate:
    bbox: np.ndarray
    confidence: float
    mask: np.ndarray | None = None
    points: np.ndarray | None = None  # pixel x, y, confidence


class PolygonROI:
    """Normalized, simple polygon; boundary pixels count as inside."""

    def __init__(self, vertices):
        p = np.asarray(vertices, dtype=np.float32)
        if (p.ndim != 2 or p.shape[1] != 2 or len(p) < 3
                or not np.isfinite(p).all() or np.any((p < 0) | (p > 1))):
            raise ValueError("ROI requires >=3 normalized [x,y] vertices in [0,1]")
        if len(np.unique(p, axis=0)) != len(p) or abs(cv2.contourArea(p)) < 1e-6:
            raise ValueError("ROI has duplicate vertices or zero area")
        def orient(a, b, c):
            return float(np.cross(b-a, c-a))
        def intersects(a, b, c, d):
            o = [orient(a,b,c), orient(a,b,d), orient(c,d,a), orient(c,d,b)]
            if o[0]*o[1] < 0 and o[2]*o[3] < 0:
                return True
            for v, q, u, w in [(o[0],c,a,b),(o[1],d,a,b),
                                (o[2],a,c,d),(o[3],b,c,d)]:
                if abs(v) < 1e-8 and np.all(q >= np.minimum(u,w)) and np.all(q <= np.maximum(u,w)):
                    return True
            return False
        for i in range(len(p)):
            for j in range(i+1,len(p)):
                if j == i+1 or (i == 0 and j == len(p)-1):
                    continue
                if intersects(p[i], p[(i+1)%len(p)], p[j], p[(j+1)%len(p)]):
                    raise ValueError("ROI must not self-intersect")
        self.vertices = p
        self._masks = {}

    def pixels(self, shape):
        h,w = shape[:2]
        return self.vertices * np.array([w-1,h-1], np.float32)

    def mask(self, shape):
        key = tuple(shape[:2])
        if key not in self._masks:
            m = np.zeros(key, np.uint8)
            cv2.fillPoly(m, [np.rint(self.pixels(shape)).astype(np.int32)], 1)
            self._masks[key] = m.astype(bool)
        return self._masks[key]

    def contains(self, point, shape, tolerance_px=0.0):
        return cv2.pointPolygonTest(self.pixels(shape), tuple(map(float,point)), True) >= -tolerance_px

    def box_outside(self, box, shape):
        h,w = shape[:2]
        x1,y1 = np.floor(box[:2]).astype(int)
        x2,y2 = np.ceil(box[2:]).astype(int)
        x1,x2 = np.clip([x1,x2],0,w)
        y1,y2 = np.clip([y1,y2],0,h)
        if x2 <= x1 or y2 <= y1:
            raise ValueError("empty bounding box")
        return float(1.0-self.mask(shape)[y1:y2,x1:x2].mean())


def box_iou(a,b):
    lo,hi = np.maximum(a[:2],b[:2]), np.minimum(a[2:],b[2:])
    inter = float(np.prod(np.maximum(hi-lo,0)))
    area = float(np.prod(a[2:]-a[:2])+np.prod(b[2:]-b[:2]))
    return inter/max(area-inter,1e-9)


class PatientSelector:
    """Conservative spatial association, not a biometric/re-ID patient tracker.

    Acquire only a person overlapping the bed; then require overlap with the
    previous patient box. Ambiguous associations remain invalid until resolved.
    """
    def __init__(self, roi, initial_bbox=None, min_iou=0.15):
        self.roi = roi
        self.initial_bbox = initial_bbox  # optional normalized patient box
        if initial_bbox is not None:
            b = np.asarray(initial_bbox, float)
            if b.shape != (4,) or not np.isfinite(b).all() or np.any((b<0)|(b>1)) or np.any(b[2:]<=b[:2]):
                raise ValueError("initial_bbox must be normalized xyxy")
        self.min_iou = min_iou
        self.reset()

    def reset(self):
        self.previous = None

    def select(self, candidates, shape):
        candidates = [c for c in candidates if np.isfinite(c.bbox).all()
                      and np.all(c.bbox[2:]>c.bbox[:2])]
        if not candidates:
            return None, "no_person"
        reference = self.previous
        if reference is None and self.initial_bbox is not None:
            h,w = shape[:2]
            reference = np.asarray(self.initial_bbox)*[w,h,w,h]
        scored = []
        for c in candidates:
            if reference is None:
                overlap = 1-self.roi.box_outside(c.bbox,shape)
                if overlap >= 0.5:
                    scored.append((overlap,c))
            else:
                overlap = box_iou(reference,c.bbox)
                if overlap >= self.min_iou:
                    scored.append((overlap,c))
        scored.sort(key=lambda x:x[0],reverse=True)
        if not scored:
            return None, "patient_unassociated"
        if len(scored)>1 and scored[1][0]>=scored[0][0]-0.15:
            return None, "ambiguous_people"
        chosen = scored[0][1]
        self.previous = chosen.bbox.copy()
        return chosen, "ok"


class BedExitMethod(FrameMethod):
    """Strategy base: every concrete method inherits the same judge(frame)."""
    def __init__(self, roi, initial_bbox=None):
        self.roi = roi
        self.selector = PatientSelector(roi, initial_bbox)
        self.reset()

    def reset(self):
        self.selector.reset()
        self.last = Diagnostics()

    @abstractmethod
    def candidates(self, frame) -> list[Candidate]:
        """Backend-specific pretrained inference, no risk policy here."""

    @abstractmethod
    def decide(self, candidate, shape) -> tuple[bool | None, float | None, str]:
        """Return None if the observation cannot support a decision."""

    def judge(self, frame) -> bool:
        if (not isinstance(frame,np.ndarray) or frame.ndim!=3 or frame.shape[2]!=3
                or frame.dtype!=np.uint8 or frame.size==0):
            self.last = Diagnostics(reason="invalid_frame")
            raise JudgmentUnavailable(self.last.reason)
        self.last = Diagnostics(reason="inference_pending")
        patient,reason = self.selector.select(self.candidates(frame),frame.shape)
        if patient is None:
            self.last = Diagnostics(reason=reason)
            raise JudgmentUnavailable(reason)
        decision,score,reason = self.decide(patient,frame.shape)
        self.last = Diagnostics(decision is not None, reason, score,
                                tuple(map(float,patient.bbox)),
                                [] if patient.points is None else patient.points.tolist())
        if decision is None:
            raise JudgmentUnavailable(reason)
        return bool(decision)

    def close(self):
        pass


class YoloMethod(BedExitMethod):
    def __init__(self, roi, weights, task, device="cpu",
                 confidence=0.25, imgsz=640, initial_bbox=None, model=None):
        super().__init__(roi,initial_bbox)
        if model is None:
            if not Path(weights).is_file():
                raise FileNotFoundError(f"Run the model-download cell first: {weights}")
            from ultralytics import YOLO
            model = YOLO(str(weights),task=task)
        self.model = model
        self.device, self.confidence, self.imgsz = device,confidence,imgsz

    def candidates(self, frame):
        result = self.model.predict(source=frame,classes=[0],conf=self.confidence,
                                    imgsz=self.imgsz,device=self.device,
                                    retina_masks=True,verbose=False)[0]
        if result.boxes is None:
            return []
        boxes = result.boxes.xyxy.cpu().numpy()
        confs = result.boxes.conf.cpu().numpy()
        masks = None if result.masks is None else result.masks.data.cpu().numpy()
        points = None if result.keypoints is None else result.keypoints.data.cpu().numpy()
        out=[]
        for i,(box,conf) in enumerate(zip(boxes,confs)):
            mask = None if masks is None else masks[i]
            if mask is not None and mask.shape!=frame.shape[:2]:
                # retina_masks normally provides original-resolution masks.
                raise ValueError("Unexpected mask coordinates; require original-size retina_masks")
            out.append(Candidate(box,float(conf),mask,None if points is None else points[i]))
        return out


class DetectionROI(YoloMethod):
    def __init__(self, roi, weights, outside_fraction=0.05, **kwargs):
        super().__init__(roi,weights,"detect",**kwargs)
        if not 0<=outside_fraction<=1:
            raise ValueError("outside_fraction must be in [0,1]")
        self.outside_fraction=outside_fraction

    def decide(self,candidate,shape):
        score=self.roi.box_outside(candidate.bbox,shape)
        return score>self.outside_fraction,score,"box_outside_fraction"


class SegmentationROI(YoloMethod):
    def __init__(self, roi, weights, outside_fraction=0.05, **kwargs):
        super().__init__(roi,weights,"segment",**kwargs)
        if not 0<=outside_fraction<=1:
            raise ValueError("outside_fraction must be in [0,1]")
        self.outside_fraction=outside_fraction

    def decide(self,candidate,shape):
        if candidate.mask is None:
            return None,None,"missing_mask"
        m=candidate.mask>0.5
        area=int(m.sum())
        if not area:
            return None,None,"empty_mask"
        score=float(np.count_nonzero(m & ~self.roi.mask(shape))/area)
        return score>self.outside_fraction,score,"mask_outside_fraction"


# Shared anatomical landmarks: nose, shoulders, elbows, wrists, hips, knees,
# ankles. MediaPipe-only fingers/toes/ears are excluded for a fair comparison.
COCO_COMMON=(0,5,6,7,8,9,10,11,12,13,14,15,16)
MP_COMMON=(0,11,12,13,14,15,16,23,24,25,26,27,28)


def pose_decision(points, roi, shape, common, torso, point_conf=0.5,
                  tolerance_px=3.0, policy="any_body"):
    if points is None or len(points)<=max(common):
        return None,None,"missing_keypoints"
    p=np.asarray(points)
    usable=np.isfinite(p).all(axis=1) & (p[:,2]>=point_conf)
    core=[i for i in torso if usable[i]]
    if len(core)<2:
        return None,None,"insufficient_torso_keypoints"
    if policy=="torso":
        selected=p[core,:2].mean(axis=0)[None,:]
    elif policy=="any_body":
        selected=p[[i for i in common if usable[i]],:2]
    else:
        raise ValueError("policy must be any_body or torso")
    outside=[not roi.contains(x,shape,tolerance_px) for x in selected]
    score=float(np.mean(outside))
    # Positive evidence can be used with partially visible joints. A negative
    # any-body decision requires ALL common landmarks, preventing occlusion
    # from being reported as a confident SAFE observation.
    if any(outside):
        return True,score,"visible_keypoint_outside"
    if policy=="any_body" and not all(usable[i] for i in common):
        return None,score,"incomplete_body_coverage"
    if policy=="torso" and len(core)!=len(torso):
        return None,score,"incomplete_torso_coverage"
    return False,score,"keypoints_inside"


class YoloPoseROI(YoloMethod):
    def __init__(self,roi,weights,policy="any_body",point_conf=0.5,
                 tolerance_px=3.0,**kwargs):
        super().__init__(roi,weights,"pose",**kwargs)
        self.policy,self.point_conf,self.tolerance_px=policy,point_conf,tolerance_px

    def decide(self,candidate,shape):
        return pose_decision(candidate.points,self.roi,shape,COCO_COMMON,
                             (5,6,11,12),self.point_conf,self.tolerance_px,self.policy)


class MediaPipePoseROI(BedExitMethod):
    def __init__(self,roi,weights,policy="any_body",point_conf=0.5,
                 tolerance_px=3.0,initial_bbox=None):
        super().__init__(roi,initial_bbox)
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
        if not Path(weights).is_file():
            raise FileNotFoundError(f"Run model-download cell first: {weights}")
        self.mp=mp
        self.model=vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_path=str(weights)),
            running_mode=vision.RunningMode.IMAGE,num_poses=4,
            min_pose_detection_confidence=0.5,min_pose_presence_confidence=0.5))
        self.policy,self.point_conf,self.tolerance_px=policy,point_conf,tolerance_px

    def candidates(self,frame):
        rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
        result=self.model.detect(self.mp.Image(image_format=self.mp.ImageFormat.SRGB,data=rgb))
        h,w=frame.shape[:2]
        out=[]
        for landmarks in result.pose_landmarks:
            points=np.array([[p.x*w,p.y*h,min(p.visibility,p.presence)] for p in landmarks],np.float32)
            good=points[list(MP_COMMON)]
            good=good[np.isfinite(good).all(axis=1) & (good[:,2]>=self.point_conf)]
            if len(good)<2:
                continue
            box=np.r_[good[:,:2].min(axis=0),good[:,:2].max(axis=0)]
            out.append(Candidate(box,float(good[:,2].mean()),points=points))
        return out

    def decide(self,candidate,shape):
        return pose_decision(candidate.points,self.roi,shape,MP_COMMON,
                             (11,12,23,24),self.point_conf,self.tolerance_px,self.policy)

    def close(self):
        self.model.close()


MODEL_FILES={"detection":"yolo11n.pt","segmentation":"yolo11n-seg.pt",
             "yolo_pose":"yolo11n-pose.pt","mediapipe_pose":"pose_landmarker_full.task"}


def make_method(name,roi,model_dir,device="cpu",initial_bbox=None,
                outside_fraction=0.05,policy="any_body",point_conf=0.5,tolerance_px=3.0):
    weights=Path(model_dir)/MODEL_FILES[name]
    base=dict(roi=roi,weights=weights,initial_bbox=initial_bbox)
    if name=="mediapipe_pose":
        return MediaPipePoseROI(**base,policy=policy,point_conf=point_conf,tolerance_px=tolerance_px)
    if name=="yolo_pose":
        return YoloPoseROI(**base,device=device,policy=policy,point_conf=point_conf,tolerance_px=tolerance_px)
    cls={"detection":DetectionROI,"segmentation":SegmentationROI}[name]
    return cls(**base,device=device,outside_fraction=outside_fraction)
