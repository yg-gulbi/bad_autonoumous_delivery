"""Load and validate the fleet.yaml settings in BRIEF section 8."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from math import cos, dist, isfinite, radians, sin
from os import PathLike
from os.path import expanduser

import yaml
from shapely.geometry import Polygon

from . import geometry
from .schemas import MISSION_TYPES


OBSTACLE_MODES = ("report", "inspect")
SEGMENT_KINDS = ("shared", "one_way", "single")
POINT_KINDS = ("hall", "crossroad", "door", "room", "dock")


def zone_display_name(zone, name):
    return f"{name}({zone})" if name else f"{zone} 구역"


class ConfigError(ValueError):
    """An invalid fleet configuration, with the offending setting in the message."""


@dataclass(frozen=True)
class DockPose:
    x: float
    y: float
    yaw_deg: float

    def path_end(self):
        yaw = radians(self.yaw_deg)
        return ((self.x - 0.6 * cos(yaw), self.y - 0.6 * sin(yaw)), (self.x, self.y))


@dataclass(frozen=True)
class RobotCfg:
    label: str
    mark: str
    ns: str
    role: str
    start_zone: str
    return_to: str = "ZONE"
    dock: DockPose | None = None


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
    start_timeout_s: float = 120.0
    control_retry_s: float = 1.5   # 4K: resend PAUSE if STATE is still MOVING after this long
    control_retries: int = 3       # 4K: more resends than this opens the STOP_UNCONFIRMED card
    # 4O: after a MOVE/DOCK/RESUME, PART 04 may hold its STATE up to this long while it starts Nav2
    # (wait_for_server). Until the next STATE the link counts as lost after link_timeout_s + command_grace_s.
    command_grace_s: float = 5.0


@dataclass(frozen=True)
class CorridorCfg:
    polygon: tuple[geometry.Point, ...]
    mouths: Mapping[str, geometry.Point]
    include_boundary: bool
    crossroad: geometry.Point            # PART 04 stops here first on the way into or out of room B·E
    follow_gap_m: float = 1.0
    follow_resume_m: float = 1.4
    crossroad_clear_m: float = 0.6

    def region_of(self, point: geometry.Point) -> str:
        return geometry.region_of(point, self.polygon, include_boundary=self.include_boundary)


@dataclass(frozen=True)
class ResourcesCfg:
    clear_margin_m: float
    clear_hold_s: float
    zone_occupied_m: float = 0.6


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
    mode: str = "inspect"
    image_topic: str = "/{ns}/hospital_nav/obstacle_image"
    marked_image_topic: str = "/{ns}/obstacle_classifier/image/compressed"
    report_wait_s: float = 4.0
    retry_if_clear: bool = False   # 4K: NONE ("no box") does not mean the path is clear (D-31)
    retry_delay_s: float = 3.0
    none_text: str = "의료용 상자는 보이지 않음"


@dataclass(frozen=True)
class BedMonitorCfg:
    warn_s: float = 1.0
    lost_s: float = 3.0
    recover_s: float = 2.0
    min_rate_hz: float = 10.0
    require_at_start: bool = True
    exit_clear_hold_s: float = 1.0
    enabled: bool = True          # off: bed exit and webcam loss neither stop robots nor raise alarms (D-51)


@dataclass(frozen=True)
class SafetyCfg:
    bed_exit_pause_targets: tuple[str, ...]
    bed_monitor: BedMonitorCfg = BedMonitorCfg()
    use_hazard_topic: bool = False
    bump_neighbor_m: float = 1.0
    proximity_stop_m: float = 0.6
    proximity_resume_m: float = 0.9
    slot_stop_m: float = 0.45


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
class SegmentSpec:
    name: str
    kind: str
    label: str
    polygon: tuple[geometry.Point, ...] = ()
    circle: tuple[float, float, float] | None = None
    region: str | None = None
    zone: str | None = None
    slot: int | None = None
    room: str | None = None


@dataclass(frozen=True)
class GraphPointSpec:
    """One PLAN graph point besides the zone slots (which come from the slot segments)."""
    name: str
    kind: str
    label: str
    segment: str | None = None       # region for "inside" judgement, reused from traffic.segments
    anchor: geometry.Point | None = None
    robot: str | None = None         # dock owner


@dataclass(frozen=True)
class GraphCfg:
    """Stage 4O PLAN: point graph, timing and cost settings (traffic.graph)."""
    cell_m: float = 0.5
    plan_speed_mps: float | None = None   # None = mock.speed_mps
    plan_step_s: float = 0.5
    level_weights: Mapping[int, float] = field(default_factory=lambda: {1: 1.0, 2: 3.0, 3: 9.0})
    idle_weight: float = 0.5
    replan_delay_s: float = 5.0
    plan_budget_ms: float = 200.0
    plan_limit_ms: float = 200.0           # joint search; over it -> Level order (inside the 10 Hz tick)
    aside_candidates: int = 4             # slots nearest to the robot offered as a move aside at plan start
    move_start_s: float = 1.0             # from a new MOVE (or CANCEL then MOVE) until the robot drives
    corridor: tuple[str, ...] = ()
    points: Mapping[str, GraphPointSpec] = field(default_factory=dict)


@dataclass(frozen=True)
class TrafficCfg:
    stop_margin_m: float = 0.8
    segments: Mapping[str, SegmentSpec] = field(default_factory=dict)
    links: tuple[tuple[str, str], ...] = ()
    graph: GraphCfg = field(default_factory=GraphCfg)


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
    supports_request_id: bool = False
    localizing_s: float = 3.0
    report_rejections: bool = False
    abort_after_s: float = 5.0


@dataclass(frozen=True)
class FleetConfig:
    robots: Mapping[str, RobotCfg]
    type_to_robot: Mapping[str, str]
    robot_link: RobotLinkCfg
    zones: Mapping[str, geometry.Point]
    corridor: CorridorCfg
    resources: ResourcesCfg
    stall: StallCfg
    safety: SafetyCfg
    mission: MissionCfg
    publish: PublishCfg
    db_path: str
    mock: MockCfg = MockCfg()
    obstacle: ObstacleCfg = field(default_factory=ObstacleCfg)
    assignment: AssignmentCfg = AssignmentCfg()
    zone_names: Mapping[str, str] = field(default_factory=dict)
    slots: Mapping[str, tuple[geometry.Point, ...]] = field(default_factory=dict)

    exit_blocked_zones: frozenset[str] = frozenset()
    traffic: TrafficCfg = field(default_factory=TrafficCfg)

    def slot_point(self, zone: str, slot: int | None = 1) -> geometry.Point:
        """Slot 1 is the zone point, slot 2 is zones.<z>.slot2."""
        points = self.slots[zone]
        return points[slot - 1] if slot and slot <= len(points) else points[0]

    def exit_clearance(self, zone):
        """Distance from slot 2 to the finite path between slot 1 and its nearest entrance."""
        start = self.slot_point(zone)
        entrance = min(self.corridor.mouths.values(), key=lambda point: dist(start, point))
        return geometry.segment_distance(self.slot_point(zone, 2), start, entrance)

    def exit_warnings(self):
        threshold = self.safety.slot_stop_m + 0.10
        warnings = []
        for zone in self.zones:
            if zone in self.exit_blocked_zones:
                continue
            gap = self.exit_clearance(zone)
            if gap < threshold:
                warnings.append(f"{self.zone_names.get(zone, zone)} 출구 경로와 2번 자리 간격 {gap:.2f} m "
                                f"(기준 {threshold:.2f} m 미만). slot2_blocks_exit 설정을 확인하세요.")
        return warnings

    def slot_count(self, zone: str) -> int:
        return len(self.slots[zone])


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
    crossroad = _point(_mapping(data, "corridor", ("crossroad",))["crossroad"], "corridor.crossroad")
    if geometry.region_of(crossroad, vertices, include_boundary=include_boundary) != geometry.CORRIDOR:
        raise ConfigError("corridor.crossroad: 복도 안에 있어야 합니다")
    return CorridorCfg(
        crossroad=crossroad,
        crossroad_clear_m=_number(data.get("crossroad_clear_m", CorridorCfg.crossroad_clear_m),
                                  "corridor.crossroad_clear_m", positive=True),
        **_distance_limits(data, "corridor", "follow_gap_m", "follow_resume_m", CorridorCfg),
        polygon=tuple(vertices),
        mouths=points,
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
    retries = data.get("control_retries", RobotLinkCfg.control_retries)
    if type(retries) is not int or retries < 0:
        raise ConfigError("robot_link.control_retries: 0 이상의 정수가 필요합니다")
    return RobotLinkCfg(
        mode=_text(data.get("mode"), "robot_link.mode", ("HOSPITAL_NAV",)),
        control_retry_s=_number(data.get("control_retry_s", RobotLinkCfg.control_retry_s),
                                "robot_link.control_retry_s", positive=True),
        control_retries=retries,
        request_id=_text(data.get("request_id"), "robot_link.request_id", ("auto", "always", "never")),
        start_timeout_s=_number(data.get("start_timeout_s", 120.0), "robot_link.start_timeout_s", positive=True),
        command_grace_s=_number(data.get("command_grace_s", RobotLinkCfg.command_grace_s),
                                "robot_link.command_grace_s", minimum=0),
        **topics,
        **_numbers(data, "robot_link", ("accept_timeout_s", "link_timeout_s"), positive=True),
    )


def _load_traffic(data: Mapping | None, zones: Mapping) -> TrafficCfg:
    raw = _mapping(data, "traffic", ("segments", "links", "graph"))
    stop_margin = _number(raw.get("stop_margin_m", TrafficCfg.stop_margin_m), "traffic.stop_margin_m", positive=True)
    seg_map = _mapping(raw["segments"], "traffic.segments")
    segments = {}
    for name, spec_raw in seg_map.items():
        _text(name, "traffic.segments.<id>")
        path = f"traffic.segments.{name}"
        spec = _mapping(spec_raw, path)
        kind = _text(spec.get("kind"), f"{path}.kind", SEGMENT_KINDS)
        label = _text(spec.get("label", name), f"{path}.label")
        polygon = ()
        if spec.get("polygon") is not None:
            pts_raw = _value(spec, "polygon", path, list)
            if len(pts_raw) < 3:
                raise ConfigError(f"{path}.polygon: 꼭짓점이 3개 이상 필요합니다")
            pts = []
            for i, pair in enumerate(pts_raw):
                p_path = f"{path}.polygon[{i}]"
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ConfigError(f"{p_path}: [x, y] 좌표가 필요합니다")
                pts.append((_number(pair[0], f"{p_path}.x"), _number(pair[1], f"{p_path}.y")))
            poly = Polygon(pts)
            if not poly.is_valid or poly.area <= 0:
                raise ConfigError(f"{path}.polygon: 유효한 다각형이 필요합니다")
            polygon = tuple(pts)
        circle = None
        if spec.get("circle") is not None:
            c = _mapping(spec["circle"], f"{path}.circle")
            circle = (
                _number(c.get("x"), f"{path}.circle.x"),
                _number(c.get("y"), f"{path}.circle.y"),
                _number(c.get("r"), f"{path}.circle.r", positive=True),
            )
        if polygon and circle:
            raise ConfigError(f"{path}: polygon과 circle 중 하나만 지정하세요")
        if kind != "shared" and not polygon and circle is None:
            raise ConfigError(f"{path}: {kind} 칸에는 polygon 또는 circle이 필요합니다")
        region = _text(spec["region"], f"{path}.region", geometry.REGIONS) if spec.get("region") is not None else None
        zone = _text(spec["zone"], f"{path}.zone", zones) if spec.get("zone") is not None else None
        slot = None
        if spec.get("slot") is not None:
            slot = _value(spec, "slot", path, int)
            if slot not in (1, 2):
                raise ConfigError(f"{path}.slot: 1 또는 2가 필요합니다")
        room = _text(spec["room"], f"{path}.room", geometry.ROOMS) if spec.get("room") is not None else None
        segments[name] = SegmentSpec(
            name=name, kind=kind, label=label, polygon=polygon, circle=circle,
            region=region, zone=zone, slot=slot, room=room,
        )
    raw_links = raw["links"]
    if not isinstance(raw_links, list):
        raise ConfigError("traffic.links: 목록이 필요합니다")
    links = []
    for i, pair in enumerate(raw_links):
        l_path = f"traffic.links[{i}]"
        if not isinstance(pair, list) or len(pair) != 2:
            raise ConfigError(f"{l_path}: [칸1, 칸2] 쌍이 필요합니다")
        a = _text(pair[0], f"{l_path}[0]")
        b = _text(pair[1], f"{l_path}[1]")
        if a == b:
            raise ConfigError(f"{l_path}: 같은 칸을 연결할 수 없습니다")
        if a not in segments or b not in segments:
            raise ConfigError(f"{l_path}: 정의되지 않은 칸이 포함됐습니다 ({a}, {b})")
        links.append((a, b))
    graph = _load_graph(raw["graph"], segments)
    return TrafficCfg(stop_margin_m=stop_margin, segments=segments, links=tuple(links), graph=graph)


def _load_graph(data: Mapping, segments: Mapping) -> GraphCfg:
    raw = _mapping(data, "traffic.graph")
    numbers = {}
    for key in ("cell_m", "plan_step_s", "idle_weight", "replan_delay_s", "plan_budget_ms", "plan_limit_ms",
                "move_start_s"):
        if raw.get(key) is not None:
            numbers[key] = _number(raw[key], f"traffic.graph.{key}", positive=True)
    speed = raw.get("plan_speed_mps")
    if speed is not None:
        speed = _number(speed, "traffic.graph.plan_speed_mps", positive=True)
    weights = dict(GraphCfg().level_weights)
    if raw.get("level_weights") is not None:
        for level, value in _mapping(raw["level_weights"], "traffic.graph.level_weights").items():
            if type(level) is not int or not 1 <= level <= 3:
                raise ConfigError("traffic.graph.level_weights: 키는 Level 1~3 정수입니다")
            weights[level] = _number(value, f"traffic.graph.level_weights.{level}", positive=True)
    if raw.get("aside_candidates") is not None:
        numbers["aside_candidates"] = _value(raw, "aside_candidates", "traffic.graph", int)
        if numbers["aside_candidates"] < 0:
            raise ConfigError("traffic.graph.aside_candidates: 0 이상 정수가 필요합니다")
    corridor = tuple(raw.get("corridor") or ())
    for name in corridor:
        spec = segments.get(name)
        if spec is None or spec.kind != "one_way" or not spec.polygon:
            raise ConfigError(f"traffic.graph.corridor: {name}은 다각형 one_way 칸이어야 합니다")
    points = {}
    for name, spec_raw in _mapping(raw.get("points") or {}, "traffic.graph.points").items():
        path = f"traffic.graph.points.{name}"
        spec = _mapping(spec_raw, path)
        kind = _text(spec.get("kind"), f"{path}.kind", POINT_KINDS)
        segment = spec.get("segment")
        if segment is not None and segment not in segments:
            raise ConfigError(f"{path}.segment: 칸 지도에 없는 칸입니다 ({segment})")
        if kind != "dock" and segment is None:
            raise ConfigError(f"{path}.segment: 영역으로 쓸 칸이 필요합니다")
        anchor = _point(spec, path) if spec.get("x") is not None else None
        robot = _text(spec["robot"], f"{path}.robot") if spec.get("robot") is not None else None
        if kind == "dock" and robot is None:
            raise ConfigError(f"{path}.robot: 도크 주인 로봇이 필요합니다")
        label = _text(spec.get("label") or (segments[segment].label if segment else name), f"{path}.label")
        points[name] = GraphPointSpec(name=name, kind=kind, label=label, segment=segment, anchor=anchor, robot=robot)
    return GraphCfg(plan_speed_mps=speed, level_weights=weights, corridor=corridor, points=points, **numbers)


def _with_slot_links(traffic: TrafficCfg, slots: Mapping, exit_blocked: set) -> TrafficCfg:
    """Slot links of the segment map from zones.<z>.slot2_blocks_exit (4O follow-up 5, D-60): true = hall -> slot 2
    -> slot 1 (slot 2 is in the way out of slot 1), false = hall -> slot 1 and hall -> slot 2. The hall is the
    shared segment of the zone's region. traffic.links holds the other links; a slot link written there anyway must
    be one of these."""
    specs = traffic.segments
    made = []
    for zone in slots:
        region = geometry.ZONE_REGIONS[zone]
        hall = next((n for n, s in specs.items() if s.kind == "shared" and s.region == region), None)
        slot = {s.slot: n for n, s in specs.items() if s.zone == zone and s.slot is not None}
        if hall is None or 1 not in slot or 2 not in slot:
            continue                                     # _check_segment_map names what is missing
        if zone in exit_blocked:
            made += [(hall, slot[2]), (slot[2], slot[1])]
        else:
            made += [(hall, slot[1]), (hall, slot[2])]
    made_set = {frozenset(pair) for pair in made}
    slot_names = {n for n, s in specs.items() if s.zone is not None and s.slot is not None}
    kept = []
    for a, b in traffic.links:
        if {a, b} & slot_names:
            if frozenset((a, b)) not in made_set:
                raise ConfigError(f"traffic.links: {a}–{b} 연결이 zones.*.slot2_blocks_exit와 맞지 않습니다 "
                                  "(자리 연결은 slot2_blocks_exit에서 만듭니다)")
            continue
        if (a, b) not in kept and (b, a) not in kept:
            kept.append((a, b))
    return replace(traffic, links=tuple(kept) + tuple(made))


def _check_segment_map(traffic: TrafficCfg, slots: Mapping) -> None:
    """The plan reads its map only from fleet.yaml: every region and every zone slot needs a segment (4O)."""
    if not traffic.segments or not traffic.links:
        raise ConfigError("traffic.segments·traffic.links: 둘 다 필요합니다")
    specs = traffic.segments.values()
    for region in (geometry.TOP, geometry.BOTTOM, *geometry.ROOMS):
        if not any(s.kind == "shared" and s.region == region for s in specs):
            raise ConfigError(f"traffic.segments: {region} 영역의 shared 칸이 필요합니다")
    for zone, points in slots.items():
        for slot in range(1, len(points) + 1):
            if not any(s.zone == zone and s.slot == slot for s in specs):
                raise ConfigError(f"traffic.segments: {zone} 구역 {slot}번 자리 칸이 필요합니다")
    linked = {name for pair in traffic.links for name in pair}
    for name in traffic.segments:
        if name not in linked:
            raise ConfigError(f"traffic.links: {name} 칸이 어디에도 연결되지 않았습니다")
    if not traffic.graph.corridor:
        raise ConfigError("traffic.graph.corridor: 나눌 복도 칸이 필요합니다")


# Settings of the LEGACY traffic judgement, gone with it in the 4O 정리 (tag 4o-dual-mode keeps them). A file
# that still has one is refused, so an old fleet.yaml is not read as if it meant something.
REMOVED_KEYS = {
    "traffic": ("mode",),
    "corridor": ("mode", "yield_enabled", "yield_trigger_m", "yield_release_m", "yield_timeout_s", "room_mode"),
    "resources": ("decision_window_s", "aging_s", "zone_arrival_clear_m"),
    "mission": ("return_after_cancel",),
}
REMOVED_SECTIONS = ("deadlock",)     # wait-for cycle cards (LEGACY's last safety net; never opened under PLAN)


def _reject_removed(root: Mapping) -> None:
    for section in REMOVED_SECTIONS:
        if section in root:
            raise ConfigError(f"{section}: 없어진 설정입니다 (교통 판단은 PLAN 하나, 4O 정리)")
    for section, keys in REMOVED_KEYS.items():
        for key in keys:
            if isinstance(root.get(section), Mapping) and key in root[section]:
                raise ConfigError(f"{section}.{key}: 없어진 설정입니다 (교통 판단은 PLAN 하나, 4O 정리)")


def config_from_dict(data: Mapping) -> FleetConfig:
    """Validate a decoded YAML mapping and its cross-references."""
    sections = ("robots", "type_to_robot", "robot_link", "zones", "corridor", "resources",
                "stall", "safety", "mission", "publish", "db")
    root = _mapping(data, "fleet", sections)
    cfg = {key: _mapping(root[key], key) for key in sections}
    _reject_removed(root)
    corridor = _load_corridor(cfg["corridor"])

    zone_data = _mapping(cfg["zones"], "zones", geometry.ZONE_REGIONS)
    zones, slots = {}, {}
    exit_blocked_zones = set()
    for zone, expected_region in geometry.ZONE_REGIONS.items():
        point = _point(zone_data[zone], f"zones.{zone}")
        _mapping(zone_data[zone], f"zones.{zone}", ("slot2", "slot2_blocks_exit"))
        points = [point, _point(zone_data[zone]["slot2"], f"zones.{zone}.slot2")]
        for index, xy in enumerate(points, 1):
            region = corridor.region_of(xy)
            if region != expected_region:
                where = f"zones.{zone}" + (".slot2" if index == 2 else "")
                raise ConfigError(f"{where}: {expected_region}에 속해야 합니다 (현재 {region})")
        zones[zone] = point
        slots[zone] = tuple(points)
        if _value(zone_data[zone], "slot2_blocks_exit", f"zones.{zone}", bool):
            exit_blocked_zones.add(zone)

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
        raw_dock = robot.get("dock")
        dock = None
        if raw_dock is not None:
            xy = _point(raw_dock, f"{path}.dock")
            dock = DockPose(*xy, _number(raw_dock.get("yaw_deg"), f"{path}.dock.yaw_deg"))
        robots[robot_id] = RobotCfg(
            dock=dock,
            mark=mark,
            label=_text(robot.get("label"), f"{path}.label"),
            ns=ns,
            role=_text(robot.get("role"), f"{path}.role", MISSION_TYPES),
            return_to=_text(robot.get("return_to", "ZONE"), f"{path}.return_to", ("ZONE", "DOCK")),
            start_zone=_text(robot.get("start_zone"), f"{path}.start_zone", zones),
        )

    role_map = {"CALL": cfg["type_to_robot"].get("MED_ROUND"), **cfg["type_to_robot"]}
    type_to_robot = {
        mission_type: _text(role_map.get(mission_type), f"type_to_robot.{mission_type}", robots)
        for mission_type in MISSION_TYPES
    }

    stall = StallCfg(**_numbers(cfg["stall"], "stall", ("window_s", "move_m", "short_s", "long_s"),
                                positive=True))
    if stall.short_s >= stall.long_s:
        raise ConfigError("stall.long_s: stall.short_s보다 커야 합니다")

    targets = _value(cfg["safety"], "bed_exit_pause_targets", "safety", list)
    policy = {**asdict(BedMonitorCfg()), **_mapping(cfg["safety"].get("bed_monitor", {}), "safety.bed_monitor")}
    monitor = BedMonitorCfg(**_numbers(policy, "safety.bed_monitor", ("warn_s", "lost_s", "recover_s", "min_rate_hz"),
                                       positive=True),
                            exit_clear_hold_s=_number(policy["exit_clear_hold_s"], "safety.bed_monitor.exit_clear_hold_s", minimum=0),
                            require_at_start=_value(policy, "require_at_start", "safety.bed_monitor", bool),
                            enabled=_value(policy, "enabled", "safety.bed_monitor", bool))
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
    mission = {"return_to_dock": False, "return_home": True, "return_interruptible": True, "return_level": 1, **cfg["mission"]}
    return_level = _value(mission, "return_level", "mission", int)
    if not 1 <= return_level <= 3:
        raise ConfigError("mission.return_level: 1~3 정수가 필요합니다")
    traffic = _with_slot_links(_load_traffic(root.get("traffic"), zones), slots, exit_blocked_zones)
    _check_segment_map(traffic, slots)
    values = {**asdict(ObstacleCfg()), **_mapping(root.get("obstacle", {}), "obstacle")}
    threshold = _number(values["min_confidence"], "obstacle.min_confidence", minimum=0)
    if threshold > 1:
        raise ConfigError("obstacle.min_confidence: 0~1 범위가 필요합니다")
    obstacle = ObstacleCfg(**{k: _topic(values, k, "obstacle")
                              for k in ("inspect_topic", "result_topic", "image_topic", "marked_image_topic")},
                           **_numbers(values, "obstacle", ("inspect_at_s", "timeout_s", "report_wait_s", "retry_delay_s"),
                                      positive=True),
                           enabled=_value(values, "enabled", "obstacle", bool),
                           mode=_text(values["mode"], "obstacle.mode", OBSTACLE_MODES),
                           retry_if_clear=_value(values, "retry_if_clear", "obstacle", bool),
                           none_text=_text(values["none_text"], "obstacle.none_text"),
                           object_blocks_now=_value(values, "object_blocks_now", "obstacle", bool),
                           min_confidence=threshold, label_ko={_text(k, "obstacle.label_ko"): _text(v, "obstacle.label_ko")
                               for k, v in _mapping(values["label_ko"], "obstacle.label_ko").items()})
    limit = cfg["publish"].get("finished_mission_limit", PublishCfg.finished_mission_limit)
    if type(limit) is not int or limit < 0:
        raise ConfigError("publish.finished_mission_limit: 0 이상의 정수가 필요합니다")
    return FleetConfig(obstacle=obstacle,
        assignment=AssignmentCfg(_text(assignment["mode"], "assignment.mode", ("IDLE_BEST_BATTERY", "ROLE")), minimum),
        mock=MockCfg(**_numbers(mock, "mock", ("speed_mps", "stall_radius_m", "abort_after_s"), positive=True),
                     localizing_s=_number(mock["localizing_s"], "mock.localizing_s", minimum=0),
                     report_rejections=_value(mock, "report_rejections", "mock", bool),
                     supports_request_id=_value(mock, "supports_request_id", "mock", bool)),
        robots=robots,
        type_to_robot=type_to_robot,
        robot_link=_load_robot_link(cfg["robot_link"]),
        zones=zones,
        zone_names={z: zone_display_name(z, zone_data[z].get("name")) for z in zones},
        slots=slots, exit_blocked_zones=frozenset(exit_blocked_zones),
        corridor=corridor,
        resources=ResourcesCfg(
            zone_occupied_m=_number(cfg["resources"].get("zone_occupied_m", ResourcesCfg.zone_occupied_m),
                                   "resources.zone_occupied_m", positive=True),
            **_numbers(cfg["resources"], "resources", ("clear_margin_m", "clear_hold_s")),
        ),
        stall=stall,
        safety=SafetyCfg(
            bed_exit_pause_targets=tuple(_text(r, "safety.bed_exit_pause_targets", robots) for r in targets),
            bed_monitor=monitor,
            use_hazard_topic=_value({"use_hazard_topic": False, **cfg["safety"]}, "use_hazard_topic", "safety", bool),
            bump_neighbor_m=_number(cfg["safety"].get("bump_neighbor_m", SafetyCfg.bump_neighbor_m),
                                    "safety.bump_neighbor_m", minimum=0),
            slot_stop_m=_number(cfg["safety"].get("slot_stop_m", SafetyCfg.slot_stop_m),
                                "safety.slot_stop_m", positive=True),
            **_distance_limits(cfg["safety"], "safety", "proximity_stop_m", "proximity_resume_m", SafetyCfg),
        ),
        mission=MissionCfg(
            return_home=_value(mission, "return_home", "mission", bool),
            return_interruptible=_value(mission, "return_interruptible", "mission", bool),
            return_level=return_level,
            manual_level=manual_level,
            retry_after_return=retry_after_return,
            confirm_at_stops=_value(cfg["mission"], "confirm_at_stops", "mission", bool),
            return_to_dock=_value(mission, "return_to_dock", "mission", bool),
        ),
        publish=PublishCfg(finished_mission_limit=limit, **_numbers(cfg["publish"], "publish", ("fleet_state_hz", "tick_hz"),
                                      positive=True)),
        db_path=expanduser(_text(cfg["db"].get("path"), "db.path")),
        traffic=traffic,
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
