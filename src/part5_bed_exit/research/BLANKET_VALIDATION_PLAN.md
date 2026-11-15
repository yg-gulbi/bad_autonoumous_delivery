# 담요 조건 검증 계획

작성일: 2026-11-16

## 1. 현재 결론

- 오늘의 데이터셋과 POC는 **Roboflow Bed Exit v4로 고정**한다.
- Roboflow 원본 클래스는 `bed_exit`, `hospital_bed`, `in_bed`뿐이며 담요 유무·가림 정도 라벨이 없다.
- 검수한 20장에는 담요로 사람 관절이 가려진 검증 장면이 없다. 병상 위의 짙은 천은 침구이며 빈 병상 장면에서도 동일하게 존재한다.
- 따라서 현재 Roboflow 결과로 담요 조건 성능을 주장할 수 없다. 이번 POC 결과는 `blanket_coverage=unknown_or_none` 범위로만 해석한다.
- 오늘은 SLP·BlanketSet을 다운로드하거나 Roboflow와 병합하지 않는다.

## 2. 후속 데이터 보충 원칙

후속 단계에서 SLP 또는 BlanketSet을 후보로 검토하되, 실제 취득 전 라이선스·분할·RGB 해상도·담요 조건 metadata를 확인한다. 원본 데이터셋 간 라벨과 촬영 조건이 다르므로 Roboflow와 물리적으로 병합하지 않고 dataset별 결과를 따로 계산한다.

| track | 입력 | 사용 목적 | 보고 가능한 근거 |
|---|---|---|---|
| RGB | 실제 RGB frame | 현재 네 방법과 같은 입력으로 담요 가림 강건성 평가 | RGB-only 결과만 보고 |
| RGB-D | 시간 정렬된 실제 RGB와 실제 depth | RGB pose 실패 시 실제 depth 보조 가능성 연구 | 실제 depth 센서가 있는 표본 결과만 보고 |
| 압력 | 시간 정렬된 실제 pressure map | 완전 가림 시 재실·자세 보조 가능성 연구 | 실제 압력 센서 표본 결과만 보고 |

RGB, RGB-D, 압력 track은 입력 정보량이 다르므로 같은 모델의 직접 우열처럼 합쳐 보고하지 않는다.

## 3. 금지 사항

- 단일 RGB에서 생성한 pseudo-depth를 실제 RGB-D 결과로 표기하지 않는다.
- pseudo-depth를 담요 조건 성능 개선의 정량 근거, 센서 대체 근거 또는 안전성 근거로 사용하지 않는다.
- 담요에 가린 관절을 수작업 추정해 실제 검출 landmark로 기록하지 않는다.
- `no_person` 또는 `incomplete_body_coverage`를 정상 `False`로 치환하지 않는다.
- Roboflow 상태 라벨을 다른 데이터셋의 자세·담요 라벨과 이름만 보고 자동 병합하지 않는다.

## 4. 최소 검증 행렬

후속 표본은 아래 조건을 교차해 기록한다. 표본 수와 합격 임계값은 팀이 임상 위험과 목표 지연시간을 확정한 뒤 별도 승인한다.

| 축 | 조건 |
|---|---|
| 담요 가림 | 없음, 하체 부분 가림, 몸통 부분 가림, 전신 가림 |
| 상태 | 누움, 몸 돌림, 상체 세움, 병상 가장자리 앉음, 팔·다리 먼저 이탈, 완전 이탈, 빈 병상 |
| 시야 | 정면/사선, 부분 crop, 의료기기·난간 가림 |
| 환경 | 밝음/어두움, 침구와 옷 색 유사, 여러 사람 진입 |

각 장면은 `dataset`, `subject/session`, `blanket_coverage`, `state`, `modality`, `ROI version`, `expected raw bool`, `method`, `predicted raw bool`, `failure reason`, `inference time`을 기록한다. 같은 세션의 인접 frame은 train/test에 나누지 않는다.

## 5. Pose·MediaPipe 안전 동작 검증

1. 실제 RGB를 두 모델에 동일 전처리와 동일 ROI로 입력한다.
2. 관절이 충분하면 raw Bool을 계산한다.
3. 담요 때문에 사람이 없거나 신뢰 가능한 몸통·사지가 부족하면 `JudgmentUnavailable`을 유지한다.
4. 판단 불가 frame은 5-frame 이력에 `False`로 넣지 않고 이력을 초기화한다.
5. 유효한 연속 입력에서만 최근 5-frame 중 3회 `True`를 ROS Bool로 공개한다.
6. 분석 지표로 3-frame 연속 `True`를 계산할 수 있으나 배포 판정과 혼합하지 않는다.

핵심 담요 지표는 Bool 정확도만이 아니라 `valid decision rate`, `no_person`, `incomplete_body_coverage`, `patient_unassociated`, `ambiguous_people`, 판단까지 걸린 시간이다. 완전 가림에서 판단 불가가 증가하는 것은 실패로 기록하되 거짓 정상 발행보다 안전한 동작으로 분리 보고한다.

## 6. 실행 게이트

후속 담요 실험 전에 팀이 다음 네 항목을 확정해야 한다.

1. 사용할 보충 데이터셋과 라이선스
2. 담요 가림 단계의 annotation 정의
3. RGB-only와 multimodal 각각의 합격 기준
4. 피험자·세션 단위 데이터 분할

현재 단계에서는 이 네 결정을 임의 수치로 채우지 않는다.
