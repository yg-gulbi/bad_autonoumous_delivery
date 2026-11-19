"""Default robot IDs for standalone schema callers, in source and installed layouts."""
from pathlib import Path

import yaml


def default_robot_ids():
    module = Path(__file__).resolve()
    path = module.parents[1] / "config/fleet.yaml"
    if not path.is_file():
        # colcon installs Python modules and share/ under the same package or merged prefix.
        relative = Path("share/part3_fleet_manager/config/fleet.yaml")
        path = next((prefix / relative for prefix in module.parents if (prefix / relative).is_file()), None)
        if path is None:
            raise FileNotFoundError(relative)
    with path.open(encoding="utf-8") as source:
        return tuple(yaml.safe_load(source)["robots"])
