"""What the plan shows: labels for events, and the snapshot parts of /fleet/state (resources, traffic)."""

from . import geometry as g


class PlanView:
    """Mixed into PlanManager. Reads the plan; changes nothing."""

    def robot_label(self, robot):
        return self.config.robots[robot].label

    def zone_label(self, zone, slot=None):
        name = self.config.zone_names.get(zone) or f"{zone} 구역"
        return f"{name} 2번 자리" if slot == 2 else name

    def resource_label(self, name):
        if name == g.CORRIDOR:
            return "복도"
        if name in self.segments:
            return self.segments[name].spec.label
        if name.startswith("ROOM_"):
            return self.zone_label(name.removeprefix("ROOM_")) + " 출입"
        return name

    def endpoint_label(self, name):
        point = self.graph.points[name]
        if point.kind == "slot":
            return self.zone_label(point.zone, point.slot)
        return point.label

    def snapshot(self):
        door_b = (self.map.door_names[g.ROOM_B],) if g.ROOM_B in self.map.door_names else ()
        door_e = (self.map.door_names[g.ROOM_E],) if g.ROOM_E in self.map.door_names else ()
        queue = [{"robot": leg.robot, "level": self.level_of(leg), "mission_id": leg.mission_id, "since": leg.since}
                 for leg in self.pending.values() if leg.robot in self.held]
        return {
            g.CORRIDOR: self.board.aggregate(self.map.corridor_names, 2, queue),
            g.ROOM_B: self.board.aggregate(door_b, 1),
            g.ROOM_E: self.board.aggregate(door_e, 1),
        }

    def traffic_snapshot(self):
        plans = {}
        for robot, plan in self.plans.items():
            points, route = [], []
            for entry in plan.entries:
                if not points or points[-1] != entry.point:
                    points.append(entry.point)
            for leg in plan.legs:
                route.extend([round(x, 3), round(y, 3)] for x, y in leg.line)
            held = self.held.get(robot)
            plans[robot] = {
                "points": points,
                "labels": [self.graph.label(p) for p in points],
                "route": route,
                "targets": [{"point": leg.target, "label": self.endpoint_label(leg.target), "aside": leg.aside}
                            for leg in plan.legs],
                "wait": held["reason"] if held else None,
                "aside": bool(plan.legs and plan.legs[0].aside) or robot in self.aside_moves,
            }
        stop_waits = {}
        for robot, held in self.held.items():
            stop_waits[robot] = {"segment": held["point"], "segment_label": self.graph.label(held["point"]),
                                 "by": held["peer"] or "", "since": held["since"],
                                 "paused": self.robots.view(robot).driving, "reason": held["reason"]}
        return {
            "mode": "PLAN",
            "stop_margin_m": self.config.traffic.stop_margin_m,
            "segments": self.board.view(),
            "stop_line_waits": stop_waits,
            "plans": plans,
            "plan": dict(self.plan_info),
        }
