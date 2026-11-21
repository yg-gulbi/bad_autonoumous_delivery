#!/usr/bin/env python3
"""Step 2: YOLO Segmentation 베이스라인 모델을 파인튜닝하고 결과 요약을 저장한다."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Fine-tune a YOLO segmentation baseline model (single-class person).',
    )
    parser.add_argument('--base-model', default='src/part5_bed_exit/models/yolo11n-seg.pt',
                        help='Base .pt model (e.g. yolo11n-seg.pt, yolo11s-seg.pt)')
    parser.add_argument('--data', required=True, help='Path to dataset.yaml')
    parser.add_argument('--project', default='src/part5_bed_exit/finetune/runs/train',
                        help='Output project directory')
    parser.add_argument('--name', required=True, help='Experiment run name')
    parser.add_argument('--epochs', type=int, default=60)
    parser.add_argument('--imgsz', type=int, default=640)
    parser.add_argument('--batch', type=int, default=16)
    parser.add_argument('--device', default='0', help='CUDA device index (0) or cpu')
    parser.add_argument('--freeze', type=int, default=10,
                        help='Number of backbone layers to freeze (0 = full fine-tune)')
    parser.add_argument('--lr0', type=float, default=0.0005)
    parser.add_argument('--optimizer', default='AdamW')
    parser.add_argument('--patience', type=int, default=20)
    parser.add_argument('--close-mosaic', type=int, default=10)
    parser.add_argument('--degrees', type=float, default=15.0)
    parser.add_argument('--scale', type=float, default=0.3)
    parser.add_argument('--fliplr', type=float, default=0.5)
    parser.add_argument('--flipud', type=float, default=0.0)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--seed', type=int, default=42)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    data_yaml = Path(args.data).expanduser().resolve()
    if not data_yaml.is_file():
        raise FileNotFoundError(f'dataset.yaml not found: {data_yaml}')

    project_dir = Path(args.project).expanduser().resolve()
    project_dir.mkdir(parents=True, exist_ok=True)

    from ultralytics import YOLO
    base_path = Path(args.base_model).expanduser()
    if not base_path.is_file() and '/' not in args.base_model:
        cache_dir = Path(__file__).resolve().parent / 'data' / 'pretrained'
        cache_dir.mkdir(parents=True, exist_ok=True)
        base_target = str(cache_dir / args.base_model)
    else:
        base_target = str(base_path.resolve()) if base_path.is_file() else args.base_model
    model = YOLO(base_target, task='segment')

    started_at = datetime.now().astimezone().isoformat()
    results = model.train(
        data=str(data_yaml),
        project=str(project_dir),
        name=args.name,
        exist_ok=False,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        freeze=args.freeze if args.freeze > 0 else None,
        optimizer=args.optimizer,
        lr0=args.lr0,
        cos_lr=True,
        patience=args.patience,
        close_mosaic=args.close_mosaic,
        degrees=args.degrees,
        scale=args.scale,
        hsv_h=0.015,
        hsv_s=0.5,
        hsv_v=0.4,
        fliplr=args.fliplr,
        flipud=args.flipud,
        workers=args.workers,
        seed=args.seed,
        plots=True,
        verbose=True,
    )

    save_dir = Path(results.save_dir).resolve()
    best_pt = save_dir / 'weights' / 'best.pt'
    metrics = getattr(results, 'results_dict', {}) or {}
    summary = {
        'started_at': started_at,
        'finished_at': datetime.now().astimezone().isoformat(),
        'base_model': args.base_model,
        'data': str(data_yaml),
        'save_dir': str(save_dir),
        'best_weights': str(best_pt),
        'hyperparameters': {
            'epochs': args.epochs,
            'imgsz': args.imgsz,
            'batch': args.batch,
            'device': args.device,
            'freeze': args.freeze,
            'lr0': args.lr0,
            'patience': args.patience,
            'close_mosaic': args.close_mosaic,
            'degrees': args.degrees,
            'scale': args.scale,
            'fliplr': args.fliplr,
            'flipud': args.flipud,
            'seed': args.seed,
        },
        'ultralytics_metrics': {
            k: float(v) for k, v in metrics.items()
            if isinstance(v, (int, float))
        },
    }
    (save_dir / 'train_summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    print(f'\n[TRAIN DONE] best.pt = {best_pt}', flush=True)
    print(f'[SUMMARY] {save_dir / "train_summary.json"}', flush=True)


if __name__ == '__main__':
    main()
