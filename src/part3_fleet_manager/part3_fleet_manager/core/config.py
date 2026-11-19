"""Load and validate the fleet.yaml settings in BRIEF section 8."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from math import isfinite
from os import PathLike
from os.path import expanduser

import yaml

from . import geometry
from .schemas import MISSION_TYPES


CORRIDOR_MODES = ("DIRECTIONAL", "SHARED_YIELD", "EXCLUSIVE")


class ConfigError(ValueError):
    """An invalid fleet configuration, with the offending setting in the message."""


@dataclass(frozen=True)
class RobotCfg:
    label: str
    mark: str
    ns: str
    role: str
    start_zone: str


@dataclass(frozen=True)
class RobotLinkCfg:
    mode: str
    command_topic: str
    status_topic: str
    amcl_pose_topic: str
    battery_topic: str
    hazard_topic: str
    dock_status_topic: str
    request_id: str
    accept_timeout_s: float
    link_timeout_s: float


@dataclass(frozen=True)
class CorridorCfg:
    polygon: tuple[geometry.Point, ...]
    mouths: Mapping[str, geometry.Point]
    include_boundary: bool
    mode: str = "DIRECTIONAL"
    yield_enabled: bool = True
    yield_trigger_m: float = 1.2
    yield_release_m: float = 0.5
    yield_timeout_s: float = 10.0
    follow_gap_m: float = 1.0
    follow_resume_m: float = 1.4

    def region_of(self, point: geometry.Point) -> str:
        return geometry.region_of(point, self.polygon, include_boundary=self.include_boundary)


@dataclass(frozen=True)
class ResourcesCfg:
    clear_margin_m: float
    clear_hold_s: float
    decision_window_s: float
    aging_s: float
    zone_occupied_m: float = 0.6


@dataclass(frozen=True)
class DeadlockCfg:
    confirm_s: float
    card_timeout_s: float
    yield_zones: Mapping[str, str]


@dataclass(frozen=True)
class StallCfg:
    window_s: float
    move_m: float
    short_s: float
    long_s: float


@dataclass(frozen=True)
class ObstacleCfg:
    enabled: bool = True
    inspect_topic: str = "/{ns}/obstacle/inspect_image"
    result_topic: str = "/{ns}/obstacle_classifier/result"
    inspect_at_s: float = 10.0
    timeout_s: float = 6.0
    object_blocks_now: bool = True
    min_confidence: float = 0.4
    label_ko: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BedMonitorCfg:
    warn_s: float = 1.0
    lost_s: float = 3.0
    recover_s: float = 2.0
    min_rate_hz: float = 10.0
    require_at_start: bool = True


@dataclass(frozen=True)
class SafetyCfg:
    bed_exit_pause_targets: tuple[str, ...]
    bed_monitor: BedMonitorCfg = BedMonitorCfg()
    bump_neighbor_m: float = 1.0
    proximity_stop_m: float = 0.6
    proximity_resume_m: float = 0.9


@dataclass(frozen=True)
class MissionCfg:
    confirm_at_stops: bool
    return_to_dock: bool
    manual_level: int = 1
    retry_after_return: bool = False
    return_home: bool = True
    return_interruptible: bool = True
    return_level: int = 1


@dataclass(frozen=True)
class AssignmentCfg:
    mode: str = "ROLE"
    min_battery_pct: float = 20.0


@dataclass(frozen=True)
class PublishCfg:
    fleet_state_hz: float
    tick_hz: float
    finished_mission_limit: int = 20


@dataclass(frozen=True)
class MockCfg:
    speed_mps: float = 0.2
    stall_radius_m: float = 0.05
    supports_request_id: bool = True


@dataclass(frozen=True)
class FleetConfig:
    robots: Mapping[str, RobotCfg]
    type_to_robot: Mapping[str, str]
    robot_link: RobotLinkCfg
    zones: Mapping[str, geometry.Point]
    corridor: CorridorCfg
    resources: ResourcesCfg
    deadlock: DeadlockCfg
    stall: StallCfg
    safety: SafetyCfg
    mission: MissionCfg
    publish: PublishCfg
    db_path: str
    mock: MockCfg = MockCfg()
    obstacle: ObstacleCfg = field(default_factory=ObstacleCfg)
    assignment: AssignmentCfg = AssignmentCfg()
    zone_names: Mapping[str, str] = field(default_factory=dict)


def _mapping(value, path: str, keys=()) -> Mapping:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{path}: 설정은 매핑이어야 합니다")
    for key in keys:
        if key not in value:
            raise ConfigError(f"{path}.{key}: 필수 설정입니다")
    return value


def _value(data: Mapping, key: str, path: str, expected: type):
    if key not in data or type(data[key]) is not expected:
        raise ConfigError(f"{path}.{key}: {expected.__name__} 값이 필요합니다")
    return data[key]


def _text(value, path: str, choices=None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}: 비어 있지 않은 문자열이 필요합니다")
    if choices is not None and value not in choices:
        raise ConfigError(f"{path}: {', '.join(choices)} 중 하나여야 합니다")
    return value


def _number(value, path: str, *, minimum=None, positive: bool = False) -> float:
    message = f"{path}: {'양의 ' if positive else ''}유한한 수가 필요합니다"
    if type(value) not in (int, float):
        raise ConfigError(message)
    try:
        number = float(value)
    except OverflowError as exc:
        raise ConfigError(message) from exc
    if not isfinite(number) or (positive and number <= 0):
        raise ConfigError(message)
    if minimum is not None and number < minimum:
        raise ConfigError(f"{path}: {minimum} 이상이어야 합니다")
    return number


def _numbers(data: Mapping, path: str, keys, *, positive: bool = False) -> dict[str, float]:
    return {
        key: _number(data.get(key), f"{path}.{key}", minimum=0, positive=positive)
        for key in keys
    }


def _point(value, path: str) -> geometry.Point:
    data = _mapping(value, path)
    return (_number(data.get("x"), f"{path}.x"), _number(data.get("y"), f"{path}.y"))


def _distance_limits(data, section, stop, resume, defaults):
    values = {key: _number(data.get(key, getattr(defaults, key)), f"{section}.{key}", positive=True)
              for key in (stop, resume)}
    if values[resume] <= values[stop]:
        raise ConfigError(f"{section}.{resume}: {stop}보다 커야 합니다")
    return values


def _load_corridor(data: Mapping) -> CorridorCfg:
    raw = _value(data, "polygon", "corridor", list)
    vertices = []
    for i, pair in enumerate(raw):
        path = f"corridor.polygon[{i}]"
        if not isinstance(pair, list) or len(pair) != 2:
            raise ConfigError(f"{path}: [x, y] 좌표가 필요합니다")
        vertices.append((_number(pair[0], f"{path}.x"), _number(pair[1], f"{path}.y")))
    try:
        geometry.corridor_polygon(vertices)
    except ValueError as exc:
        raise ConfigError(f"corridor.polygon: {exc}") from exc

    regions = (geometry.TOP, geometry.BOTTOM, *geometry.ROOMS)
    mouths = _mapping(data.get("mouths"), "corridor.mouths", regions)
    include_boundary = data.get("include_boundary", False)
    if type(include_boundary) is not bool:
        raise ConfigError("corridor.include_boundary: bool 값이 필요합니다")
    points = {region: _point(mouths[region], f"corridor.mouths.{region}") for region in regions}
    if points[geometry.TOP] == points[geometry.BOTTOM]:
        raise ConfigError("corridor.mouths: TOP과 BOTTOM은 서로 달라야 합니다")
    # A fleet file written before shared corridors inherits the section 8 policy.
    keys = ("mode", "yield_enabled", "yield_trigger_m", "yield_release_m", "yield_timeout_s")
    policy = {key: data.get(key, getattr(CorridorCfg, key)) for key in keys}
    return CorridorCfg(
        **_distance_limits(data, "corridor", "follow_gap_m", "follow_resume_m", CorridorCfg),
        polygon=tuple(vertices),
        mouths=points,
        mode=_text(policy["mode"], "corridor.mode", CORRIDOR_MODES),
        yield_enabled=_value(policy, "yield_enabled", "corridor", bool),
        **_numbers(policy, "corridor", ("yield_trigger_m", "yield_release_m", "yield_timeout_s"), positive=True),
        include_boundary=include_boundary,
    )


def _topic(data, key, section):
    topic = _text(data.get(key), f"{section}.{key}")
    expanded = topic.replace("{ns}", "robot")
    if topic.count("{ns}") != 1 or not expanded.startswith("/") or (
        "{" in expanded or "}" in expanded or any(c.isspace() for c in expanded)
    ):
        raise ConfigError(f"{section}.{key}: {{ns}}가 한 개 포함된 절대 토픽이 필요합니다")
    return topic


def _load_robot_link(data: Mapping) -> RobotLinkCfg:
    topics = {}
    for key in ("command_topic", "status_topic", "amcl_pose_topic", "battery_topic",
                "hazard_topic", "dock_status_topic"):
        topics[key] = _topic(data, key, "robot_link")
    return RobotLinkCfg(
        mode=_text(data.get("mode"), "robot_link.mode", ("HOSPITAL_NAV",)),
        request_id=_text(data.get("request_id"), "robot_link.request_id", ("auto", "always", "never")),
        **topics,
        **_numbers(data, "robot_link", ("accept_timeout_s", "link_timeout_s"), positive=True),
    )


def config_from_dict(data: Mapping) -> FleetConfig:
    """Validate a decoded YAML mapping and its cross-references."""
    sections = ("robots", "type_to_robot", "robot_link", "zones", "corridor", "resources",
                "deadlock", "stall", "safety", "mission", "publish", "db")
    root = _mapping(data, "fleet", sections)
    cfg = {key: _mapping(root[key], key) for key in sections}
    corridor = _load_corridor(cfg["corridor"])

    zone_data = _mapping(cfg["zones"], "zones", geometry.ZONE_REGIONS)
    zones = {}
    for zone, expected_region in geometry.ZONE_REGIONS.items():
        point = _point(zone_data[zone], f"zones.{zone}")
        region = corridor.region_of(point)
        if region != expected_region:
            raise ConfigError(f"zones.{zone}: {expected_region}에 속해야 합니다 (현재 {region})")
        zones[zone] = point

    robots = {}
    namespaces = set()
    for robot_id, raw in cfg["robots"].items():
        _text(robot_id, "robots.<id>")
        path = f"robots.{robot_id}"
        robot = _mapping(raw, path)
        ns = _text(robot.get("ns"), f"{path}.ns")
        if ns in namespaces:
            raise ConfigError(f"{path}.ns: 중복 namespace {ns}")
        namespaces.add(ns)
        mark = _text(robot.get("mark", chr(ord("A") + len(robots))), f"{path}.mark")
        if len(mark) != 1 or any(value.mark == mark for value in robots.values()):
            raise ConfigError(f"{path}.mark: 서로 다른 표시 글자 한 자가 필요합니다")
        robots[robot_id] = RobotCfg(
            mark=mark,
            label=_text(robot.get("label"), f"{path}.label"),
            ns=ns,
            role=_text(robot.get("role"), f"{path}.role", MISSION_TYPES),
            start_zone=_text(robot.get("start_zone"), f"{path}.start_zone", zones),
        )

    role_map = {"CALL": cfg["type_to_robot"].get("MED_ROUND"), **cfg["type_to_robot"]}
    type_to_robot = {
        mission_type: _text(role_map.get(mission_type), f"type_to_robot.{mission_type}", robots)
        for mission_type in MISSION_TYPES
    }

    yield_zones = _mapping(cfg["deadlock"].get("yield_zones"), "deadlock.yield_zones")
    halls = [zone for zone, region in geometry.ZONE_REGIONS.items() if region in (geometry.TOP, geometry.BOTTOM)]
    yield_zones = {
        room: _text(yield_zones.get(room), f"deadlock.yield_zones.{room}", halls)
        for room in geometry.ROOMS
    }

    stall = StallCfg(**_numbers(cfg["stall"], "stall", ("window_s", "move_m", "short_s", "long_s"),
                                positive=True))
    if stall.short_s >= stall.long_s:
        raise ConfigError("stall.long_s: stall.short_s보다 커야 합니다")

    targets = _value(cfg["safety"], "bed_exit_pause_targets", "safety", list)
    policy = {**asdict(BedMonitorCfg()), **_mapping(cfg["safety"].get("bed_monitor", {}), "safety.bed_monitor")}
    monitor = BedMonitorCfg(**_numbers(policy, "safety.bed_monitor", ("warn_s", "lost_s", "recover_s", "min_rate_hz"),
                                       positive=True),
                            require_at_start=_value(policy, "require_at_start", "safety.bed_monitor", bool))
    if monitor.warn_s >= monitor.lost_s:
        raise ConfigError("safety.bed_monitor.lost_s: warn_s보다 커야 합니다")
    manual_level = cfg["mission"].get("manual_level", MissionCfg.manual_level)
    if type(manual_level) is not int or manual_level not in (1, 2, 3):
        raise ConfigError("mission.manual_level: 1~3 정수가 필요합니다")
    retry_after_return = cfg["mission"].get("retry_after_return", MissionCfg.retry_after_return)
    if type(retry_after_return) is not bool:
        raise ConfigError("mission.retry_after_return: bool 값이 필요합니다")
    mock = {**asdict(MockCfg()), **_mapping(root.get("mock", {}), "mock")}
    assignment = {**asdict(AssignmentCfg()), **_mapping(root.get("assignment", {}), "assignment")}
    minimum = _number(assignment["min_battery_pct"], "assignment.min_battery_pct", minimum=0)
    if minimum > 100:
        raise ConfigError("assignment.min_battery_pct: 0~100 범위가 필요합니다")
    mission = {"return_home": True, "return_interruptible": True, "return_level": 1, **cfg["mission"]}
    return_level = _value(mission, "return_level", "mission", int)
    if not 1 <= return_level <= 3:
        raise ConfigError("mission.return_level: 1~3 정수가 필요합니다")
    values = {**asdict(ObstacleCfg()), **_mapping(root.get("obstacle", {}), "obstacle")}
    threshold = _number(values["min_confidence"], "obstacle.min_confidence", minimum=0)
    if threshold > 1:
        raise ConfigError("obstacle.min_confidence: 0~1 범위가 필요합니다")
    obstacle = ObstacleCfg(**{k: _topic(values, k, "obstacle") for k in ("inspect_topic", "result_topic")},
                           **_numbers(values, "obstacle", ("inspect_at_s", "timeout_s"), positive=True),
                           enabled=_value(values, "enabled", "obstacle", bool),
                           object_blocks_now=_value(values, "object_blocks_now", "obstacle", bool),
                           min_confidence=threshold, label_ko={_text(k, "obstacle.label_ko"): _text(v, "obstacle.label_ko")
                               for k, v in _mapping(values["label_ko"], "obstacle.label_ko").items()})
    limit = cfg["publish"].get("finished_mission_limit", PublishCfg.finished_mission_limit)
    if type(limit) is not int or limit < 0:
        raise ConfigError("publish.finished_mission_limit: 0 이상의 정수가 필요합니다")
    return FleetConfig(obstacle=obstacle,
        assignment=AssignmentCfg(_text(assignment["mode"], "assignment.mode", ("IDLE_BEST_BATTERY", "ROLE")), minimum),
        mock=MockCfg(**_numbers(mock, "mock", ("speed_mps", "stall_radius_m"), positive=True),
                     supports_request_id=_value(mock, "supports_request_id", "mock", bool)),
        robots=robots,
        type_to_robot=type_to_robot,
        robot_link=_load_robot_link(cfg["robot_link"]),
        zones=zones,
        zone_names={z: zone_data[z].get("name") or f"{z} 구역" for z in zones},
        corridor=corridor,
        resources=ResourcesCfg(
            zone_occupied_m=_number(cfg["resources"].get("zone_occupied_m", ResourcesCfg.zone_occupied_m),
                                   "resources.zone_occupied_m", positive=True),
            **_numbers(cfg["resources"], "resources", ("clear_margin_m", "clear_hold_s", "decision_window_s", "aging_s")),
        ),
        deadlock=DeadlockCfg(
            **_numbers(cfg["deadlock"], "deadlock", ("confirm_s", "card_timeout_s"), positive=True),
            yield_zones=yield_zones,
        ),
        stall=stall,
        safety=SafetyCfg(
            bed_exit_pause_targets=tuple(_text(r, "safety.bed_exit_pause_targets", robots) for r in targets),
            bed_monitor=monitor,
            bump_neighbor_m=_number(cfg["safety"].get("bump_neighbor_m", SafetyCfg.bump_neighbor_m),
                                    "safety.bump_neighbor_m", minimum=0),
            **_distance_limits(cfg["safety"], "safety", "proximity_stop_m", "proximity_resume_m", SafetyCfg),
        ),
        mission=MissionCfg(
            return_home=_value(mission, "return_home", "mission", bool),
            return_interruptible=_value(mission, "return_interruptible", "mission", bool),
            return_level=return_level,
            manual_level=manual_level,
            retry_after_return=retry_after_return,
            confirm_at_stops=_value(cfg["mission"], "confirm_at_stops", "mission", bool),
            return_to_dock=_value(cfg["mission"], "return_to_dock", "mission", bool),
        ),
        publish=PublishCfg(finished_mission_limit=limit, **_numbers(cfg["publish"], "publish", ("fleet_state_hz", "tick_hz"),
                                      positive=True)),
        db_path=expanduser(_text(cfg["db"].get("path"), "db.path")),
    )


def read_config_yaml(path: str | PathLike) -> Mapping:
    """Read the fleet settings shared by the manager and the UI."""
    try:
        with open(path, encoding="utf-8") as source:
            data = yaml.safe_load(source)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return _mapping(data, "fleet")


def load_config(path: str | PathLike) -> FleetConfig:
    return config_from_dict(read_config_yaml(path))
