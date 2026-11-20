"""ROS 노드의 네 가지 내장 사전학습 메서드를 구현하는 실제 운영 코어 파일.

공통 입력은 OpenCV의 BGR ``uint8`` 프레임이고 출력은 프레임별 Python ``bool``이다.
이 파일은 다각형 ROI, 환자 후보 선택, Detection, Segmentation, YOLO Pose,
MediaPipe Pose 판정을 포함한다. 학습, ROS 발행, 5프레임 다수결은 수행하지 않는다.
판단 근거가 부족하면 안전으로 간주하지 않고 ``JudgmentUnavailable``을 발생시킨다.

System Flowchart v5 대응:
``BedExitMethod.judge()``가 사람 인식 → 단일 환자 선정 → 환자별 ROI 판정을 연결한다.
운영 Segmentation에서는 ``YoloMethod.candidates()``가 사람 후보를 만들고,
``PatientSelector.select()``가 환자를 고르며, ``SegmentationROI.decide()``가
마스크의 Bed ROI 밖 픽셀 비율을 계산한다.
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from part5_bed_exit.bed_exit_method import BedExitMethod as FrameMethod


# 모든 YOLO Detection·Segmentation·Pose가 공통으로 사용하는 사람 검출 최소 신뢰도다.
YOLO_CONFIDENCE = 0.5


# 판정 불가를 False로 바꾸지 않고 호출자에게 전달하기 위한 운영 예외 타입이다.
class JudgmentUnavailable(RuntimeError):
    """정상·이탈 어느 쪽도 근거가 부족해 해당 프레임을 판정할 수 없다는 예외."""


# 프리뷰와 오프라인 분석에서 최근 판정의 근거를 확인하기 위한 공통 기록 구조다.
@dataclass
class Diagnostics:
    """가장 최근 프레임의 판정 가능 여부와 근거를 보관하는 진단 데이터."""

    valid: bool = False
    reason: str = "not_observed"
    score: float | None = None  # geometry score, NOT a calibrated probability
    bbox: tuple[float, float, float, float] | None = None
    points: list = field(default_factory=list)
    mask: np.ndarray | None = None


# 서로 다른 모델의 출력을 BBox·마스크·관절이라는 동일한 사람 후보 형식으로 맞춘다.
@dataclass
class Candidate:
    """모델이 검출한 사람 한 명의 BBox·신뢰도·마스크·관절 데이터."""

    bbox: np.ndarray
    confidence: float
    mask: np.ndarray | None = None
    points: np.ndarray | None = None  # pixel x, y, confidence


class PolygonROI:
    """경계까지 내부로 간주하는 정규화 좌표 기반 단순 다각형 ROI."""

    def __init__(self, vertices):
        """정규화 꼭짓점을 검증하고 해상도별 마스크 캐시를 준비한다."""
        p = np.asarray(vertices, dtype=np.float32)
        # 꼭짓점은 최소 세 개이며 모든 좌표가 유한한 [0, 1] 범위여야 한다.
        if (p.ndim != 2 or p.shape[1] != 2 or len(p) < 3
                or not np.isfinite(p).all() or np.any((p < 0) | (p > 1))):
            raise ValueError("ROI requires >=3 normalized [x,y] vertices in [0,1]")

        # 같은 꼭짓점의 중복과 면적이 없는 선 모양 ROI를 별도로 거부한다.
        if len(np.unique(p, axis=0)) != len(p) or abs(cv2.contourArea(p)) < 1e-6:
            raise ValueError("ROI has duplicate vertices or zero area")

        def orient(a, b, c):
            """세 점의 외적 부호로 시계·반시계 방향을 계산한다."""
            return float(np.cross(b-a, c-a))

        def intersects(a, b, c, d):
            """두 선분 AB와 CD가 교차하거나 접하는지 확인한다."""
            o = [orient(a,b,c), orient(a,b,d), orient(c,d,a), orient(c,d,b)]
            if o[0]*o[1] < 0 and o[2]*o[3] < 0:
                return True
            for v, q, u, w in [(o[0],c,a,b),(o[1],d,a,b),
                                (o[2],a,c,d),(o[3],b,c,d)]:
                if abs(v) < 1e-8 and np.all(q >= np.minimum(u,w)) and np.all(q <= np.maximum(u,w)):
                    return True
            return False

        # 인접 변을 제외한 모든 변 조합을 검사해 자기 교차 ROI를 차단한다.
        for i in range(len(p)):
            for j in range(i+1,len(p)):
                if j == i+1 or (i == 0 and j == len(p)-1):
                    continue
                if intersects(p[i], p[(i+1)%len(p)], p[j], p[(j+1)%len(p)]):
                    raise ValueError("ROI must not self-intersect")
        self.vertices = p
        self._masks = {}

    def pixels(self, shape):
        """정규화 꼭짓점을 주어진 영상 크기의 픽셀 좌표로 변환한다."""
        h,w = shape[:2]
        return self.vertices * np.array([w-1,h-1], np.float32)

    def mask(self, shape):
        """ROI 내부가 ``True``인 불리언 마스크를 해상도별로 생성·재사용한다."""
        key = tuple(shape[:2])
        if key not in self._masks:
            # 같은 해상도에서는 매 프레임 다각형을 다시 채우지 않도록 마스크를 캐시한다.
            m = np.zeros(key, np.uint8)
            cv2.fillPoly(m, [np.rint(self.pixels(shape)).astype(np.int32)], 1)
            self._masks[key] = m.astype(bool)
        return self._masks[key]

    def contains(self, point, shape, tolerance_px=0.0):
        """픽셀 점이 허용 오차를 포함한 ROI 내부 또는 경계에 있는지 반환한다."""
        return cv2.pointPolygonTest(self.pixels(shape), tuple(map(float,point)), True) >= -tolerance_px

    def box_outside(self, box, shape):
        """BBox 영역 중 ROI 바깥에 놓인 픽셀 면적 비율을 계산한다."""
        h,w = shape[:2]
        # 실수 BBox를 바깥 방향으로 반올림하고 영상 범위 안으로 자른다.
        x1,y1 = np.floor(box[:2]).astype(int)
        x2,y2 = np.ceil(box[2:]).astype(int)
        x1,x2 = np.clip([x1,x2],0,w)
        y1,y2 = np.clip([y1,y2],0,h)
        if x2 <= x1 or y2 <= y1:
            raise ValueError("empty bounding box")
        return float(1.0-self.mask(shape)[y1:y2,x1:x2].mean())


def box_iou(a,b):
    """두 ``xyxy`` BBox의 교집합/합집합 면적비(IoU)를 계산한다."""
    # 교집합의 우하단이 좌상단보다 작으면 각 축 길이를 0으로 잘라 겹침 없음으로 처리한다.
    lo,hi = np.maximum(a[:2],b[:2]), np.minimum(a[2:],b[2:])
    inter = float(np.prod(np.maximum(hi-lo,0)))
    area = float(np.prod(a[2:]-a[:2])+np.prod(b[2:]-b[:2]))
    return inter/max(area-inter,1e-9)


def _draw_roi(canvas, roi):
    """판정에 사용한 고정 ROI 경계를 프리뷰에 그린다."""
    points = np.rint(roi.pixels(canvas.shape)).astype(np.int32)
    cv2.polylines(canvas, [points], True, (255, 200, 0), 2)


def _draw_banner(canvas, text, color):
    """프리뷰 상단에 판정 상태 한 줄을 표시한다."""
    cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 34), (20, 20, 20), -1)
    cv2.putText(canvas, text, (10, 24), cv2.FONT_HERSHEY_SIMPLEX,
                0.6, color, 2, cv2.LINE_AA)


class PatientSelector:
    """생체 식별 없이 공간 겹침만으로 침대 환자를 보수적으로 연결한다.

    최초에는 침대 ROI와 겹치는 사람만 선택하고 이후에는 이전 BBox와의 IoU를 사용한다.
    후보 점수가 비슷해 모호하면 임의로 고르지 않고 판단 불가 상태를 반환한다.
    """

    def __init__(self, roi, min_iou=0.15, init_overlap=0.5, ambiguity_gap=0.15):
        """ROI와 추적 IoU·최초 겹침·모호성 기준을 검증·저장한다."""
        if not 0.0 <= min_iou <= 1.0:
            raise ValueError("min_iou must be in [0,1]")
        if not 0.0 <= init_overlap <= 1.0:
            raise ValueError("init_overlap must be in [0,1]")
        if not 0.0 <= ambiguity_gap <= 1.0:
            raise ValueError("ambiguity_gap must be in [0,1]")
        self.roi = roi
        self.min_iou = float(min_iou)
        self.init_overlap = float(init_overlap)
        self.ambiguity_gap = float(ambiguity_gap)
        self.reset()

    def reset(self):
        """이전 프레임에서 선택했던 환자 BBox를 삭제한다."""
        self.previous = None

    def select(self, candidates, shape):
        """순서도의 '단일 환자 후보 선정'을 수행하고 판단 불가 이유를 반환한다."""
        # [PART05-V5-07 단일 환자 선정]
        # 이전: BedExitMethod.judge() → YoloMethod.candidates()
        # 다음: BedExitMethod.judge() → SegmentationROI.decide()
        # 좌표가 유효하고 양의 면적을 가진 후보만 환자 선택에 사용한다.
        candidates = [c for c in candidates if np.isfinite(c.bbox).all()
                      and np.all(c.bbox[2:]>c.bbox[:2])]
        if not candidates:
            return None, "no_person"

        reference = self.previous
        # 후보마다 최초 ROI 겹침 또는 이전 BBox IoU 점수를 계산한다.
        scored = []
        for c in candidates:
            if reference is None:
                # [순서도: 이전 환자 위치 없음]
                # BBox의 init_overlap 이상이 Bed ROI와 겹치는 후보 중 겹침 비율이 가장 큰 사람을 고른다.
                overlap = 1-self.roi.box_outside(c.bbox,shape)
                if overlap >= self.init_overlap:
                    scored.append((overlap,c))
            else:
                # [순서도: 이전 환자 위치 있음]
                # 이전 환자 BBox와 min_iou 이상 겹치는 사람만 같은 환자로 연결한다.
                overlap = box_iou(reference,c.bbox)
                if overlap >= self.min_iou:
                    scored.append((overlap,c))
        scored.sort(key=lambda x:x[0],reverse=True)

        # 기준을 만족하는 후보가 없으면 다른 사람을 임의 환자로 바꾸지 않는다.
        if not scored:
            return None, "patient_unassociated"
        # 1·2위 점수 차가 ambiguity_gap 이하이면 방문자와 환자를 구별할 근거가 부족하다고 본다.
        if len(scored)>1 and scored[1][0]>=scored[0][0]-self.ambiguity_gap:
            return None, "ambiguous_people"
        chosen = scored[0][1]

        # 다음 프레임 연결을 위해 확정 환자의 현재 BBox를 복사해 보관한다.
        self.previous = chosen.bbox.copy()
        return chosen, "ok"


class BedExitMethod(FrameMethod):
    """모든 내장 모델이 동일한 ``judge(frame)`` 흐름을 공유하도록 하는 전략 기반 클래스."""

    def __init__(self, roi, *, min_iou=0.15, init_overlap=0.5, ambiguity_gap=0.15):
        """ROI와 환자 선택기를 만들고 프레임별 상태를 초기화한다."""
        self.roi = roi
        self._selector_kwargs = {
            "min_iou": min_iou,
            "init_overlap": init_overlap,
            "ambiguity_gap": ambiguity_gap,
        }
        self.selector = PatientSelector(roi, **self._selector_kwargs)
        self.reset()

    def reset(self):
        """환자 연결과 가장 최근 진단·후보 목록을 초기 상태로 되돌린다."""
        self.selector.reset()
        self.last = Diagnostics()
        self.last_candidates: list[Candidate] = []

    def set_roi(self, roi):
        """정적 이미지별 ROI를 바꿀 때 선택기와 추적 상태를 함께 초기화한다."""
        self.roi = roi
        self.selector = PatientSelector(roi, **self._selector_kwargs)
        self.reset()

    @abstractmethod
    def candidates(self, frame) -> list[Candidate]:
        """백엔드별 사전학습 추론으로 사람 후보 목록을 생성한다."""

    @abstractmethod
    def decide(self, candidate, shape) -> tuple[bool | None, float | None, str]:
        """선택된 환자를 판정하고 근거 부족이면 결정값으로 ``None``을 반환한다."""

    def judge(self, frame) -> bool:
        """순서도의 사람 인식 → 환자 선정 → 마스크 판정을 순서대로 수행한다."""
        # [PART05-V5-06~08 프레임 단위 대표 판단 흐름]
        # 호출자: bed_exit_node.BedExitNode.process_frame()
        # 내부 순서: candidates() → PatientSelector.select() → decide()
        # 반환 후: bed_exit_state.BedExitState.update()
        # 모든 백엔드가 같은 OpenCV BGR uint8 입력 계약을 따르도록 경계에서 검증한다.
        if (not isinstance(frame,np.ndarray) or frame.ndim!=3 or frame.shape[2]!=3
                or frame.dtype!=np.uint8 or frame.size==0):
            self.last = Diagnostics(reason="invalid_frame")
            self.last_candidates = []
            raise JudgmentUnavailable(self.last.reason)
        self.last = Diagnostics(reason="inference_pending")

        # [순서도: 이미지에서 사람 인식] YOLO11n-seg 결과를 사람 후보 목록으로 변환한다.
        self.last_candidates = self.candidates(frame)

        # [순서도: 단일 환자 후보 선정] 여러 사람 중 침대 환자로 연결되는 한 명만 선택한다.
        patient,reason = self.selector.select(self.last_candidates,frame.shape)
        if patient is None:
            self.last = Diagnostics(reason=reason)
            raise JudgmentUnavailable(reason)
        # [순서도: 환자 마스크와 Bed ROI 비교] 선택된 환자만 ROI 규칙으로 판정한다.
        decision,score,reason = self.decide(patient,frame.shape)
        # score는 기하 비율이며 확률이 아니다. 진단·시각화 용도로만 보관한다.
        self.last = Diagnostics(
            valid=decision is not None,
            reason=reason,
            score=score,
            bbox=tuple(map(float, patient.bbox)),
            points=[] if patient.points is None else patient.points.tolist(),
            mask=None if patient.mask is None else patient.mask.copy(),
        )
        if decision is None:
            raise JudgmentUnavailable(reason)
        return bool(decision)

    def close(self):
        """기본 구현에는 해제할 공통 자원이 없으며 필요한 하위 클래스가 재정의한다."""
        pass


class YoloMethod(BedExitMethod):
    """Ultralytics YOLO 기반 Detection·Segmentation·Pose의 공통 추론 클래스."""

    def __init__(self, roi, weights, task, device="cpu",
                 confidence=YOLO_CONFIDENCE, imgsz=640, model=None,
                 min_iou=0.15, init_overlap=0.5, ambiguity_gap=0.15):
        """가중치와 추론 옵션을 검증하고 YOLO 모델을 준비한다."""
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be in [0,1]")
        if type(imgsz) is not int or imgsz <= 0:
            raise ValueError("imgsz must be a positive int")
        super().__init__(
            roi,
            min_iou=min_iou,
            init_overlap=init_overlap,
            ambiguity_gap=ambiguity_gap,
        )

        # 테스트가 가짜 모델을 주입하지 않은 운영 경로에서만 실제 가중치를 로드한다.
        if model is None:
            if not Path(weights).is_file():
                raise FileNotFoundError(f"Run the model-download cell first: {weights}")
            from ultralytics import YOLO
            model = YOLO(str(weights),task=task)
        self.model = model
        self.device, self.confidence, self.imgsz = device,confidence,imgsz

    def candidates(self, frame):
        """순서도의 '이미지에서 사람 인식'을 수행해 ``Candidate`` 목록을 만든다."""
        # [PART05-V5-06 이미지에서 사람 인식]
        # 이전: BedExitMethod.judge(); 다음: PatientSelector.select()
        # COCO person 클래스(0)만 추론하며 마스크는 원본 프레임 좌표로 요청한다.
        result = self.model.predict(source=frame,classes=[0],conf=self.confidence,
                                    imgsz=self.imgsz,device=self.device,
                                    retina_masks=True,verbose=False)[0]
        if result.boxes is None:
            return []

        # Ultralytics Tensor를 CPU NumPy로 옮기고 선택적 마스크·관절 출력을 분리한다.
        boxes = result.boxes.xyxy.cpu().numpy()
        confs = result.boxes.conf.cpu().numpy()
        masks = None if result.masks is None else result.masks.data.cpu().numpy()
        points = None if result.keypoints is None else result.keypoints.data.cpu().numpy()
        # 같은 사람 인덱스의 BBox·신뢰도·마스크·관절을 하나의 Candidate로 묶는다.
        out=[]
        for i,(box,conf) in enumerate(zip(boxes,confs)):
            mask = None if masks is None else masks[i]
            if mask is not None and mask.shape!=frame.shape[:2]:
                # retina_masks 요청 결과는 원본 해상도여야 하므로 다른 좌표계는 즉시 거부한다.
                raise ValueError("Unexpected mask coordinates; require original-size retina_masks")
            out.append(Candidate(box,float(conf),mask,None if points is None else points[i]))
        return out


class DetectionROI(YoloMethod):
    """사람 BBox 중 ROI 밖 면적 비율로 이탈 여부를 판단하는 내장 메서드."""

    def __init__(self, roi, weights, outside_fraction=0.05, **kwargs):
        """Detection YOLO와 허용 가능한 ROI 밖 BBox 비율을 초기화한다."""
        super().__init__(roi,weights,"detect",**kwargs)
        if not 0<=outside_fraction<=1:
            raise ValueError("outside_fraction must be in [0,1]")
        self.outside_fraction=outside_fraction

    def decide(self,candidate,shape):
        """선택된 환자 BBox의 ROI 밖 비율이 임계값을 넘는지 반환한다."""
        # BBox 전체 면적 중 침대 다각형에 포함되지 않는 비율을 이탈 점수로 사용한다.
        score=self.roi.box_outside(candidate.bbox,shape)
        return score>self.outside_fraction,score,"box_outside_fraction"

    def render(self, frame):
        """고정 ROI, 선택 환자 BBox와 ROI 밖 비율을 프레임에 표시한다."""
        canvas = frame.copy()
        _draw_roi(canvas, self.roi)
        if not self.last.valid or self.last.bbox is None:
            _draw_banner(canvas, f'DET | UNAVAILABLE ({self.last.reason})', (0, 165, 255))
            return canvas

        color = (0, 0, 255) if self.last.score > self.outside_fraction else (0, 200, 0)
        x1, y1, x2, y2 = (int(round(v)) for v in self.last.bbox)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
        verdict = 'EXIT(True)' if self.last.score > self.outside_fraction else 'SAFE(False)'
        _draw_banner(canvas, f'DET | {verdict} | outside={self.last.score * 100:.1f}%', color)
        return canvas


class SegmentationROI(YoloMethod):
    """사람 인스턴스 마스크 중 ROI 밖 픽셀 비율로 이탈을 판단하는 내장 메서드."""

    def __init__(self, roi, weights, outside_fraction=0.05, mask_threshold=0.5, **kwargs):
        """Segmentation YOLO와 허용 가능한 ROI 밖 마스크 비율·이진화 임계치를 초기화한다."""
        super().__init__(roi,weights,"segment",**kwargs)
        if not 0<=outside_fraction<=1:
            raise ValueError("outside_fraction must be in [0,1]")
        if not 0<=mask_threshold<=1:
            raise ValueError("mask_threshold must be in [0,1]")
        self.outside_fraction=outside_fraction
        self.mask_threshold=float(mask_threshold)

    def decide(self,candidate,shape):
        """순서도의 마스크 이진화·ROI 적용·이탈 임계치 판정을 수행한다."""
        # [PART05-V5-08 환자 마스크와 Bed ROI 비교]
        # 이전: PatientSelector.select(); 다음: BedExitState.update()
        # [순서도: 환자 마스크 존재 여부]
        # Segmentation 출력이 없으면 Detection BBox로 대신 판정하지 않고 판단 불가로 남긴다.
        if candidate.mask is None:
            return None,None,"missing_mask"

        # [순서도: 환자 마스크 이진화] 모델 출력값이 mask_threshold를 초과한 픽셀만 환자로 본다.
        m=candidate.mask>self.mask_threshold
        area=int(m.sum())
        if not area:
            return None,None,"empty_mask"

        # [순서도: Bed ROI 마스크 적용 → ROI 밖 픽셀 비율 계산]
        # 계산식은 (환자 마스크 AND NOT Bed ROI)의 픽셀 수 / 전체 환자 마스크 픽셀 수다.
        score=float(np.count_nonzero(m & ~self.roi.mask(shape))/area)
        # [순서도: ROI 밖 비율 임계치 초과 여부] 정확히 임계치는 정상, 초과는 이탈이다.
        return score>self.outside_fraction,score,"mask_outside_fraction"

    def render(self, frame):
        """고정 ROI와 선택 환자 마스크의 안쪽·바깥쪽 픽셀을 표시한다."""
        canvas = frame.copy()
        _draw_roi(canvas, self.roi)
        if not self.last.valid or self.last.mask is None:
            _draw_banner(canvas, f'SEG | UNAVAILABLE ({self.last.reason})', (0, 165, 255))
            return canvas

        person_mask = self.last.mask > self.mask_threshold
        roi_mask = self.roi.mask(frame.shape)
        overlay = canvas.copy()
        overlay[person_mask & roi_mask] = (0, 200, 0)
        overlay[person_mask & ~roi_mask] = (0, 0, 255)
        cv2.addWeighted(overlay, 0.42, canvas, 0.58, 0.0, dst=canvas)
        color = (0, 0, 255) if self.last.score > self.outside_fraction else (0, 200, 0)
        verdict = 'EXIT(True)' if self.last.score > self.outside_fraction else 'SAFE(False)'
        _draw_banner(canvas, f'SEG | {verdict} | outside={self.last.score * 100:.1f}%', color)
        return canvas


# 두 Pose 모델에 같은 정책을 적용하기 위해 공통 13개 관절의 인덱스를 대응시킨다.
# MediaPipe에만 있는 손가락·발 세부점·추가 얼굴점은 공정한 비교를 위해 제외한다.
COMMON_LANDMARKS = (
    ("nose", 0, 0),
    ("left_shoulder", 5, 11), ("right_shoulder", 6, 12),
    ("left_elbow", 7, 13), ("right_elbow", 8, 14),
    ("left_wrist", 9, 15), ("right_wrist", 10, 16),
    ("left_hip", 11, 23), ("right_hip", 12, 24),
    ("left_knee", 13, 25), ("right_knee", 14, 26),
    ("left_ankle", 15, 27), ("right_ankle", 16, 28),
)
COCO_COMMON=tuple(coco for _,coco,_ in COMMON_LANDMARKS)
MP_COMMON=tuple(mediapipe for _,_,mediapipe in COMMON_LANDMARKS)

# COMMON_LANDMARKS 배열 안의 위치를 연결한 간소화된 사람 골격이다.
COMMON_SKELETON = (
    (0, 1), (0, 2), (1, 2),
    (1, 3), (3, 5), (2, 4), (4, 6),
    (1, 7), (2, 8), (7, 8),
    (7, 9), (9, 11), (8, 10), (10, 12),
)


def _render_pose(frame, diagnostics, roi, indexes, point_conf, label, skeleton_color):
    """Pose 방법 이름·공통 관절·연결선을 모델별 색으로 표시한다."""
    canvas = frame.copy()
    _draw_roi(canvas, roi)
    if not diagnostics.valid or not diagnostics.points:
        _draw_banner(canvas, f'{label} | UNAVAILABLE ({diagnostics.reason})', (0, 165, 255))
        return canvas

    points = np.asarray(diagnostics.points, dtype=np.float32)
    selected = points[list(indexes)]
    usable = np.isfinite(selected).all(axis=1) & (selected[:, 2] >= point_conf)
    for start, end in COMMON_SKELETON:
        if usable[start] and usable[end]:
            a = tuple(np.rint(selected[start, :2]).astype(int))
            b = tuple(np.rint(selected[end, :2]).astype(int))
            cv2.line(canvas, a, b, skeleton_color, 2, cv2.LINE_AA)
    for index, point in enumerate(selected):
        if usable[index]:
            center = tuple(np.rint(point[:2]).astype(int))
            cv2.circle(canvas, center, 4, skeleton_color, -1, cv2.LINE_AA)

    color = (0, 0, 255) if diagnostics.score and diagnostics.score > 0 else (0, 200, 0)
    verdict = 'EXIT(True)' if diagnostics.score and diagnostics.score > 0 else 'SAFE(False)'
    _draw_banner(canvas, f'{label} | {verdict} | outside={diagnostics.score * 100:.1f}%', color)
    return canvas


def pose_decision(points, roi, shape, common, torso, point_conf=0.5,
                  tolerance_px=3.0, policy="any_body"):
    """Pose 관절의 ROI 안팎 상태로 이탈·정상·판단 불가 중 하나를 결정한다."""
    # 공통 관절의 최대 인덱스까지 결과가 없으면 판정에 필요한 배열 자체가 부족하다.
    if points is None or len(points)<=max(common):
        return None,None,"missing_keypoints"
    p=np.asarray(points)
    # 좌표·신뢰도가 유효한 관절만 이후 판정에 사용한다.
    usable=np.isfinite(p).all(axis=1) & (p[:,2]>=point_conf)
    core=[i for i in torso if usable[i]]
    if len(core)<2:
        return None,None,"insufficient_torso_keypoints"
    if policy=="torso":
        # torso 정책은 보이는 몸통 관절의 평균점 하나를 대표 위치로 사용한다.
        selected=p[core,:2].mean(axis=0)[None,:]
    elif policy=="any_body":
        # any_body 정책은 두 모델이 공통으로 제공하는 모든 유효 관절을 사용한다.
        selected=p[[i for i in common if usable[i]],:2]
    else:
        raise ValueError("policy must be any_body or torso")
    # 선택된 관절마다 ROI 경계 허용 오차를 적용해 바깥 여부를 계산한다.
    outside=[not roi.contains(x,shape,tolerance_px) for x in selected]
    score=float(np.mean(outside))
    # 관절 하나라도 밖이면 부분 가림 상태에서도 이탈의 양성 근거로 사용할 수 있다.
    if any(outside):
        return True,score,"visible_keypoint_outside"
    # 반대로 정상 판정은 필요한 관절이 모두 보여야 한다. 누락 관절을 정상으로 오인하지 않는다.
    if policy=="any_body" and not all(usable[i] for i in common):
        return None,score,"incomplete_body_coverage"
    if policy=="torso" and len(core)!=len(torso):
        return None,score,"incomplete_torso_coverage"

    # 요구 관절이 모두 관측되고 ROI 안에 있을 때만 안전 False를 확정한다.
    return False,score,"keypoints_inside"


class YoloPoseROI(YoloMethod):
    """YOLO COCO 관절을 공통 Pose 판정 규칙에 연결하는 내장 메서드."""

    def __init__(self,roi,weights,policy="any_body",point_conf=0.5,
                 tolerance_px=3.0,**kwargs):
        """YOLO Pose 모델과 관절 판정 정책·신뢰도·경계 허용 오차를 설정한다."""
        if policy not in ("any_body", "torso"):
            raise ValueError("policy must be any_body or torso")
        if not 0.0 <= point_conf <= 1.0:
            raise ValueError("point_conf must be in [0,1]")
        if not np.isfinite(tolerance_px) or tolerance_px < 0.0:
            raise ValueError("tolerance_px must be >= 0")
        super().__init__(roi,weights,"pose",**kwargs)
        self.policy,self.point_conf,self.tolerance_px=policy,float(point_conf),float(tolerance_px)

    def decide(self,candidate,shape):
        """COCO 관절 인덱스를 이용해 공통 ``pose_decision``을 호출한다."""
        return pose_decision(candidate.points,self.roi,shape,COCO_COMMON,
                             (5,6,11,12),self.point_conf,self.tolerance_px,self.policy)

    def render(self, frame):
        """YOLO Pose의 COCO 공통 관절을 자홍색 골격으로 표시한다."""
        return _render_pose(
            frame, self.last, self.roi, COCO_COMMON, self.point_conf,
            'YOLO POSE', (255, 0, 255),
        )


class MediaPipePoseROI(BedExitMethod):
    """MediaPipe 33개 관절을 공통 Pose 판정 규칙에 연결하는 내장 메서드."""

    def __init__(self,roi,weights,policy="any_body",point_conf=0.5,
                 tolerance_px=3.0, num_poses=4,
                 min_pose_detection_confidence=0.5,
                 min_pose_presence_confidence=0.5,
                 min_iou=0.15, init_overlap=0.5, ambiguity_gap=0.15):
        """MediaPipe PoseLandmarker와 관절 판정 옵션을 초기화한다."""
        if policy not in ("any_body", "torso"):
            raise ValueError("policy must be any_body or torso")
        if not 0.0 <= point_conf <= 1.0:
            raise ValueError("point_conf must be in [0,1]")
        if not np.isfinite(tolerance_px) or tolerance_px < 0.0:
            raise ValueError("tolerance_px must be >= 0")
        if type(num_poses) is not int or num_poses < 1:
            raise ValueError("num_poses must be an int >= 1")
        if not 0.0 <= min_pose_detection_confidence <= 1.0:
            raise ValueError("min_pose_detection_confidence must be in [0,1]")
        if not 0.0 <= min_pose_presence_confidence <= 1.0:
            raise ValueError("min_pose_presence_confidence must be in [0,1]")
        super().__init__(
            roi,
            min_iou=min_iou,
            init_overlap=init_overlap,
            ambiguity_gap=ambiguity_gap,
        )

        # MediaPipe는 선택 메서드일 때만 지연 import해 다른 세 방법의 실행 의존성을 줄인다.
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
        if not Path(weights).is_file():
            raise FileNotFoundError(f"Run model-download cell first: {weights}")
        self.mp=mp

        # 정지 이미지 모드로 최대 num_poses명의 Pose를 찾고 각 후보는 이후 환자 선택기를 거친다.
        self.model=vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_path=str(weights)),
            running_mode=vision.RunningMode.IMAGE,num_poses=num_poses,
            min_pose_detection_confidence=float(min_pose_detection_confidence),
            min_pose_presence_confidence=float(min_pose_presence_confidence)))
        self.policy,self.point_conf,self.tolerance_px=policy,float(point_conf),float(tolerance_px)

    def candidates(self,frame):
        """BGR 프레임을 MediaPipe에 전달하고 사람별 관절과 BBox 후보를 만든다."""
        # MediaPipe 입력 형식에 맞춰 OpenCV BGR을 RGB로 변환한다.
        rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
        result=self.model.detect(self.mp.Image(image_format=self.mp.ImageFormat.SRGB,data=rgb))
        h,w=frame.shape[:2]
        out=[]

        # 검출된 사람별 랜드마크를 순회해 공통 Candidate 형식으로 변환한다.
        for landmarks in result.pose_landmarks:
            # 정규화 관절 좌표를 원본 프레임의 픽셀 좌표로 변환한다.
            points=np.array([[p.x*w,p.y*h,min(p.visibility,p.presence)] for p in landmarks],np.float32)
            good=points[list(MP_COMMON)]
            good=good[np.isfinite(good).all(axis=1) & (good[:,2]>=self.point_conf)]

            # 신뢰할 수 있는 공통 관절이 두 개 미만이면 사람 위치 박스를 만들 수 없다.
            if len(good)<2:
                continue

            # 유효 관절의 최소·최대 좌표로 환자 선택용 임시 BBox를 만든다.
            box=np.r_[good[:,:2].min(axis=0),good[:,:2].max(axis=0)]
            out.append(Candidate(box,float(good[:,2].mean()),points=points))
        return out

    def decide(self,candidate,shape):
        """MediaPipe 관절 인덱스를 이용해 공통 ``pose_decision``을 호출한다."""
        return pose_decision(candidate.points,self.roi,shape,MP_COMMON,
                             (11,12,23,24),self.point_conf,self.tolerance_px,self.policy)

    def render(self, frame):
        """MediaPipe Pose의 공통 관절을 청록색 골격으로 표시한다."""
        return _render_pose(
            frame, self.last, self.roi, MP_COMMON, self.point_conf,
            'MEDIAPIPE POSE', (255, 255, 0),
        )

    def close(self):
        """MediaPipe 네이티브 모델 자원을 해제한다."""
        self.model.close()


# 짧은 메서드 이름을 models/ 디렉터리의 실제 가중치 파일명에 대응시킨다.
MODEL_FILES={"detection":"yolo11n.pt","segmentation":"yolo11n-seg.pt",
             "yolo_pose":"yolo11n-pose.pt","mediapipe_pose":"pose_landmarker_full.task"}
