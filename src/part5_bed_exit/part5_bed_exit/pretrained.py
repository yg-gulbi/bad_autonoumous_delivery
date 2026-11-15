"""One factory for the notebook, comparison runner, and ROS node."""

import json
import math
from pathlib import Path

import numpy as np

from part5_bed_exit.methods.bed_exit_core import MODEL_FILES, PolygonROI, make_method


def read_config(path):
    """Resolve model paths relative to the JSON, never the launch directory."""
    path = Path(path).expanduser().resolve()
    config = json.loads(path.read_text(encoding='utf-8'))
    if config.get('roi_reviewed') is not True:
        raise ValueError('Review the camera ROI and set roi_reviewed=true before inference')
    if not config.get('roi_version'):
        raise ValueError('roi_version is required')
    bed, safe = PolygonROI(config['bed_roi']), PolygonROI(config['safe_roi'])
    if np.any(safe.mask((1000, 1000)) & ~bed.mask((1000, 1000))):
        raise ValueError('Safe ROI must be inside Bed ROI')
    settings = config.get('method_settings', {})
    if settings.get('policy', 'any_body') not in ('any_body', 'torso'):
        raise ValueError('policy must be any_body or torso')
    for key, default in (('outside_fraction', 0.05), ('point_conf', 0.5)):
        value = settings.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError(f'{key} must be a number in [0, 1]')
    tolerance = settings.get('tolerance_px', 3.0)
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError('tolerance_px must be finite and nonnegative')
    model_dir = Path(config['model_dir']).expanduser()
    if not model_dir.is_absolute():
        model_dir = path.parent / model_dir
    return config, bed, safe, model_dir.resolve()


def create_method(name, config_path):
    """Create one raw-frame strategy; voting remains in the caller."""
    if name not in MODEL_FILES:
        raise ValueError(f'Unknown method {name!r}; choose from {tuple(MODEL_FILES)}')
    config, bed, safe, model_dir = read_config(config_path)
    return make_method(name, bed, safe, model_dir,
                       device=config.get('device', 'cpu'),
                       initial_bbox=config.get('initial_patient_bbox'),
                       **config.get('method_settings', {}))
