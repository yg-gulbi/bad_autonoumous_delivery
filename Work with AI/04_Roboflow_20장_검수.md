# 04. Roboflow 20장 검수

## 1. 수행 내용

- 2단계 POC의 고정 표본 20장과 동일한 이미지를 검수했다.
- 원본 이미지에 `hospital_bed` box(초록색)와 `bed_exit`/`in_bed` 환자 box(빨강/파랑)를 겹친 4×5 검수 시트를 생성했다.
- 원본 512×512 이미지도 개별 확인했다.
- 합의된 단일 ROI 기준으로 정상/비정상 초안을 만들고 실제 pose 결과와 대조했다.

## 2. 결과

- 표본: `bed_exit` 6장, `in_bed` 6장, 빈 병상 8장
- 단일 ROI 사람 검수: 비정상 5장, 정상 7장, 판정 불가인 빈 병상 8장
- 원본 `bed_exit` 중 #2는 사람 geometry가 ROI 안이다. 이는 오류로 단정할 수 없고, 시퀀스 상태 라벨과 단일 프레임 MVP 규칙의 의미 차이다.
- 네 방법 모두 JPEG 입력 형식에는 호환된다. 실제 추론은 담당 범위인 YOLO Pose와 MediaPipe Pose만 수행했고 Detection·Segmentation 결과는 만들지 않았다.
- 현재 20장은 같은 촬영 시퀀스 계열이므로 정식 정확도나 일반화 성능으로 보고하지 않는다.

## 3. 사용할 수 있는 3장

1. #5 `frame-000217...`: 명확한 이탈, YOLO Pose와 MediaPipe 모두 `True`
2. #12 `frame-000025...`: 명확한 정상, 두 방법 모두 `False`
3. #13 `frame-000002...`: 빈 병상, 두 방법 모두 `JudgmentUnavailable`

이 세 장으로 모듈의 세 출력 경로 `True`/`False`/판단 불가를 설명할 수 있다.

## 4. 코너케이스

- #2: 원본 `bed_exit`, 정적 ROI 판정은 정상, 앉은 자세의 조기징후 후보
- #4: 한 명 장면에서 MediaPipe `ambiguous_people`
- #7~#11: 누운 자세에서 pose 불완전
- #20: 병상 box가 이미지 하단까지 닿는 ROI annotation outlier

전체 20장 표와 재현 명령은 `src/part5_bed_exit/research/ROBOFLOW_20_IMAGE_AUDIT.md`에 기록했다. 검수 시트는 원본 데이터와 함께 Git 제외 상태로 유지한다.
