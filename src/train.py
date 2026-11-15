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

np.random.seed(SEED)

FEATURES = ["thk_prev", "max_depth_prev", "n_defects_prev", "censored_prev",
            "nb_thk_min", "nb_thk_mean", "nb_depth_max", "nb_n_defects", "nb_censored_share",
            "rate_hist", "horizon_years", "segment_id", "rel_pos",
            "z_mean", "slope", "z_above_min100", "is_local_min",
            "reg_watercut", "reg_v_liquid_true", "reg_v_mix", "reg_pit_corrosion_intensity",
            "reg_p_start", "reg_t_mix", "reg_gas_content_rate", "reg_share_not_turbulent", "id"]
CAT = ["id"]


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
    m.fit(X_of(df), loss_target(df, clip=not loss.startswith("Quantile")))
    return m


def predict_thk(m, df: pd.DataFrame, clip: bool = True) -> np.ndarray:
    loss = m.predict(X_of(df))
    return df["thk_prev"].values - (np.clip(loss, 0, None) if clip else loss)


def predict_interval(df_train: pd.DataFrame, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    a = predict_thk(fit_catboost(df_train, "Quantile:alpha=0.1"), df, clip=False)
    b = predict_thk(fit_catboost(df_train, "Quantile:alpha=0.9"), df, clip=False)
    return np.minimum(a, b), np.maximum(a, b)


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

    # интервалы на временной валидации
    lo, hi = predict_interval(tr, te)
    cover = float(np.mean((te["thk_next"] >= lo) & (te["thk_next"] <= hi)))

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

    print(met.to_string(index=False))
    print(f"p10-p90 coverage on 2022->2023: {cover:.2f}")
    print(imp.head(8).round(2).to_string(index=False))


if __name__ == "__main__":
    main()
