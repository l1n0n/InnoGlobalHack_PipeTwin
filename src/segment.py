"""Шаг 3: сетка 10-м сегментов и цель min_thk/censored -> data/cache/segments.parquet."""
import numpy as np
import pandas as pd

from config import CACHE, CASE1_IDS, SEG_LEN, WT_NOM
from load import load


def section_lengths() -> dict[int, float]:
    r = load("regime")
    r = r[r["simple_section_id"].isin(CASE1_IDS)]
    lengths = r.groupby("simple_section_id")["length"].max().to_dict()
    p = load("profile")
    prof_max = p[p["id"].isin(CASE1_IDS)].groupby("id")["total_distance"].max().to_dict()
    return {i: lengths.get(i, prof_max.get(i)) for i in CASE1_IDS}


def segment_grid() -> pd.DataFrame:
    return pd.DataFrame([(i, s) for i, L in section_lengths().items()
                         for s in range(int(np.floor(L / SEG_LEN)) + 1)],
                        columns=["id", "segment_id"])


def build_segments() -> pd.DataFrame:
    v = pd.read_parquet(CACHE / "vtd_clean.parquet")
    grid = segment_grid()
    max_seg = grid.groupby("id")["segment_id"].max()
    v["segment_id"] = np.floor(v["Distance"] / SEG_LEN).astype(int).clip(lower=0)
    v["segment_id"] = np.minimum(v["segment_id"], v["id"].map(max_seg))  # хвост за длиной -> последний сегмент

    agg = v.groupby(["id", "date", "segment_id"]).agg(
        min_thk=("rwt", "min"), max_depth=("depth_pct", "max"), n_defects=("rwt", "size")).reset_index()

    runs = v[["id", "date"]].drop_duplicates()
    full = grid.merge(runs, on="id").merge(agg, on=["id", "date", "segment_id"], how="left")
    full["censored"] = full["min_thk"].isna().astype(int)
    full["min_thk"] = full["min_thk"].fillna(WT_NOM)
    full["max_depth"] = full["max_depth"].fillna(0.0)
    full["n_defects"] = full["n_defects"].fillna(0).astype(int)
    return full.sort_values(["id", "date", "segment_id"]).reset_index(drop=True)


if __name__ == "__main__":
    s = build_segments()
    s.to_parquet(CACHE / "segments.parquet", index=False)
    print(s.groupby(["id", "date"]).agg(n_seg=("segment_id", "size"), censored=("censored", "mean"),
                                        thk_mean=("min_thk", "mean"), thk_min=("min_thk", "min")).round(3))
