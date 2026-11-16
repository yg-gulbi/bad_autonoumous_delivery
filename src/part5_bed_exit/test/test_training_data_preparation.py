import numpy as np

from research.datasets.prepare_training_data import (
    convert_roboflow_label_lines,
    lsp_frame_to_yolo_pose,
)


def test_roboflow_state_boxes_merge_to_person_and_bed_is_preserved():
    rows = convert_roboflow_label_lines([
        "0 0.5 0.5 0.2 0.4",
        "1 0.4 0.5 0.5 0.8",
        "2 0.5 0.5 0.2 0.3",
    ])
    assert [row.split()[0] for row in rows] == ["0", "1", "0"]


def test_lsp_pose_maps_twelve_limbs_and_leaves_coco_face_absent():
    joints = np.zeros((3, 16), dtype=float)
    for index in range(12):
        joints[0, index] = 100 + index * 10
        joints[1, index] = 200 + index * 5
    joints[2, 0] = 1  # LSP invisibility -> YOLO occluded visibility 1.

    fields = lsp_frame_to_yolo_pose(joints, width=400, height=400).split()
    assert len(fields) == 1 + 4 + 17 * 3
    keypoints = np.asarray(fields[5:], dtype=float).reshape(17, 3)
    assert np.all(keypoints[:5] == 0)
    assert np.count_nonzero(keypoints[:, 2]) == 12
    assert keypoints[16, 2] == 1
    assert keypoints[14, 2] == 2
