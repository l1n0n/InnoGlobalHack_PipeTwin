import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from config import TARGET_IDS, WT_NOM  # noqa: E402
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
    grid = segment_grid()
    grid = grid[grid["id"].isin(TARGET_IDS)]
    assert set(zip(res["id"], res["segment_id"])) == set(zip(grid["id"], grid["segment_id"]))


def test_thickness_range(res):
    assert ((res["thickness_pred"] > 0) & (res["thickness_pred"] <= WT_NOM)).all()


def test_decimal_point():
    assert "," not in RESULT.read_text().splitlines()[1].split(",", 2)[2]
