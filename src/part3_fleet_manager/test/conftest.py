from pathlib import Path

import pytest

from part3_fleet_manager.core.config import load_config


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "fleet.yaml"


@pytest.fixture
def cfg():
    return load_config(CONFIG_PATH)
