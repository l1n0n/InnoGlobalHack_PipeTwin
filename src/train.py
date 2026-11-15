"""Шаг 5: базовые решения, CatBoost, валидация -> reports/metrics.csv, reports/importance.csv."""
import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from scipy.stats import pearsonr
from sklearn.linear_model import Ridge
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from config import CACHE, REPORTS, SEED
from segment import build_segments

np.random.seed(SEED)

FEATURES = ["thk_prev", "max_depth_prev", "n_defects_prev", "censored_prev",
            "nb_thk_min", "nb_thk_mean", "nb_depth_max", "nb_n_defects", "nb_censored_share",
            "rate_hist", "horizon_years", "segment_id", "rel_pos",
            "z_mean", "slope", "z_above_min100", "is_local_min",
            "reg_watercut", "reg_v_liquid_true", "reg_v_mix", "reg_pit_corrosion_intensity",
            "reg_p_start", "reg_t_mix", "reg_gas_content_rate", "reg_share_not_turbulent", "id"]
CAT = ["id"]
W_2018 = 0.3  # вес переходов 2018->2022 (старый прогон, другой формат отчёта)


def r2(y, p) -> float:
    return float(pearsonr(y, p)[0] ** 2) if np.std(p) > 0 else 0.0


def mae(y, p) -> float:
    return float(np.mean(np.abs(np.asarray(y) - np.asarray(p))))


def loss_target(df: pd.DataFrame, clip: bool = True) -> pd.Series:
    d = df["thk_prev"] - df["thk_next"]
    return d.clip(lower=0) if clip else d


def X_of(df: pd.DataFrame) -> pd.DataFrame:
    X = df[FEATURES].copy()
    X["id"] = X["id"].astype(str)
    return X


def fit_catboost(df: pd.DataFrame, loss: str = "RMSE", iters: int = 500) -> CatBoostRegressor:
    m = CatBoostRegressor(depth=5, iterations=iters, learning_rate=0.03, loss_function=loss,
                          random_seed=SEED, verbose=0, cat_features=CAT,
                          allow_writing_files=False)
    # квантильные модели учим на «сырой» дельте: в ~25 % сегментов следующий прогон толще (шум)
    w = np.where(df["transition"] == "2018->2022", W_2018, 1.0)
    m.fit(X_of(df), loss_target(df, clip=not loss.startswith("Quantile")), sample_weight=w)
    return m


def predict_thk(m, df: pd.DataFrame, clip: bool = True) -> np.ndarray:
    loss = m.predict(X_of(df))
    return df["thk_prev"].values - (np.clip(loss, 0, None) if clip else loss)


def predict_interval(df_train: pd.DataFrame, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    a = predict_thk(fit_catboost(df_train, "Quantile:alpha=0.1"), df, clip=False)
    b = predict_thk(fit_catboost(df_train, "Quantile:alpha=0.9"), df, clip=False)
    return np.minimum(a, b), np.maximum(a, b)


def widen(p50, lo, hi, k: float):
    return p50 - k * np.clip(p50 - lo, 0, None), p50 + k * np.clip(hi - p50, 0, None)


def band(cat, lo, hi, k: float, final=None):
    """p10-p90: квантили CatBoost, расширенные в k раз вокруг p50 CatBoost; затем включают итоговый прогноз."""
    a, b = widen(cat, lo, hi, k)
    return (a, b) if final is None else (np.minimum(a, final), np.maximum(b, final))


def coverage(y, a, b) -> float:
    return float(np.mean((y >= a) & (y <= b)))


def calibrate_k(y, cat, lo, hi, final=None, target: float = 0.8) -> float:
    """Минимальный коэффициент расширения, дающий покрытие >= target."""
    for k in np.arange(1.0, 10.01, 0.05):
        if coverage(y, *band(cat, lo, hi, k, final)) >= target:
            return round(float(k), 2)
    return 10.0


def fit_ridge(df: pd.DataFrame):
    X = X_of(df).drop(columns=CAT).fillna(0.0)
    return make_pipeline(StandardScaler(), Ridge(alpha=10.0)).fit(X, loss_target(df))


def predict_ridge(m, df: pd.DataFrame) -> np.ndarray:
    return df["thk_prev"].values - np.clip(m.predict(X_of(df).drop(columns=CAT).fillna(0.0)), 0, None)


def all_approaches(tr: pd.DataFrame, te: pd.DataFrame) -> dict[str, np.ndarray]:
    return {
        "persistence": te["thk_prev"].values,
        "linear_extrap": (te["thk_prev"] - te["rate_hist"].fillna(0) * te["horizon_years"]).values,
        "ridge": predict_ridge(fit_ridge(tr), te),
        "catboost": predict_thk(fit_catboost(tr), te),
    }


ALPHAS = [0.0, 0.25, 0.5, 0.75, 1.0]


def blend(thk_prev, cat, alpha: float) -> np.ndarray:
    return alpha * np.asarray(thk_prev) + (1 - alpha) * np.asarray(cat)


def loso(lab: pd.DataFrame) -> tuple[list[dict], float]:
    """Leave-one-section-out на 2022->2023: train = все 2018->2022 + 2022->2023 другого участка."""
    te_all = lab[lab["transition"] == "2022->2023"]
    oof = pd.Series(np.nan, index=te_all.index)
    per_sec = {}
    for sid in sorted(te_all["id"].unique()):
        te = te_all[te_all["id"] == sid]
        tr = lab[(lab["transition"] == "2018->2022") | ((lab["transition"] == "2022->2023") & (lab["id"] != sid))]
        oof[te.index] = predict_thk(fit_catboost(tr), te)
        per_sec[sid] = te.index
    y, prev = te_all["thk_next"], te_all["thk_prev"]
    rows, best = [], (-1.0, 1.0)
    for a in ALPHAS:
        p = pd.Series(blend(prev, oof, a), index=y.index)
        name = {1.0: "persistence", 0.0: "catboost"}.get(a, f"blend_a={a}")
        sec = "; ".join(f"{s}: {r2(y[i], p[i]):.3f}" for s, i in per_sec.items())
        rows.append({"approach": name, "scheme": f"LOSO 2022->2023 pooled (per section {sec})",
                     "n_test": len(y), "r2": r2(y, p), "mae": mae(y, p)})
        if r2(y, p) > best[0] + 1e-9:
            best = (r2(y, p), a)
    return rows, best[1]


def main():
    f = pd.read_parquet(CACHE / "features.parquet")
    lab = f[f["is_predict"] == 0].reset_index(drop=True)
    tr, te = lab[lab["transition"] == "2018->2022"], lab[lab["transition"] == "2022->2023"]

    rows = []
    for name, p in all_approaches(tr, te).items():
        rows.append({"approach": name, "scheme": "temporal 2018->2022 => 2022->2023",
                     "n_test": len(te), "r2": r2(te["thk_next"], p), "mae": mae(te["thk_next"], p)})

    # GroupKFold по блокам 50 сегментов (500 м) на всех размеченных переходах
    groups = lab["id"].astype(str) + "_" + (lab["segment_id"] // 50).astype(str)
    oof = {k: np.zeros(len(lab)) for k in ["persistence", "linear_extrap", "ridge", "catboost"]}
    for tri, tei in GroupKFold(n_splits=5).split(lab, groups=groups):
        for k, p in all_approaches(lab.iloc[tri], lab.iloc[tei]).items():
            oof[k][tei] = p
    for k, p in oof.items():
        rows.append({"approach": k, "scheme": "GroupKFold(5) 500m blocks", "n_test": len(lab),
                     "r2": r2(lab["thk_next"], p), "mae": mae(lab["thk_next"], p)})

    # выравнивание в метрах: r² persistence 2022->2023 при сдвиге прошлых прогонов на d м (d = 10 == -1 сегмент)
    base = build_segments()
    yy = base[base["date"] == pd.Timestamp("2023-12-01")].sort_values(["id", "segment_id"])
    for d in range(0, 11):
        s = build_segments(float(d), "2023-12-01")
        x = s[(s["date"] == pd.Timestamp("2022-07-06")) & s["id"].isin(yy["id"].unique())
              ].sort_values(["id", "segment_id"])
        rows.append({"approach": f"persistence_shift_{d}m", "scheme": "align: 2022 Distance + d m vs 2023",
                     "n_test": len(yy), "r2": r2(yy["min_thk"].values, x["min_thk"].values),
                     "mae": mae(yy["min_thk"].values, x["min_thk"].values)})

    loso_rows, alpha = loso(lab)
    rows += loso_rows
    (CACHE / "blend_alpha.txt").write_text(str(alpha))

    # интервалы на временной валидации; покрытие меряем вокруг итогового прогноза (смесь с LOSO-альфой)
    lo, hi = predict_interval(tr, te)
    y, cat = te["thk_next"].values, predict_thk(fit_catboost(tr), te)
    final = blend(te["thk_prev"], cat, alpha)
    k_old = 1.5
    cover = coverage(y, lo, hi)
    cover_old = coverage(y, *band(cat, lo, hi, k_old, final))
    k = k_old if abs(cover_old - 0.8) <= 0.05 else calibrate_k(y, cat, lo, hi, final)
    cover_k = coverage(y, *band(cat, lo, hi, k, final))
    (CACHE / "interval_k.txt").write_text(str(k))

    cov_rows = [("catboost_p10_p90_raw", cover), (f"final_p10_p90_k={k_old}", cover_old)]
    if k != k_old:
        cov_rows.append((f"final_p10_p90_k={k}_recalibrated", cover_k))
    for name, c in cov_rows:
        rows.append({"approach": name, "scheme": "temporal 2018->2022 => 2022->2023", "n_test": len(te),
                     "coverage": c})

    REPORTS.mkdir(exist_ok=True)
    met = pd.DataFrame(rows).round(4)
    met.to_csv(REPORTS / "metrics.csv", index=False)
    pd.DataFrame({"thk_next": te["thk_next"].values, "id": te["id"].values,
                  "segment_id": te["segment_id"].values, **{k: v for k, v in all_approaches(tr, te).items()}}
                 ).to_parquet(CACHE / "val_preds.parquet", index=False)

    full = fit_catboost(lab)
    imp = pd.DataFrame({"feature": FEATURES, "importance": full.feature_importances_}
                       ).sort_values("importance", ascending=False)
    imp.to_csv(REPORTS / "importance.csv", index=False)

    print(met[["approach", "scheme", "r2", "mae"]].assign(scheme=met["scheme"].str[:40]).to_string(index=False))
    print(f"LOSO-selected blend alpha (weight of thk_prev): {alpha}")
    print(f"p10-p90 coverage on 2022->2023: catboost raw {cover:.2f}; final k={k_old} {cover_old:.2f}; "
          f"final k={k} {cover_k:.2f}")
    print(imp.head(8).round(2).to_string(index=False))


if __name__ == "__main__":
    main()
