"""Шаг 2: очистка vtd -> data/cache/vtd_clean.parquet."""
import numpy as np
import pandas as pd

from config import CACHE, CASE1_IDS, WT_NOM
from load import load

COLS = ["id", "date", "Distance", "Defect Type", "depth_pct", "rwt", "Angle",
        "Feature Length", "Feature Width"]


def clean_vtd() -> pd.DataFrame:
    v = load("vtd")
    v = v[v["id"].isin(CASE1_IDS)].copy()
    v = v[~v["is_beyond_pot"].fillna(False).astype(bool)]

    desc = v["Feature Description"].astype("string").str.lower().str.strip()
    trait = v["trait"].astype("string").str.strip()
    is_ml = (trait == "Потеря металла") | (trait.isna() & desc.str.startswith("потеря металла"))
    v = v[is_ml.fillna(False)].copy()

    depth = pd.to_numeric(v["Defect Depth in %"], errors="coerce")
    is_2018 = v["date"].dt.year == 2018
    depth = depth.where(~is_2018, depth * 100)   # 2018: доли -> %
    v["depth_pct"] = depth

    rwt = pd.to_numeric(v["Residual Wall Thickness"], errors="coerce")
    v["rwt"] = rwt.fillna(WT_NOM * (1 - depth / 100)).clip(upper=WT_NOM)

    v["Defect Type"] = (v["Defect Type"].astype("string").str.strip()
                        .replace({"внутр.": "Внутренний", "внешн.": "Внешний"}))
    v = v.dropna(subset=["Distance", "rwt"])
    return v[COLS].reset_index(drop=True)


if __name__ == "__main__":
    v = clean_vtd()
    v.to_parquet(CACHE / "vtd_clean.parquet", index=False)
    s = v.groupby(["id", "date"]).agg(n=("rwt", "size"), rwt_med=("rwt", "median"),
                                      rwt_min=("rwt", "min"), depth_min=("depth_pct", "min"),
                                      depth_max=("depth_pct", "max"))
    print(s.round(2))
    print(v["Defect Type"].value_counts(dropna=False).to_dict())
