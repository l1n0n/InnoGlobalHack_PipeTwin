"""Шаг 6: обучение на всех переходах, прогноз для TARGET_IDS -> result.csv и reports/."""
import numpy as np
import pandas as pd

from config import CACHE, REPORTS, ROOT, TARGET_IDS, WT_NOM
from segment import segment_grid
from train import blend, fit_catboost, predict_interval, predict_thk, widen


def main():
    f = pd.read_parquet(CACHE / "features.parquet")
    lab = f[f["is_predict"] == 0]
    tgt = f[(f["is_predict"] == 1) & f["id"].isin(TARGET_IDS)].sort_values(["id", "segment_id"])

    cat = np.clip(predict_thk(fit_catboost(lab), tgt), 0.01, WT_NOM)
    alpha = float((CACHE / "blend_alpha.txt").read_text())  # выбран LOSO-валидацией в train.py
    p50 = np.clip(blend(tgt["thk_prev"].values, cat, alpha), 0.01, WT_NOM)
    # интервалы: квантильный CatBoost, расширенный вокруг p50 CatBoost, затем обязательно содержит итоговый p50
    lo, hi = predict_interval(lab, tgt)
    lo, hi = widen(cat, lo, hi, float((CACHE / "interval_k.txt").read_text()))  # калибровка на валидации
    lo, hi = np.clip(np.minimum(lo, p50), 0.01, WT_NOM), np.clip(np.maximum(hi, p50), 0.01, WT_NOM)

    res = pd.DataFrame({"id": tgt["id"].values, "segment_id": tgt["segment_id"].values,
                        "thickness_pred": p50.round(4)})

    grid = segment_grid()
    grid = grid[grid["id"].isin(TARGET_IDS)]
    assert list(res.columns) == ["id", "segment_id", "thickness_pred"]
    assert res["thickness_pred"].notna().all()
    assert not res.duplicated(["id", "segment_id"]).any()
    assert len(res) == len(grid) and set(map(tuple, res[["id", "segment_id"]].values)) == \
        set(map(tuple, grid[["id", "segment_id"]].values))
    assert ((res["thickness_pred"] > 0) & (res["thickness_pred"] <= WT_NOM)).all()
    res.to_csv(ROOT / "result.csv", index=False, float_format="%.4f")

    REPORTS.mkdir(exist_ok=True)
    iv = res.rename(columns={"thickness_pred": "p50"}).assign(p10=lo.round(4), p90=hi.round(4),
                                                             thk_prev=tgt["thk_prev"].values,
                                                             p50_catboost=cat.round(4))
    iv[["id", "segment_id", "thk_prev", "p10", "p50", "p90", "p50_catboost"]].to_csv(REPORTS / "intervals.csv", index=False)
    n_top = int(np.ceil(0.1 * len(res)))
    res.nsmallest(n_top, "thickness_pred").to_csv(REPORTS / "top10_dangerous.csv", index=False)

    print(f"result.csv: {len(res)} rows, blend alpha={alpha}; pred mean={p50.mean():.3f}, min={p50.min():.3f}; "
          f"thk_prev mean={tgt['thk_prev'].mean():.3f}")
    print(f"top10_dangerous: {n_top} segments, thk <= {res.nsmallest(n_top, 'thickness_pred')['thickness_pred'].max():.3f}")


if __name__ == "__main__":
    main()
