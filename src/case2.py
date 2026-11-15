"""Кейс 2: скорость коррозии от ввода до ВТД. Обучение на DIR-2 (ввод 2000-04-22 -> ВТД 2022-07-06,
сырые дистанции), прогноз для 1250009583 на T_DATE_CASE2 -> дописывает result.csv и reports/."""
import numpy as np
import pandas as pd

from config import (CACHE, CASE1_IDS, CASE2_ID, COMMISSION_CASE1, COMMISSION_CASE2, COMMISSION_DIR3, DIR3_IDS,
                    SEG_LEN, T_DATE_CASE2, TRAIN_DATE_CASE2, VTD_DATE_DIR3, WT_NOM, WT_NOM_CASE2, WT_NOM_DIR3)
from catboost import CatBoostRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from config import REPORTS, ROOT, SEED
from features import relief_features
from load import load
from clean import clean_vtd
from segment import build_segments, segment_grid
from train import band, calibrate_k, coverage, mae, r2

# rel_pos исключён: GroupKFold r² CatBoost terrain 0.046 -> 0.074 без него, и положение на другой трубе не переносится
TERRAIN = ["z_rank", "slope", "z_above_min100", "z_above_min500", "is_local_min", "dist_to_low"]
REGIME_C2 = ["watercut", "v_liquid_true", "v_mix", "pit_corrosion_intensity", "gas_content_rate", "t_mix"]
REGIME = [f"reg_{c}" for c in REGIME_C2]
FEATS = TERRAIN + REGIME
FINAL = "catboost_all"   # выбран по GKF DIR-2 (≈ catboost_terrain) и знаку r при переносе DIR-2 -> DIR-3


def years(d0, d1) -> float:
    return (pd.Timestamp(d1) - pd.Timestamp(d0)).days / 365.25


def train_target() -> pd.DataFrame:
    """rate = (8.0 - min_thk) / возраст, мм/год; сегменты без дефектов (censored) -> 0."""
    s = build_segments()
    s = s[s["id"].isin(CASE1_IDS) & (s["date"] == pd.Timestamp(TRAIN_DATE_CASE2))]
    s = s[["id", "segment_id", "min_thk", "censored"]].copy()
    s["rate"] = ((WT_NOM - s["min_thk"]) / years(COMMISSION_CASE1, TRAIN_DATE_CASE2)).clip(lower=0)
    s["pipe"] = "DIR-2"
    s = s.merge(features(CASE1_IDS, TRAIN_DATE_CASE2), on=["id", "segment_id"], how="left")
    return s.reset_index(drop=True)


def dir3_target() -> pd.DataFrame:
    """DIR-3: ввод 2011-10-20 -> ВТД 2023-08-12, номинал 6.0, внутренние потери металла."""
    v = clean_vtd(DIR3_IDS, WT_NOM_DIR3)
    grid = segment_grid(DIR3_IDS)
    mx = grid.groupby("id")["segment_id"].max()
    v["segment_id"] = np.minimum(np.floor(v["Distance"] / SEG_LEN).astype(int).clip(lower=0), v["id"].map(mx))
    m = v.groupby(["id", "segment_id"])["rwt"].min().rename("min_thk").reset_index()
    s = grid.merge(m, on=["id", "segment_id"], how="left")
    s["censored"] = s["min_thk"].isna().astype(int)
    s["min_thk"] = s["min_thk"].fillna(WT_NOM_DIR3)
    s["rate"] = ((WT_NOM_DIR3 - s["min_thk"]) / years(COMMISSION_DIR3, VTD_DATE_DIR3)).clip(lower=0)
    s["pipe"] = "DIR-3"
    return s.merge(features(DIR3_IDS, VTD_DATE_DIR3), on=["id", "segment_id"], how="left")


def terrain(ids) -> pd.DataFrame:
    """Рельеф по сегментам; одинаковый код для обучающих и прогнозного участков."""
    rel = relief_features(ids)
    out = []
    for _, g in rel.groupby("id"):
        g = g.sort_values("segment_id").copy()
        z = g["z_mean"]
        g["z_rank"] = z.rank(pct=True)            # абсолютная высота между трубами не переносится
        g["z_above_min500"] = z - z.rolling(51, center=True, min_periods=1).min()
        low = np.flatnonzero(g["z_above_min100"].values <= 1e-9)   # нижняя точка в окне 100 м
        pos = np.arange(len(g))
        g["dist_to_low"] = SEG_LEN * np.abs(pos[:, None] - low[None, :]).min(axis=1) if len(low) else np.nan
        g["rel_pos"] = g["segment_id"] / g["segment_id"].max()
        out.append(g)
    return pd.concat(out, ignore_index=True)[["id", "segment_id"] + TERRAIN]


def regime_life(sid: int, d1) -> dict:
    """Средние режима за всю доступную историю до d1 (режим есть только с 2012/2014)."""
    r = load("regime")
    w = r[(r["simple_section_id"] == sid) & (r["calc_date"] <= pd.Timestamp(d1))]
    return {f"reg_{c}": w[c].mean() for c in REGIME_C2}


def features(ids, d1) -> pd.DataFrame:
    f = terrain(ids)
    reg = pd.DataFrame([{"id": i, **regime_life(i, d1)} for i in ids])
    return f.merge(reg, on="id", how="left")


def fit(name: str, df: pd.DataFrame, loss: str = "RMSE"):
    cols = TERRAIN if name.endswith("terrain") else FEATS
    if name.startswith("ridge"):
        m = make_pipeline(StandardScaler(), Ridge(alpha=1.0))
    else:
        m = CatBoostRegressor(depth=4, iterations=300, loss_function=loss, random_seed=SEED,
                              verbose=0, allow_writing_files=False)
    m.fit(df[cols], df["rate"])
    return lambda d: m.predict(d[cols]), m


MODELS = ["ridge_terrain", "ridge_all", "catboost_terrain", "catboost_all"]


def blocks(df: pd.DataFrame) -> pd.Series:
    return df["id"].astype(str) + "_" + (df["segment_id"] // 50).astype(str)


def oof_gkf(tr: pd.DataFrame, models=MODELS, quant: bool = False) -> pd.DataFrame:
    """GroupKFold(5) по блокам 500 м; прогнозы out-of-fold."""
    oof = pd.DataFrame(index=tr.index)
    for a, b in GroupKFold(5).split(tr, groups=blocks(tr)):
        for k in models:
            oof.loc[tr.index[b], k] = fit(k, tr.iloc[a])[0](tr.iloc[b])
        if quant:
            for q in (10, 90):
                oof.loc[tr.index[b], f"q{q}"] = fit("catboost_all", tr.iloc[a], f"Quantile:alpha={q / 100}")[0](tr.iloc[b])
    return oof


def validate(d2: pd.DataFrame, d3: pd.DataFrame):
    """r² по скорости (= r² по толщине внутри трубы: возраст одинаков). Считается отдельно по каждой трубе."""
    rows = []
    def add(scheme, y, preds):
        for k, p in preds.items():
            rows.append({"approach": f"case2_{k}", "scheme": scheme, "n_test": len(y),
                         "r2": round(r2(y, p), 4) if np.std(p) > 1e-12 else 0.0, "mae": round(mae(y, p), 4)})
    o2 = oof_gkf(d2, quant=True)
    add("case2 GKF(5) 500m DIR-2, train DIR-2", d2["rate"], {"const": np.full(len(d2), d2["rate"].mean()),
                                                              **{k: o2[k] for k in MODELS}})
    both = pd.concat([d2, d3], ignore_index=True)
    ob = oof_gkf(both)
    m2 = (both["pipe"] == "DIR-2").values
    add("case2 GKF(5) 500m DIR-2, train DIR-2+DIR-3", both["rate"][m2], {k: ob[k][m2] for k in MODELS})
    add("case2 GKF(5) 500m DIR-3, train DIR-2+DIR-3", both["rate"][~m2], {k: ob[k][~m2] for k in MODELS})
    add("case2 leave-one-pipe-out: train DIR-2 -> test DIR-3", d3["rate"], {k: fit(k, d2)[0](d3) for k in MODELS})
    add("case2 leave-one-pipe-out: train DIR-3 -> test DIR-2", d2["rate"], {k: fit(k, d3)[0](d2) for k in MODELS})
    return pd.DataFrame(rows), o2


def predict(d2: pd.DataFrame, k: float, scale: float) -> pd.DataFrame:
    """scale: уровень скорости DIR-2 -> DIR-3 (похожая обводнённость); множитель > 0, ранжирование не меняется."""
    te = features([CASE2_ID], T_DATE_CASE2).sort_values("segment_id").reset_index(drop=True)
    age = years(COMMISSION_CASE2, T_DATE_CASE2)
    rate = scale * fit(FINAL, d2)[0](te)
    q10 = scale * fit(FINAL, d2, "Quantile:alpha=0.1")[0](te)
    q90 = scale * fit(FINAL, d2, "Quantile:alpha=0.9")[0](te)
    lo, hi = band(rate, np.minimum(q10, q90), np.maximum(q10, q90), k)
    thk = lambda r: np.clip(WT_NOM_CASE2 - r * age, 0.01, WT_NOM_CASE2)
    return pd.DataFrame({"id": CASE2_ID, "segment_id": te["segment_id"], "rate_pred": rate,
                         "p50": thk(rate), "p10": thk(hi), "p90": thk(lo)})


def write(pred: pd.DataFrame, met: pd.DataFrame, k: float, imp: pd.DataFrame) -> None:
    grid = segment_grid([CASE2_ID])
    assert len(pred) == len(grid) and set(pred["segment_id"]) == set(grid["segment_id"])
    assert pred["p50"].notna().all() and ((pred["p50"] > 0) & (pred["p50"] <= WT_NOM_CASE2)).all()
    res = pd.read_csv(ROOT / "result.csv")
    res = res[res["id"] != CASE2_ID]
    # 6 знаков: после масштабирования на уровень DIR-3 разброс ~0.1 мм, 4 знака дают 392 уникальных значения из 921
    new = pred[["id", "segment_id"]].assign(thickness_pred=pred["p50"].round(6))
    res = pd.concat([res, new], ignore_index=True)
    assert not res.duplicated(["id", "segment_id"]).any()
    res.to_csv(ROOT / "result.csv", index=False, float_format="%.6f")

    iv = pd.read_csv(REPORTS / "intervals.csv")
    iv = iv[iv["id"] != CASE2_ID]
    iv2 = pred.assign(thk_prev=np.nan, p50_catboost=pred["p50"])[iv.columns].round(6)
    pd.concat([iv, iv2], ignore_index=True).to_csv(REPORTS / "intervals.csv", index=False)

    # 10 % самых тонких сегментов отдельно по каждой трубе
    top = pd.concat([g.nsmallest(int(np.ceil(0.1 * len(g))), "thickness_pred") for _, g in res.groupby("id")])
    top.to_csv(REPORTS / "top10_dangerous.csv", index=False)

    m = pd.read_csv(REPORTS / "metrics.csv")
    m = m[~m["approach"].astype(str).str.startswith("case2_")]
    pd.concat([m, met], ignore_index=True).to_csv(REPORTS / "metrics.csv", index=False)
    imp.to_csv(REPORTS / "importance_case2.csv", index=False)
    print(f"result.csv: {len(res)} rows ({len(new)} for {CASE2_ID}); case2 pred mean={pred.p50.mean():.3f}, "
          f"min={pred.p50.min():.3f}, max={pred.p50.max():.3f}, clipped={int((pred.p50 <= 0.01).sum())}; "
          f"top10: {top.groupby('id').size().to_dict()}")


if __name__ == "__main__":
    d2, d3 = train_target(), dir3_target()
    print(f"age at T: {years(COMMISSION_CASE2, T_DATE_CASE2):.2f} y")
    print(pd.concat([d2, d3]).groupby("pipe").agg(n=("rate", "size"), censored=("censored", "mean"),
                                                   rate_mean=("rate", "mean"), rate_max=("rate", "max")).round(4))
    met, o2 = validate(d2, d3)
    print(met.pivot(index="approach", columns="scheme", values="r2").T.to_string())
    lo, hi = np.minimum(o2.q10, o2.q90), np.maximum(o2.q10, o2.q90)
    cov_raw = coverage(d2["rate"], lo, hi)
    k = calibrate_k(d2["rate"].values, o2[FINAL].values, lo.values, hi.values)
    cov_k = coverage(d2["rate"], *band(o2[FINAL].values, lo.values, hi.values, k))
    print(f"q10-q90 coverage DIR-2 GKF: raw {cov_raw:.3f}; k={k} -> {cov_k:.3f}")
    sch = "case2 GKF(5) 500m DIR-2, train DIR-2"
    met = pd.concat([met, pd.DataFrame([
        {"approach": "case2_catboost_p10_p90_raw", "scheme": sch, "n_test": len(d2), "coverage": round(cov_raw, 4)},
        {"approach": f"case2_final_p10_p90_k={k}", "scheme": sch, "n_test": len(d2), "coverage": round(cov_k, 4)}])],
        ignore_index=True)
    model = fit(FINAL, d2)[1]
    imp = pd.DataFrame({"feature": FEATS, "importance": model.get_feature_importance().round(2)}
                       ).sort_values("importance", ascending=False)
    print(imp.head(6).to_string(index=False))
    # уровень: средняя скорость DIR-3 (обводнённость 0.12 %, как у 1250009583) / средняя скорость DIR-2 (88 %)
    scale = d3["rate"].mean() / d2["rate"].mean()
    print(f"rate scale DIR-2 -> DIR-3: {d3['rate'].mean():.5f} / {d2['rate'].mean():.5f} = {scale:.4f}")
    write(predict(d2, k, scale), met, k, imp)
