"""Шаг 7: графики -> reports/*.png."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from config import CACHE, REPORTS, T_DATE, WT_NOM
from train import r2

BLUE, ORANGE, GRAY, INK, INK2, SURF = "#2a78d6", "#eb6834", "#8a8984", "#0b0b0b", "#52514e", "#fcfcfb"
plt.rcParams.update({"figure.facecolor": SURF, "axes.facecolor": SURF, "axes.edgecolor": "#d6d5d0",
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                     "text.color": INK, "axes.grid": True, "grid.color": "#ebeae6", "grid.linewidth": 0.6,
                     "axes.spines.top": False, "axes.spines.right": False, "font.size": 10})


def plot_validation():
    v = pd.read_parquet(CACHE / "val_preds.parquet")
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.6), sharex=True, sharey=True)
    for ax, (key, title) in zip(axes, [("persistence", "Persistence (толщина 2022)"), ("catboost", "CatBoost")]):
        ax.scatter(v[key], v["thk_next"], s=28, color=BLUE, edgecolor=SURF, linewidth=1.5, zorder=3)
        lo, hi = min(v[key].min(), v["thk_next"].min()) - 0.2, WT_NOM + 0.1
        ax.plot([lo, hi], [lo, hi], color=GRAY, lw=1, ls="--", zorder=2)
        ax.set_title(f"{title}: r² = {r2(v['thk_next'], v[key]):.3f}", loc="left", fontsize=11)
        ax.set_xlabel("прогноз, мм")
    axes[0].set_ylabel("факт ВТД 2023-12-01, мм")
    fig.suptitle("Валидация 2022 → 2023 (участки 1250009490, 1250011604, 55 сегментов)",
                 x=0.01, ha="left", fontsize=12)
    fig.tight_layout()
    fig.savefig(REPORTS / "validation_scatter.png", dpi=130)
    plt.close(fig)


def plot_profile():
    iv = pd.read_csv(REPORTS / "intervals.csv")
    for sid, g in iv.groupby("id"):
        x = g["segment_id"] * 10
        fig, ax = plt.subplots(figsize=(13, 4.8))
        ax.fill_between(x, g["p10"], g["p90"], color=BLUE, alpha=0.18, lw=0, step="mid", label="прогноз p10–p90")
        ax.plot(x, g["thk_prev"], color=GRAY, lw=1, drawstyle="steps-mid", label="факт ВТД 2022-07-06")
        ax.plot(x, g["p50"], color=BLUE, lw=2, drawstyle="steps-mid", label=f"прогноз на {T_DATE}")
        ax.axhline(WT_NOM, color=INK2, lw=0.8, ls=":")
        ax.text(x.max(), WT_NOM + 0.05, "номинал 8.0 мм", ha="right", va="bottom", color=INK2, fontsize=9)
        ax.set_xlabel("расстояние от начала участка, м")
        ax.set_ylabel("мин. остаточная толщина, мм")
        ax.set_title(f"Участок {sid}: толщина стенки по 10-м сегментам", loc="left", fontsize=12)
        ax.set_ylim(top=WT_NOM + 0.4)
        ax.margins(x=0.005)
        ax.legend(loc="lower left", frameon=False, ncol=3)
        fig.tight_layout()
        fig.savefig(REPORTS / f"profile_{sid}.png", dpi=130)
        plt.close(fig)


if __name__ == "__main__":
    plot_validation()
    plot_profile()
    print(sorted(p.name for p in REPORTS.glob("*.png")))
