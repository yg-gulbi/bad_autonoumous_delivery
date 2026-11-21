#!/usr/bin/env python3
"""Automated runner for Part 5 OFAT, Notion 1.1 proposals, Phase 2 COCO-rot4, and Phase 3 ensembles."""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import subprocess
import sys


def _run(cmd: list[str], cwd: Path) -> None:
    print(f'\n[AUTO RUN] {" ".join(cmd)}', flush=True)
    subprocess.run(cmd, cwd=str(cwd), check=True)


def main():
    parser = argparse.ArgumentParser(description='Run automated Part 5 experiment suite.')
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--with-train', action='store_true',
                        help='Also prepare COCO person rot4 and train ft7 (frz10/frz0) + eval')
    parser.add_argument('--epochs', type=int, default=20)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[3]
    ft_dir = Path(__file__).resolve().parent
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    suite_dir = ft_dir / 'runs' / 'eval' / f'auto_suite_{stamp}'
    suite_dir.mkdir(parents=True, exist_ok=True)
    py = args.python

    # 1) Phase 1: OFAT Post-processing
    _run([py, str(ft_dir / '08_run_phase1_ofat_postproc.py'),
          '--output-dir', str(suite_dir / '01_phase1_ofat')], repo_root)

    # 2) Phase 1: Unexplored <= 0.05 grid sweep
    _run([py, str(ft_dir / '08_run_phase1_ofat_postproc.py'), '--grid-unexplored',
          '--output-dir', str(suite_dir / '02_phase1_grid_unexplored')], repo_root)

    # 3) Notion 1.1 proposals (m compare, Pixel Rot-TTA, ByteTrack, Optical Flow, UNKNOWN)
    _run([py, str(ft_dir / '10_eval_1_1_proposals.py'),
          '--output-dir', str(suite_dir / '03_proposals_1_1')], repo_root)

    # 4) Phase 3: Baseline ensembles (RotTTA, Seg+Pose, SegN+SegM)
    _run([py, str(ft_dir / '09_eval_phase3_ensemble.py'),
          '--output-dir', str(suite_dir / '04_phase3_ensemble_base')], repo_root)

    if args.with_train:
        # 5) Phase 2: COCO person-only rot4 dataset + train freeze=10 & freeze=0
        _run([py, str(ft_dir / '07_prepare_coco_person_seg.py'), '--rot4'], repo_root)
        data_yaml = ft_dir / 'data' / 'coco_person_rot4' / 'dataset.yaml'
        for frz in (10, 0):
            run_name = f'ft7_yolo11n_coco_rot4_frz{frz}_{stamp}'
            _run([
                py, str(ft_dir / '02_train_yolo_seg.py'),
                '--base-model', 'src/part5_bed_exit/models/yolo11n-seg.pt',
                '--data', str(data_yaml),
                '--name', run_name,
                '--epochs', str(args.epochs),
                '--batch', '16',
                '--freeze', str(frz),
                '--lr0', '0.0005',
                '--degrees', '0.0',
                '--fliplr', '0.5',
            ], repo_root)
            best_pt = ft_dir / 'runs' / 'train' / run_name / 'weights' / 'best.pt'
            _run([
                py, str(ft_dir / '03_benchmark_seg_models.py'),
                '--candidate', f'{run_name}={best_pt}',
                '--output-dir', str(suite_dir / f'05_bench_{run_name}'),
            ], repo_root)

    print(f'\n[AUTO SUITE DONE] All results saved under: {suite_dir}')


if __name__ == '__main__':
    main()
