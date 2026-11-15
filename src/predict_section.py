"""Calculate an on-demand CatBoost profile without writing submission files."""

import json
import math
import sys

import numpy as np
import pandas as pd

from clean import clean_vtd
from config import RUN_SHIFT, RUN_SHIFT_M, T_DATE
from features import build_features, prev_block, regime_window, relief_features
from load import load
from segment import build_segments, section_lengths
from train import FEATURES, fit_catboost, predict_interval, predict_thk


def forecast(section):
    target = pd.Timestamp(T_DATE)
    passport = load("passport")
    id_column = next(c for c in passport if c.endswith("_ID простого участка трубопровода"))
    wall_column = "Общие данные_Толщина  стенки по исполнительной доку-ии, мм"
    walls = pd.to_numeric(passport.loc[pd.to_numeric(passport[id_column], errors="coerce") == section, wall_column],
                          errors="coerce").dropna()
    if walls.empty or not math.isfinite(walls.iloc[0]) or walls.iloc[0] <= 0:
        raise ValueError("Для расчёта нужен паспорт участка с номинальной толщиной.")
    wall = walls.iloc[0]
    if not section_lengths([section])[section] or not (load("profile")["id"] == section).any():
        raise ValueError("Для расчёта нужен профиль трассы участка.")
    inspections = clean_vtd([section], wt_nom=wall)
    dates = sorted(inspections.loc[inspections["date"] < target, "date"].unique())
    if not dates:
        raise ValueError(f"Для расчёта нужны данные ВТД участка до {T_DATE}.")
    previous = pd.Timestamp(dates[-1])
    key = (previous.date().isoformat(), T_DATE)
    segments = build_segments(RUN_SHIFT_M.get(key, 0), target, inspections=inspections, ids=[section], wall=wall)
    shift = RUN_SHIFT.get(key, 0)
    rows = prev_block(segments, section, previous, shift)
    rows["rate_hist"] = np.nan
    if len(dates) > 1:
        older = pd.Timestamp(dates[-2])
        earlier = prev_block(segments, section, older, shift)
        rows["rate_hist"] = ((earlier["thk_prev"] - rows["thk_prev"]) * 365.25 / (previous - older).days).clip(lower=0)
    rows["horizon_years"] = (target - previous).days / 365.25
    rows["rel_pos"] = rows["segment_id"] / max(1, rows["segment_id"].max())
    for key, value in regime_window(section, previous, target).items():
        rows[key] = value
    rows = rows.merge(relief_features([section]), on=["id", "segment_id"])

    features = build_features(clean_vtd())
    training = features[(features["date_next"] <= target) & features["thk_next"].notna()
                        & ((features["id"] != section) | (features["date_next"] < target))]
    model = fit_catboost(training)
    mid = np.clip(predict_thk(model, rows), 0, wall)
    low, high = predict_interval(training, rows)
    profile = rows[["segment_id"]].assign(thickness_pred=mid, p50=mid,
                                           p10=np.clip(np.minimum(low, mid), 0, wall),
                                           p90=np.clip(np.maximum(high, mid), 0, wall))
    top = profile.nsmallest(math.ceil(len(profile) / 10), "thickness_pred")
    return {
        "pipe": {"id": str(section), "t_date": T_DATE, "segments": len(rows),
                 "min_thickness": float(mid.min()), "mean_thickness": float(mid.mean())},
        "profile": profile.to_dict("records"),
        "top": top.to_dict("records"),
        "importance": sorted(
            ({"feature": name, "importance": float(value)} for name, value in zip(FEATURES, model.feature_importances_)),
            key=lambda row: -row["importance"],
        ),
    }


if __name__ == "__main__":
    try:
        result = forecast(int(sys.argv[1]))
    except ValueError as error:
        result = {"error": str(error)}
    print(json.dumps(result))
