import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from part3_fleet_manager.core import schemas
from part1_ui_dashboard import ui_api as api


@pytest.mark.parametrize("form,expected", [
    ({"level": "2", "destination": "C", "item": ""}, {"type": "CALL", "level": 2, "destination": "C"}),
    ({"type": "CALL", "level": 3, "destination": "E", "item": "거즈"}, {"destination": "E", "item": "거즈"}),
    ({"type": "PATIENT_TRANSPORT", "level": 1, "pickup": "A", "destination": "B", "name": "김", "chart_no": "123"},
     {"patient": {"name": "김", "chart_no": "123"}, "pickup": "A", "destination": "B"}),
    ({"type": "MED_ROUND", "level": 1, "stops": [{"zone": "F", "item": "거즈"}] * 6},
     {"stops": [{"zone": "F", "item": "거즈"}] * 6}),
])
def test_mission_contract(form, expected):
    data = api.mission(dict(form, operator=" 담당자 "))
    assert schemas.parse_mission_req(schemas.dumps(data)) == data
    assert data["requested_by"] == "담당자"
    assert all(data[k] == v for k, v in expected.items())
    if form.get("item") == "":
        assert "item" not in data


@pytest.mark.parametrize("form", [
    {"level": 1}, {"level": 4, "destination": "A"}, {"level": True, "destination": "A"},
    {"operator": "  ", "level": 1, "destination": "A"},
    {"level": 1, "type": "MED_ROUND", "stops": [{"zone": "B", "item": "거즈"}] * 7},
    {"level": 1, "type": "MED_ROUND", "stops": []},
])
def test_invalid_forms(form):
    with pytest.raises(ValueError):
        api.mission(form)


def test_ids_across_concurrent_requests():
    with ThreadPoolExecutor(4) as pool:
        ids = list(pool.map(lambda _: api.request_id(), range(100)))
    assert len(set(ids)) == len(ids)
    assert all(re.fullmatch(r"mon-\d{13}-\d+", ref) for ref in ids)


@pytest.mark.parametrize("cmd,fields", [
    ("PAUSE_ROBOT", {"robot": "robot_b"}), ("RESUME_ROBOT", {"robot": "robot_a"}),
    ("CONFIRM_STEP", {"robot": "robot_a", "result": "MISMATCH", "mission_id": "M-0003"}),
    ("CONFIRM_STEP", {"robot": "robot_a", "result": "OK", "mission_id": "M-0003"}),
    ("GO_ZONE", {"robot": "robot_b", "zone": "E"}), ("RETRY", {"robot": "robot_a"}),
    ("CANCEL_MISSION", {"mission_id": "M-0003"}),
])
def test_command_contract(cmd, fields):
    data = api.command(dict(fields, cmd=cmd))
    assert schemas.parse_operator_cmd(schemas.dumps(data)) == {"cmd_id": data["cmd_id"], "v": 1,
                                                               "by": "operator", "cmd": cmd, **fields}


def test_decision_uses_open_card_reference():
    data = api.decision({"card_id": "K-1", "choice": "yield_a", "operator": "간호사"})
    assert schemas.parse_decision(schemas.dumps(data)) == {"v": 1, "card_id": "K-1", "choice": "yield_a", "by": "간호사"}


def test_32_public_event_and_state_titles_are_complete():
    from pathlib import Path
    brief = (Path(__file__).resolve().parents[3] / "docs/part3/BRIEF.md").read_text()
    event_line = next(line for line in brief.splitlines() if line.startswith("이벤트 종류:"))
    assert set(re.findall(r"`([A-Z_]+)`", event_line)) <= api.EVENTS.keys()
    table = brief.split("### 5.1 ",1)[1].split("- **두 로봇",1)[0]
    assert set(re.findall(r"^\| `([A-Z_]+)`", table, re.M)) == api.STATES.keys()
    for template in [*api.EVENTS.values(), *api.STATES.values()]:
        assert re.search("[가-힣]", template)


def test_33_measured_floor_plan_against_original_occupancy():
    from math import atan2, ceil, degrees, dist
    from pathlib import Path
    import yaml
    from shapely.geometry import LineString, Point
    from part1_ui_dashboard.occupancy import Grid

    root = Path(__file__).resolve().parents[3]
    path = root / "main_map/main_maps.yaml"
    try:
        grid = Grid.read(path)
    except FileNotFoundError:
        pytest.skip("실제 맵 파일 없음")
    plan = yaml.safe_load((root / "src/part1_ui_dashboard/config/floor_plan.yaml").read_text())
    cfg = yaml.safe_load((root / "src/part3_fleet_manager/config/fleet.yaml").read_text())
    meta, image = api.map_meta(path, floor_plan=plan)
    assert image.startswith(b"P5\n") and 3.6 <= meta["deskew_deg"] <= 4.6
    matrix = meta["matrix"]
    wall_centers = grid.wall_centers()
    segments = []
    for x, y, u, v in plan["walls"]:
        segments.append(LineString([(x,y), (u,v)]))
        count = ceil(dist((x,y), (u,v))/.05)
        samples = [(x+(u-x)*i/count, y+(v-y)*i/count) for i in range(count+1)]
        assert sum(any(dist(p,q) <= .10 for q in wall_centers) for p in samples) >= .80*len(samples)
        p, q = api.map_point(matrix, x,y), api.map_point(matrix, u,v)
        angle = degrees(atan2(q[1]-p[1], q[0]-p[0]))
        assert abs(angle-90*round(angle/90)) <= .5
    labels = {}
    for z, pos in cfg["zones"].items():
        p = (pos["x"], pos["y"])
        assert min(line.distance(Point(p)) for line in segments) >= .25
        labels[z] = api.map_point(matrix, *p)
        assert api.unmap_point(matrix, *labels[z]) == pytest.approx(p)
    assert labels["D"][0] < labels["E"][0] < labels["F"][0]
    assert labels["A"][0] < labels["B"][0] < labels["C"][0]
    assert max(labels[z][1] for z in "DEF") < min(labels[z][1] for z in "ABC")
    tl,tr,br,bl = cfg["corridor"]["polygon"]
    for room, edge in (("ROOM_B",(tl,bl)), ("ROOM_E",(tr,br))):
        x,y,u,v = plan["doors"][room]
        assert LineString(edge).distance(Point((x+u)/2,(y+v)/2)) <= .10
    measured = api.measure_floor_plan(path)
    assert len(measured["walls"]) == len(segments)
    for reference, actual in zip(plan["walls"], measured["walls"]):
        assert dist(reference[:2], actual[:2]) <= .11
        assert dist(reference[2:], actual[2:]) <= .11


def test_34_delivery_patient_and_call_forms():
    form = {"type":"MED_ROUND","pickup":"D","destination":"B","item":"거즈","level":"3"}
    delivery = api.mission(form)
    assert delivery["stops"] == [{"zone":"D","item":"수령: 거즈"},{"zone":"B","item":"인계: 거즈"}]
    for bad in ({"pickup":"B"},{"item":"  "},{"extra":["D"]},{"destination":""},{"level":4},{"extra":["A","C","F","E","D"]}):
        with pytest.raises(ValueError):
            api.mission({**form,**bad})
    patient = api.mission({**form,"type":"PATIENT_TRANSPORT","chart_no":"TEST", "tag_id":"손목"})
    assert patient["patient"] == {"chart_no": "TEST", "tag_id": "손목"}
    blank = api.mission({**form, "type":"PATIENT_TRANSPORT", "chart_no":"TEST", "name":"  "})
    assert blank["patient"]["name"] == ""
    assert api.mission({"type":"CALL","destination":"A","level":2})["type"] == "CALL"


def test_summary_and_resume_evidence():
    road = {"blocked":False,"holders":["robot_b"],"give_way":None,"queue_detail":[{"robot":"robot_a"}],"state":"OCCUPIED"}
    state = {"resources":{"CORRIDOR":road}, "robots":{"robot_a":{"link_ok":True,"state":"HOLD"},
            "robot_b":{"link_ok":True,"state":"MOVING"}},"missions":[{"status":"ACTIVE","level":3,"type":"CALL","mission_id":"M-1"}]}
    summary=api.summary(state)
    assert (summary["online"],summary["active"],summary["urgent"],summary["waiting"]) == (2,1,["M-1"],["robot_a"])
    track, flags=api.resume_checks(state)
    assert flags == [False]*4
    road.update(holders=["robot_a"],queue_detail=[],state="GRANTED")
    state["robots"]["robot_a"]["state"]="MOVING"
    assert api.resume_checks(state,track)[1]==[True]*4


def test_cleaned_grid_keeps_door_and_exterior_unknown():
    from part1_ui_dashboard.occupancy import clean_grid, FREE, UNKNOWN, WALL

    width = 31
    cells = bytearray([UNKNOWN]) * (width*width)
    def index(x,y):
        return y*width+x
    for y in range(5,26):
        for x in range(5,26):
            cells[index(x,y)] = WALL if x in (5,25) or y in (5,25) else FREE
    for x in range(6,25):
        cells[index(x,15)] = WALL
    cells[index(9,15)] = FREE                         # one-cell break
    cells[index(14,15):index(18,15)] = bytes([FREE])*4  # doorway
    cells[index(9,10)] = WALL
    cells[index(10,11)] = WALL
    cells[index(20,20)] = UNKNOWN
    cells[index(1,1)] = FREE
    clean_grid(cells,width,width)
    assert cells[index(9,15)] == WALL
    assert all(cells[index(x,15)] == FREE for x in range(14,18))
    assert all(cells[index(x,y)] == FREE for x,y in ((9,10),(10,11),(20,20)))
    assert cells[index(1,1)] == cells[0] == UNKNOWN


def test_grid_crop_transform_and_pixel_centres(tmp_path):
    import yaml
    from part1_ui_dashboard.occupancy import read_pgm

    path = tmp_path/"map.yaml"
    path.write_text(yaml.safe_dump({"image": "map.pgm", "origin": [1,2,0], "resolution": .05}))
    (tmp_path/"map.pgm").write_bytes(b"P5\n6 6\n255\n"+bytes([254]*36))
    for rotate in ("none", "ccw90", "cw90", "180"):
        meta, image = api.map_meta(path, rotate, 0, .25)
        w,h,_,pixels = read_pgm(image)
        assert (w,h) == (16,16) and meta["deskew_deg"] == 0
        assert pixels.count(254) == 36 and pixels.count(205) == w*h-36
        col,row = api.map_point(meta["matrix"],1.025,2.025)
        assert pixels[int(row)*w+int(col)] == 254
        assert api.unmap_point(meta["matrix"],0,h) == pytest.approx(meta["origin"][:2])
