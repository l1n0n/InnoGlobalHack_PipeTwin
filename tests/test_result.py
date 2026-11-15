import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from config import CASE2_ID, TARGET_IDS, WT_NOM, WT_NOM_CASE2  # noqa: E402
from segment import segment_grid  # noqa: E402

RESULT = ROOT / "result.csv"


@pytest.fixture(scope="module")
def res():
    if not RESULT.exists():
        pytest.skip("result.csv not built; run `uv run python main.py`")
    return pd.read_csv(RESULT)


def test_columns(res):
    assert list(res.columns) == ["id", "segment_id", "thickness_pred"]


def test_no_nan_no_dupes(res):
    assert res.notna().all().all()
    assert not res.duplicated(["id", "segment_id"]).any()


def test_full_segment_set(res):
    grid = segment_grid(TARGET_IDS + [CASE2_ID])
    assert set(zip(res["id"], res["segment_id"])) == set(zip(grid["id"], grid["segment_id"]))


@pytest.mark.parametrize("sid,n_seg", [(1250022871, 525), (CASE2_ID, 1475)])
def test_both_pipes_complete(res, sid, n_seg):
    g = res[res["id"] == sid]
    assert len(g) == n_seg and g["segment_id"].nunique() == n_seg
    assert sorted(g["segment_id"]) == list(range(n_seg))
    assert g["thickness_pred"].notna().all()


def test_thickness_range(res):
    nom = res["id"].map({CASE2_ID: WT_NOM_CASE2}).fillna(WT_NOM)
    assert ((res["thickness_pred"] > 0) & (res["thickness_pred"] <= nom)).all()


def test_decimal_point():
    assert "," not in RESULT.read_text().splitlines()[1].split(",", 2)[2]
