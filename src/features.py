"""Шаг 4: признаки переходов «ВТД_prev -> ВТД_next» -> data/cache/features.parquet."""
import numpy as np
import pandas as pd

from config import CACHE, CASE1_IDS, RUN_SHIFT, RUN_SHIFT_AVG_M, RUN_SHIFT_M, SEG_LEN, T_DATE, TARGET_IDS
from load import load
from segment import build_segments, segment_grid

REGIME_COLS = ["watercut", "v_liquid_true", "v_mix", "pit_corrosion_intensity",
               "p_start", "t_mix", "gas_content_rate"]
NB = 2  # окно соседей ±2 сегмента


def relief_features(ids=CASE1_IDS) -> pd.DataFrame:
    p = load("profile")
    p = p[p["id"].isin(ids)].sort_values(["id", "total_distance"]).copy()
    p["segment_id"] = np.floor(p["total_distance"] / SEG_LEN).astype(int)
    out = []
    for sid, g in p.groupby("id"):
        seg = g.groupby("segment_id").agg(z_mean=("z", "mean"), z_first=("z", "first"),
                                          z_last=("z", "last"), d0=("total_distance", "first"),
                                          d1=("total_distance", "last")).reset_index()
        seg["slope"] = (seg["z_last"] - seg["z_first"]) / (seg["d1"] - seg["d0"]).clip(lower=1.0)
        # окно 100 м = ±5 сегментов
        seg["z_above_min100"] = seg["z_mean"] - seg["z_mean"].rolling(11, center=True, min_periods=1).min()
        nb_min = pd.concat([seg["z_mean"].shift(k) for k in (-2, -1, 1, 2)], axis=1).min(axis=1)
        seg["is_local_min"] = (seg["z_mean"] <= nb_min).astype(int)
        seg["id"] = sid
        out.append(seg[["id", "segment_id", "z_mean", "slope", "z_above_min100", "is_local_min"]])
    grid = segment_grid(ids)
    rel = grid.merge(pd.concat(out), on=["id", "segment_id"], how="left")
    cols = ["z_mean", "slope", "z_above_min100", "is_local_min"]
    rel[cols] = rel.groupby("id")[cols].transform(lambda s: s.ffill().bfill())
    return rel


def regime_window(sid: int, d0: pd.Timestamp, d1: pd.Timestamp) -> dict:
    r = load("regime")
    d1 = min(d1, pd.Timestamp(T_DATE))
    w = r[(r["simple_section_id"] == sid) & (r["calc_date"] >= d0) & (r["calc_date"] <= d1)]
    feats = {f"reg_{c}": w[c].mean() for c in REGIME_COLS}
    fr = w["flow_regime_real"].dropna()
    feats["reg_share_not_turbulent"] = (fr != "Турбулентный").mean() if len(fr) else np.nan
    return feats


def prev_block(seg: pd.DataFrame, sid: int, date, shift: int = 0) -> pd.DataFrame:
    s = seg[(seg["id"] == sid) & (seg["date"] == date)].sort_values("segment_id").reset_index(drop=True)
    if shift:  # выравнивание к координатам следующего прогона: значение в s берём из s + shift
        cols = ["min_thk", "max_depth", "n_defects", "censored"]
        s[cols] = s[cols].shift(-shift).ffill().bfill()
    b = pd.DataFrame({"id": sid, "segment_id": s["segment_id"], "thk_prev": s["min_thk"],
                      "max_depth_prev": s["max_depth"], "n_defects_prev": s["n_defects"],
                      "censored_prev": s["censored"]})
    roll = lambda x, f: getattr(x.rolling(2 * NB + 1, center=True, min_periods=1), f)()
    b["nb_thk_min"] = roll(b["thk_prev"], "min")
    b["nb_thk_mean"] = roll(b["thk_prev"], "mean")
    b["nb_depth_max"] = roll(b["max_depth_prev"], "max")
    b["nb_n_defects"] = roll(b["n_defects_prev"], "sum")
    b["nb_censored_share"] = roll(b["censored_prev"], "mean")
    return b


def build_features(inspections=None) -> pd.DataFrame:
    seg = pd.read_parquet(CACHE / "segments.parquet") if inspections is None else build_segments(inspections=inspections)
    rel = relief_features()
    t = pd.Timestamp(T_DATE)
    rows = []
    for sid in CASE1_IDS:
        dates = sorted(seg.loc[seg["id"] == sid, "date"].unique())
        pairs = [(i, i + 1) for i in range(len(dates) - 1)]
        if sid in TARGET_IDS and t not in dates:
            dates = dates + [t]
            pairs.append((len(dates) - 2, len(dates) - 1))
        for i, j in pairs:
            d0, d1 = pd.Timestamp(dates[i]), pd.Timestamp(dates[j])
            key = (d0.strftime("%Y-%m-%d"), d1.strftime("%Y-%m-%d"))
            sh = RUN_SHIFT.get(key, 0)
            # метровый сдвиг: прошлые прогоны пересегментируются в координатах прогона d1
            sp = build_segments(RUN_SHIFT_M[key], d1, inspections=inspections) if key in RUN_SHIFT_M else seg
            b = prev_block(sp, sid, d0, sh)
            if key in RUN_SHIFT_AVG_M:  # сдвиг неточен: усредняем толщину прошлого прогона по сетке сдвигов
                b["thk_prev"] = np.mean([prev_block(build_segments(d, d1, inspections=inspections), sid, d0, sh)["thk_prev"].values
                                         for d in RUN_SHIFT_AVG_M[key]], axis=0)
            if i > 0:
                pp = prev_block(sp, sid, dates[i - 1], sh)
                yrs = (d0 - pd.Timestamp(dates[i - 1])).days / 365.25
                b["rate_hist"] = ((pp["thk_prev"] - b["thk_prev"]) / yrs).clip(lower=0).values
            else:
                b["rate_hist"] = np.nan
            nxt = seg[(seg["id"] == sid) & (seg["date"] == d1)][["segment_id", "min_thk", "censored"]]
            b = b.merge(nxt.rename(columns={"min_thk": "thk_next", "censored": "censored_next"}),
                        on="segment_id", how="left")
            b["date_prev"], b["date_next"] = d0, d1
            b["horizon_years"] = (d1 - d0).days / 365.25
            b["rel_pos"] = b["segment_id"] / b["segment_id"].max()
            for k, val in regime_window(sid, d0, d1).items():
                b[k] = val
            b["is_predict"] = int(b["thk_next"].isna().all())
            rows.append(b)
    f = pd.concat(rows, ignore_index=True).merge(rel, on=["id", "segment_id"], how="left")
    f["transition"] = f["date_prev"].dt.year.astype(str) + "->" + f["date_next"].dt.year.astype(str)
    return f


if __name__ == "__main__":
    f = build_features()
    f.to_parquet(CACHE / "features.parquet", index=False)
    print(f.shape)
    print(f.groupby(["id", "transition", "is_predict"]).size())
    print(f.isna().mean().loc[lambda s: s > 0].round(2).to_dict())
