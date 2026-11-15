"""FastAPI endpoints that serve the ML pipeline outputs (result.csv, reports/)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import ml_results
from backend.web_api import app

ROOT = Path(__file__).resolve().parent.parent
CASE1, CASE2 = ml_results.CASE1_ID, ml_results.CASE2_ID
N_SEG = {CASE1: 525, CASE2: 1475}

pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in ml_results.FILES.values()),
    reason="pipeline outputs missing; run `uv run python main.py`",
)


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_pipes(client):
    r = client.get("/api/ml/pipes")
    assert r.status_code == 200
    pipes = {p["id"]: p for p in r.json()}
    assert set(pipes) == {CASE1, CASE2}
    assert pipes[CASE1]["case"] == 1 and pipes[CASE2]["case"] == 2
    assert pipes[CASE1]["t_date"] == "2023-12-01" and pipes[CASE2]["t_date"] == "2023-08-12"
    for pid, n in N_SEG.items():
        p = pipes[pid]
        assert p["segments"] == n
        assert 0 < p["min_thickness"] <= p["mean_thickness"]


@pytest.mark.parametrize("pid", [CASE1, CASE2])
def test_profile(client, pid):
    rows = client.get(f"/api/ml/profile/{pid}").json()
    assert len(rows) == N_SEG[pid]
    assert [r["segment_id"] for r in rows] == list(range(N_SEG[pid]))
    assert set(rows[0]) == {"segment_id", "thickness_pred", "p10", "p50", "p90", "thk_prev"}
    for r in rows:
        assert r["thickness_pred"] > 0
        assert r["p10"] <= r["p50"] <= r["p90"]
    has_prev = [r["thk_prev"] is not None for r in rows]
    assert all(has_prev) if pid == CASE1 else not any(has_prev)


@pytest.mark.parametrize("pid", [CASE1, CASE2])
def test_top10(client, pid):
    rows = client.get(f"/api/ml/top10/{pid}").json()
    assert 0 < len(rows) <= N_SEG[pid]
    thk = [r["thickness_pred"] for r in rows]
    assert thk == sorted(thk)
    profile_min = min(r["thickness_pred"] for r in client.get(f"/api/ml/profile/{pid}").json())
    assert thk[0] == pytest.approx(profile_min)


def test_metrics(client):
    body = client.get("/api/ml/metrics").json()
    cases = {c["case"]: c for c in body["cases"]}
    assert set(cases) == {1, 2}
    for c in cases.values():
        assert c["r2"] is not None and 0 <= c["r2"] <= 1
        assert c["validation"] and 0 < c["interval_coverage"] <= 1
    assert cases[1]["r2"] > cases[2]["r2"]
    assert {"approach", "scheme", "n_test", "r2", "mae", "coverage"} <= set(body["approaches"][0])
    assert len(body["experiments"]) > 0


@pytest.mark.parametrize("pid", [CASE1, CASE2])
def test_importance(client, pid):
    rows = client.get(f"/api/ml/importance/{pid}").json()
    assert rows and set(rows[0]) == {"feature", "importance"}
    imp = [r["importance"] for r in rows]
    assert imp == sorted(imp, reverse=True)


def test_result_csv(client):
    r = client.get("/api/ml/result.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0] == "id,segment_id,thickness_pred"
    assert len(lines) - 1 == sum(N_SEG.values())


def test_unknown_pipe(client):
    assert client.get("/api/ml/profile/123").status_code == 404


def test_missing_files(client, monkeypatch, tmp_path):
    monkeypatch.setitem(ml_results.FILES, "result", tmp_path / "result.csv")
    r = client.get("/api/ml/pipes")
    assert r.status_code == 503
    assert "main.py" in r.json()["detail"]
