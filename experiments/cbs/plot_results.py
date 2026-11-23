"""Generate 3 comparison charts in experiments/cbs/results/ from compare_all.py outputs.

Usage:
    MPLCONFIGDIR=/tmp/mpl python3 experiments/cbs/plot_results.py \
        --raw /tmp/cbs_compare_raw.json \
        --summary experiments/cbs/results/summary.json \
        --out-dir experiments/cbs/results
"""

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import font_manager as fm
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np


METHODS = ["joint", "cbs", "cbs_strict", "cbs_disjoint"]
LABELS = {
    "joint": "Joint A*",
    "cbs": "CBS (순간 제약)",
    "cbs_strict": "CBS-Strict (범위 제약)",
    "cbs_disjoint": "CBS-Disjoint (개선)",
}
SHORT_LABELS = {
    "joint": "Joint A*",
    "cbs": "cbs\n(순간)",
    "cbs_strict": "cbs_strict\n(범위)",
    "cbs_disjoint": "cbs_disjoint\n(분할)",
}
COLORS = {
    "joint": "#2563eb",
    "cbs": "#f59e0b",
    "cbs_strict": "#dc2626",
    "cbs_disjoint": "#059669",
}


def setup_style():
    families = []
    for p in (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    ):
        if Path(p).exists():
            fm.fontManager.addfont(p)
            name = fm.FontProperties(fname=p).get_name()
            if name not in families:
                families.append(name)
    if not families:
        families = ["DejaVu Sans"]
    else:
        families.append("DejaVu Sans")
    plt.rcParams["font.family"] = families
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.facecolor"] = "white"
    plt.rcParams["axes.facecolor"] = "#fafafa"
    plt.rcParams["axes.grid"] = True
    plt.rcParams["grid.alpha"] = 0.35
    plt.rcParams["grid.linestyle"] = "--"


def plot_plan_time(raw, summary, out_path: Path):
    limits = [k for k in ("200", "1000") if k in raw["limits"]]
    fig, axes = plt.subplots(2, len(limits), figsize=(13.5, 8.6), squeeze=False)
    fig.suptitle("계획 시간 분포 비교 (1,080건: 자리 쌍 × Level 9조합)", fontsize=14, fontweight="bold", y=0.98)

    for col, lim in enumerate(limits):
        rows = raw["limits"][lim]
        lim_val = float(lim)

        # Top row: Log-scale boxplot + mean/P95 markers
        ax = axes[0, col]
        data = [[max(0.05, r[m]["ms"]) for r in rows] for m in METHODS]
        positions = np.arange(len(METHODS))
        bp = ax.boxplot(
            data,
            positions=positions,
            widths=0.52,
            patch_artist=True,
            showfliers=True,
            flierprops={"marker": ".", "markersize": 2.5, "alpha": 0.25, "color": "#475569"},
            medianprops={"color": "#0f172a", "linewidth": 1.8},
            whiskerprops={"linewidth": 1.2, "color": "#475569"},
            capprops={"linewidth": 1.2, "color": "#475569"},
        )
        for patch, m in zip(bp["boxes"], METHODS):
            patch.set_facecolor(COLORS[m])
            patch.set_alpha(0.55)
            patch.set_edgecolor(COLORS[m])
            patch.set_linewidth(1.5)

        for idx, m in enumerate(METHODS):
            s = summary["limits"][lim]["all"][m]
            ax.scatter([idx], [max(0.05, s["mean_ms"])], color="#0f172a", marker="D", s=36, zorder=5,
                       label="평균" if (col == 0 and idx == 0) else None)
            ax.scatter([idx], [max(0.05, s["p95_ms"])], color="#b91c1c", marker="^", s=46, zorder=5,
                       label="95% (P95)" if (col == 0 and idx == 0) else None)
            ax.text(
                idx,
                min(lim_val * 1.45, max(s["max_ms"], lim_val) * 1.12),
                f"평균 {s['mean_ms']:.1f}\nP95 {s['p95_ms']:.1f}",
                ha="center", va="bottom", fontsize=8.3, color="#1e293b",
                bbox={"boxstyle": "round,pad=0.22", "facecolor": "white", "edgecolor": "#cbd5e1", "alpha": 0.9},
            )

        ax.axhline(lim_val, color="#ef4444", linestyle=":", linewidth=1.3, label=f"시간 한도 ({lim} ms)")
        ax.set_yscale("log")
        ax.set_ylim(0.2, lim_val * 3.4)
        ax.set_xticks(positions)
        ax.set_xticklabels([SHORT_LABELS[m] for m in METHODS], fontsize=9.5)
        ax.set_ylabel("계획 시간 (ms, 로그 스케일)", fontsize=10)
        ax.set_title(f"시간 한도 {lim} ms — 분포 및 평균/P95", fontsize=11.5, fontweight="bold")
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda y, _: f"{y:g}"))
        if col == 0:
            ax.legend(loc="lower right", fontsize=8.5, framealpha=0.92)

        # Bottom row: Empirical CDF (solved fraction vs planning time)
        ax2 = axes[1, col]
        for m in METHODS:
            solved_ms = sorted(r[m]["ms"] for r in rows if r[m]["ok"])
            n_tot = len(rows)
            if solved_ms:
                xs = [0.1] + solved_ms + [lim_val]
                ys = [0.0] + [(i + 1) / n_tot * 100.0 for i in range(len(solved_ms))] + [len(solved_ms) / n_tot * 100.0]
            else:
                xs, ys = [0.1, lim_val], [0.0, 0.0]
            s = summary["limits"][lim]["all"][m]
            ax2.step(
                xs, ys, where="post", linewidth=2.1, color=COLORS[m],
                label=f"{LABELS[m]} ({s['solved']}/{n_tot})",
            )
        ax2.axvline(lim_val, color="#ef4444", linestyle=":", linewidth=1.3)
        ax2.set_xscale("log")
        ax2.set_xlim(0.3, lim_val * 1.15)
        ax2.set_ylim(0, 105)
        ax2.set_xlabel("계획 시간 (ms, 로그 스케일)", fontsize=10)
        ax2.set_ylabel("누적 해결 비율 (%)", fontsize=10)
        ax2.set_title(f"시간 한도 {lim} ms — 누적 해결 곡선 (CDF)", fontsize=11.5, fontweight="bold")
        ax2.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f"{x:g}"))
        ax2.legend(loc="lower right", fontsize=8.5, framealpha=0.92)

    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def plot_cost_gap(raw, summary, out_path: Path):
    limits = [k for k in ("200", "1000") if k in raw["limits"]]
    fig, axes = plt.subplots(2, len(limits), figsize=(14.0, 9.0), squeeze=False)
    fig.suptitle("최적(Joint A* 10s 기준) 대비 비용 차이 분포", fontsize=14, fontweight="bold", y=0.98)

    bucket_specs = [
        ("<0% (느슨한 기준 단축)", "#8b5cf6"),
        ("최적 동일 (0%)", "#10b981"),
        ("0~2% 초과", "#60a5fa"),
        ("2~5% 초과", "#f59e0b"),
        ("5~15% 초과", "#f97316"),
        ("15% 이상 초과", "#dc2626"),
    ]

    for col, lim in enumerate(limits):
        rows = raw["limits"][lim]

        # Top row: Stacked bar of optimality buckets over solved instances
        ax = axes[0, col]
        y_pos = np.arange(len(METHODS))
        counts_pct = np.zeros((len(METHODS), len(bucket_specs)))
        solved_counts = []
        for i, m in enumerate(METHODS):
            pairs = [
                (r[m]["cost"] - r["opt"]["cost"], ((r[m]["cost"] - r["opt"]["cost"]) / r["opt"]["cost"]) * 100.0)
                for r in rows if r[m]["ok"] and r["opt"]["ok"]
            ]
            n_s = len(pairs)
            solved_counts.append(n_s)
            if n_s == 0:
                continue
            bm = sum(ga < -1e-6 for ga, _ in pairs)
            b0 = sum(abs(ga) <= 1e-6 for ga, _ in pairs)
            b1 = sum(ga > 1e-6 and gp <= 2.0 for ga, gp in pairs)
            b2 = sum(ga > 1e-6 and 2.0 < gp <= 5.0 for ga, gp in pairs)
            b3 = sum(ga > 1e-6 and 5.0 < gp <= 15.0 for ga, gp in pairs)
            b4 = sum(ga > 1e-6 and gp > 15.0 for ga, gp in pairs)
            counts_pct[i] = [b * 100.0 / n_s for b in (bm, b0, b1, b2, b3, b4)]

        left = np.zeros(len(METHODS))
        for b_idx, (b_label, b_color) in enumerate(bucket_specs):
            vals = counts_pct[:, b_idx]
            ax.barh(y_pos, vals, left=left, height=0.58, color=b_color, edgecolor="white", linewidth=0.7, label=b_label)
            for i, v in enumerate(vals):
                if v >= 5.8:
                    ax.text(left[i] + v / 2.0, y_pos[i], f"{v:.1f}%", ha="center", va="center",
                            fontsize=8.3, color="white", fontweight="bold")
            left += vals

        ax.set_yticks(y_pos)
        ax.set_yticklabels([f"{LABELS[m]}\n(풀이 {solved_counts[i]})" for i, m in enumerate(METHODS)], fontsize=9.0)
        ax.invert_yaxis()
        ax.set_xlim(0, 100)
        ax.set_xlabel("풀이 성공 건 중 비율 (%)", fontsize=10)
        ax.set_title(f"시간 한도 {lim} ms — 최적 대비 오차 구간별 비율", fontsize=11.5, fontweight="bold")

        # Bottom row: Distribution of strictly suboptimal gaps (>0%) with jitter + overall mean/max annotation
        ax2 = axes[1, col]
        rng = np.random.default_rng(42)
        pos_data = []
        max_y = 5.0
        for idx, m in enumerate(METHODS):
            pos_gaps = [
                ((r[m]["cost"] - r["opt"]["cost"]) / r["opt"]["cost"]) * 100.0
                for r in rows if r[m]["ok"] and r["opt"]["ok"] and (r[m]["cost"] - r["opt"]["cost"]) > 1e-6
            ]
            pos_data.append(pos_gaps)
            if pos_gaps:
                max_y = max(max_y, max(pos_gaps))
                jitter = rng.uniform(-0.18, 0.18, size=len(pos_gaps))
                ax2.scatter(np.full(len(pos_gaps), idx) + jitter, pos_gaps,
                            s=11, alpha=0.45, color=COLORS[m], edgecolors="none", zorder=3)

        non_empty_idx = [i for i, d in enumerate(pos_data) if len(d) >= 2]
        if non_empty_idx:
            bp = ax2.boxplot(
                [pos_data[i] for i in non_empty_idx],
                positions=non_empty_idx,
                widths=0.42,
                patch_artist=True,
                showfliers=False,
                medianprops={"color": "#0f172a", "linewidth": 1.8},
                boxprops={"linewidth": 1.3},
            )
            for patch, i in zip(bp["boxes"], non_empty_idx):
                patch.set_facecolor(COLORS[METHODS[i]])
                patch.set_alpha(0.35)
                patch.set_edgecolor(COLORS[METHODS[i]])

        ylim_top = max(50.0, max_y * 1.32)
        for idx, m in enumerate(METHODS):
            s = summary["limits"][lim]["all"][m]
            base_y = max(pos_data[idx]) if pos_data[idx] else 0.5
            label_y = min(ylim_top * 0.80, base_y + ylim_top * 0.045)
            ax2.text(
                idx, label_y,
                f"초과 {s['worse_count']}건\n평균 {s['cost_diff_mean_pct']:+.2f}%\n최대 {s['cost_diff_max_pct']:+.1f}%",
                ha="center", va="bottom", fontsize=8.0, color="#1e293b",
                bbox={"boxstyle": "round,pad=0.24", "facecolor": "white", "edgecolor": "#cbd5e1", "alpha": 0.92},
            )

        ax2.set_xlim(-0.62, len(METHODS) - 0.38)
        ax2.set_xticks(np.arange(len(METHODS)))
        ax2.set_xticklabels([SHORT_LABELS[m] for m in METHODS], fontsize=9.5)
        ax2.set_ylim(-1.5, ylim_top)
        ax2.set_ylabel("최적 대비 비용 초과율 (%, 초과 건 분포)", fontsize=9.8)
        ax2.set_title(f"시간 한도 {lim} ms — 비용 손실 발생 건 분포 (>0%)", fontsize=11.5, fontweight="bold")

    handles, leg_labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, leg_labels, loc="center", bbox_to_anchor=(0.53, 0.455), ncol=6, fontsize=8.6, framealpha=0.95)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.subplots_adjust(hspace=0.48)
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def plot_unsolved(summary, out_path: Path):
    limits = [k for k in ("200", "1000") if k in summary["limits"]]
    fig, axes = plt.subplots(1, 2, figsize=(13.8, 5.5))
    fig.suptitle("방법별 미해결(시간 초과) 건수 비교 (총 1,080건)", fontsize=13.8, fontweight="bold", y=0.98)

    # Panel 1: Overall unsolved (200 ms vs 1000 ms) across all 4 methods
    ax = axes[0]
    x = np.arange(len(METHODS))
    w = 0.34
    bar_colors = {"200": "#ef4444", "1000": "#3b82f6"}
    offsets = np.linspace(-w / 2, w / 2, len(limits)) if len(limits) > 1 else [0.0]

    max_h = 10
    for li, lim in enumerate(limits):
        vals = [summary["limits"][lim]["all"][m]["unsolved"] for m in METHODS]
        max_h = max(max_h, max(vals))
        bars = ax.bar(x + offsets[li], vals, width=w, color=bar_colors.get(lim, "#64748b"),
                      alpha=0.88, edgecolor="#1e293b", linewidth=0.8, label=f"미해결 ({lim} ms)")
        for idx, (bar, v) in enumerate(zip(bars, vals)):
            m = METHODS[idx]
            fb = summary["limits"][lim]["all"][m]["fallback_prioritized"]
            if m == "joint" and fb > 0:
                ax.bar(bar.get_x() + bar.get_width() / 2.0, fb, width=w, color="none",
                       edgecolor=bar_colors.get(lim, "#64748b"), hatch="///", linewidth=1.2,
                       label=f"Joint 우선순위 대체 ({lim} ms)" if idx == 0 else None)
                ax.text(bar.get_x() + bar.get_width() / 2.0, fb + 14, f"0\n(대체 {fb})",
                        ha="center", va="bottom", fontsize=8.5, fontweight="bold", color="#1e293b")
            else:
                ax.text(bar.get_x() + bar.get_width() / 2.0, v + 14, f"{v}",
                        ha="center", va="bottom", fontsize=9.5, fontweight="bold", color="#0f172a")

    ax.set_xticks(x)
    ax.set_xticklabels([SHORT_LABELS[m] for m in METHODS], fontsize=9.8)
    ax.set_ylim(0, max_h * 1.18)
    ax.set_ylabel("미해결 건수 (개)", fontsize=10.5)
    ax.set_title("전체 4가지 방법 미해결 건수 (200 ms vs 1000 ms)", fontsize=11.5, fontweight="bold")
    ax.legend(loc="upper right", fontsize=9.0, framealpha=0.94)

    # Panel 2: Zoomed breakdown of strict-criterion methods (Joint, cbs_strict, cbs_disjoint) at 200 ms & 1000 ms
    ax2 = axes[1]
    zoom_methods = ["joint", "cbs_strict", "cbs_disjoint"]
    lim_ref = "200" if "200" in summary["limits"] else limits[0]
    lim_s = summary["limits"][lim_ref]
    xz = np.arange(len(zoom_methods))

    l3_asym = [lim_s["level3_asym"][m]["unsolved"] for m in zoom_methods]
    l3_both = [lim_s["level3_both"][m]["unsolved"] for m in zoom_methods]
    non_l3 = [lim_s["non_level3"][m]["unsolved"] for m in zoom_methods]

    ax2.bar(xz, l3_asym, width=0.46, color="#f59e0b", edgecolor="white", label="Level 3 비대칭 (L3 vs L1/L2, 480건)")
    ax2.bar(xz, l3_both, width=0.46, bottom=l3_asym, color="#dc2626", edgecolor="white", label="Level 3 동급 (L3 vs L3, 120건)")
    bottom2 = [a + b for a, b in zip(l3_asym, l3_both)]
    ax2.bar(xz, non_l3, width=0.46, bottom=bottom2, color="#64748b", edgecolor="white", label="Level 3 미포함 (L1/L2, 480건)")

    max_h2 = max(10, max(a + b + c for a, b, c in zip(l3_asym, l3_both, non_l3)))
    for i, m in enumerate(zoom_methods):
        tot = l3_asym[i] + l3_both[i] + non_l3[i]
        l3_tot = l3_asym[i] + l3_both[i]
        if tot > 0:
            ax2.text(xz[i], tot + 0.8, f"총 {tot}건\n(L3 비대칭 {l3_asym[i]} · 동급 {l3_both[i]})",
                     ha="center", va="bottom", fontsize=8.8, fontweight="bold", color="#0f172a")
        else:
            ax2.text(xz[i], 0.8, "0건\n(1,080건 전원 풀이)", ha="center", va="bottom",
                     fontsize=8.8, fontweight="bold", color="#0f172a")

    ax2.set_xticks(xz)
    ax2.set_xticklabels([SHORT_LABELS[m] for m in zoom_methods], fontsize=9.8)
    ax2.set_ylim(0, max_h2 * 1.48)
    ax2.set_ylabel("미해결 건수 (개)", fontsize=10.5)
    ax2.set_title(f"{lim_ref} ms 엄격 기준 방법 확대 — Level 3 분해\n(cbs 순간 제약 894건 중 L3 484건은 좌측 그래프 참고)",
                  fontsize=10.5, fontweight="bold")
    ax2.legend(loc="upper left", fontsize=8.5, framealpha=0.94)

    fig.tight_layout(rect=[0, 0, 1, 0.93])
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", default="/tmp/cbs_compare_raw.json")
    parser.add_argument("--summary", default=str(Path(__file__).resolve().parent / "results/summary.json"))
    parser.add_argument("--out-dir", default=str(Path(__file__).resolve().parent / "results"))
    args = parser.parse_args()

    setup_style()
    raw = json.loads(Path(args.raw).read_text())
    summary = json.loads(Path(args.summary).read_text())
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    plot_plan_time(raw, summary, out_dir / "plan_time_dist.png")
    plot_cost_gap(raw, summary, out_dir / "cost_gap_dist.png")
    plot_unsolved(summary, out_dir / "unsolved_counts.png")
    print(f"Saved 3 PNG charts in {out_dir}")


if __name__ == "__main__":
    main()
