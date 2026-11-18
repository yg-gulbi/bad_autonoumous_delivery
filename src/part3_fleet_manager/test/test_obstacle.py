import json
import pytest

from part3_fleet_manager.core.obstacle import classify, description


@pytest.mark.parametrize("person_conf,expected", [(.82,"PERSON"),(.39,"OBJECT")])
def test_40_labels_with_and_without_depth(cfg,person_conf,expected):
    payload = {"status":"ok","obstacle_present":True,"header":{"stamp":1,"frame_id":"oakd_rgb_camera_optical_frame"},
               "detections":[{"label":"person","confidence":person_conf,"bbox":[210,80,400,470],
                              "pose":{"x":-1.52,"y":-2.31,"z":0}}, {"label":"chair","confidence":.4}]}
    obs = classify(json.dumps(payload),cfg.obstacle,2)
    assert obs["kind"] == expected
    assert obs["labels"] == (["person","chair"] if expected == "PERSON" else ["chair"])
    person,chair = obs["detections"]
    assert person["label_ko"] == "사람" and person["confidence"] == person_conf and person["pose"]["x"] == -1.52
    assert chair["label_ko"] == "의자" and "pose" not in chair
    text = description(obs,(0,0),cfg.obstacle)
    assert ("로봇 앞" in text) == (expected == "PERSON")


@pytest.mark.parametrize("payload,kind", [
    ('{"status":"ok","obstacle_present":false}', "NONE"),
    ('{"status":"image_timeout","obstacle_present":null}', "UNKNOWN"),
    ('{"status":"processing_error","obstacle_present":null}', "UNKNOWN"),
    ('{"status":"ok","obstacle_present":true,"detections":[{"label":"chair","confidence":0.39}]}', "UNKNOWN"),
    ('{"status":"ok","obstacle_present":"false"}', "UNKNOWN"), ('{broken', "UNKNOWN")])
def test_40_unavailable_and_negative_are_distinct(cfg,payload,kind):
    assert classify(payload,cfg.obstacle,1)["kind"] == kind
