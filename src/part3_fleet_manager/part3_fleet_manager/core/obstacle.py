"""Decode a single PART 06 observation without treating a missing image as a clear path."""
import json
from math import hypot, isfinite


def finite(value):
    if type(value) not in (float, int):
        return False
    try:
        return isfinite(value)
    except OverflowError:
        return False


def is_yolo_result(payload):
    # At 406e497 YOLO publishes its boxed JPEG with a default ROS header. The abort
    # reporter copies the camera header. PART 06 echoes the image header in its verdict.
    try:
        body = json.loads(payload)
    except (TypeError, ValueError, RecursionError):
        return False
    header = body.get("header") if isinstance(body, dict) else None
    return header == {"stamp": {"sec": 0, "nanosec": 0}, "frame_id": ""}


def classify(payload, policy, at):
    result = {"kind": "UNKNOWN", "at": at, "labels": [], "detections": []}
    try:
        body = json.loads(payload)
    except (ValueError, TypeError, RecursionError):
        return result
    if not isinstance(body, dict) or body.get("status") != "ok":
        return result
    present, items = body.get("obstacle_present"), body.get("detections", [])
    if type(present) is not bool or not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        label, conf, pose = item.get("label"), item.get("confidence"), item.get("pose")
        if not isinstance(label, str) or not label.strip() or not finite(conf) or not 0 <= conf <= 1:
            continue
        detection = {"label": label, "label_ko": policy.label_ko.get(label, label), "confidence": conf}
        if isinstance(pose, dict) and all(finite(pose.get(k)) for k in ("x", "y")):
            detection["pose"] = {k: pose[k] for k in ("x", "y", "z") if finite(pose.get(k))}
        result["detections"].append(detection)
        if conf >= policy.min_confidence and label not in result["labels"]:
            result["labels"].append(label)
    if not present:
        result["kind"] = "NONE"
    elif result["labels"]:
        result["kind"] = "PERSON" if "person" in result["labels"] else "OBJECT"
    return result


def description(observation, pose, policy):
    kind = observation["kind"]
    if kind == "NONE":
        return "카메라에는 장애물이 보이지 않음 — 주행 문제일 수 있음"
    if kind == "UNKNOWN":
        return "장애물 분류 불가"
    detections = [d for d in observation["detections"] if d["confidence"] >= policy.min_confidence
                  and (kind != "PERSON" or d["label"] == "person")]
    names = ", ".join(dict.fromkeys(d["label_ko"] for d in detections))
    text = "사람 감지" if kind == "PERSON" else "물건이 길을 막고 있음: " + names
    distances = [hypot(d["pose"]["x"]-pose[0], d["pose"]["y"]-pose[1])
                 for d in detections if pose is not None and "pose" in d]
    return text + (f" (로봇 앞 약 {min(distances):.1f} m)" if distances else "")


def _subject(names):
    """Korean subject particle after the last name (상자가, 가방이)."""
    last = names[-1][-1] if names and names[-1] else ""
    final = 0xAC00 <= ord(last) <= 0xD7A3 and (ord(last) - 0xAC00) % 28
    return ", ".join(names) + ("이" if final else "가")


def report_description(observation, lidar_m, policy):
    """Card sentence for a PART 04 obstacle photo judged by PART 06 (BRIEF 5.5 report mode)."""
    kind = observation["kind"]
    if kind == "NONE":
        return policy.none_text
    if kind == "UNKNOWN":
        return "장애물 판별 불가 — 로봇이 찍은 사진 확인"
    detections = [d for d in observation["detections"] if d["confidence"] >= policy.min_confidence
                  and (kind != "PERSON" or d["label"] == "person")]
    names = list(dict.fromkeys(d["label_ko"] for d in detections))
    text = "사람 감지" if kind == "PERSON" else _subject(names) + " 길을 막고 있음"
    return text + (f" (로봇 앞 약 {lidar_m:.1f} m)" if finite(lidar_m) else "")


def best_detection(observation, policy):
    """Highest-confidence usable detection for the photo caption ("판별: 의료용 상자 0.93")."""
    usable = [d for d in observation["detections"] if d["confidence"] >= policy.min_confidence]
    return max(usable, key=lambda d: d["confidence"], default=None)
