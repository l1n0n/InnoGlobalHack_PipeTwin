"""Read-only access to the ML pipeline outputs (result.csv and reports/) written by main.py."""

import csv
import importlib.util
import json
import math
import subprocess
import sys
import threading
import time
from pathlib import Path

from .pipeline_data import PROJECT_ROOT

RESULT = PROJECT_ROOT / "result.csv"
REPORTS = PROJECT_ROOT / "reports"
FILES = {
    "result": RESULT,
    "intervals": REPORTS / "intervals.csv",
    "top10": REPORTS / "top10_dangerous.csv",
    "metrics": REPORTS / "metrics.csv",
    "experiments": REPORTS / "experiments.csv",
    "importance": REPORTS / "importance.csv",
    "importance_case2": REPORTS / "importance_case2.csv",
}


class MissingResults(RuntimeError):
    pass


def _config():
    spec = importlib.util.spec_from_file_location("pipetwin_config", PROJECT_ROOT / "src" / "config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_cfg = _config()
CASE1_ID = str(_cfg.TARGET_IDS[0])
CASE2_ID = str(_cfg.CASE2_ID)
CASES = {
    CASE1_ID: {"case": 1, "t_date": _cfg.T_DATE, "importance": "importance"},
    CASE2_ID: {"case": 2, "t_date": _cfg.T_DATE_CASE2, "importance": "importance_case2"},
}
# Rows of metrics.csv that hold the final numbers of each case: (approach prefix, scheme prefix).
FINAL_ROWS = {
    1: {"r2": ("persistence", "LOSO"), "coverage": ("final_p10_p90", "temporal")},
    2: {"r2": ("case2_catboost_terrain", "case2 GKF(5) 500m DIR-2, train DIR-2"),
        "coverage": ("case2_final_p10_p90", "case2 GKF")},
}

_cache: dict[str, tuple[float, list[dict]]] = {}
_lock = threading.Lock()


def _num(value: str):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except ValueError:
        return value
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() and "." not in value else number


def table(name: str) -> list[dict]:
    """Rows of one output file, cached until its mtime changes."""
    path = FILES[name]
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        shown = path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path
        raise MissingResults(
            f"{shown} not found. Run the pipeline first: `uv run python main.py`."
        ) from None
    with _lock:
        cached = _cache.get(name)
        if cached and cached[0] == mtime:
            return cached[1]
        with path.open(newline="", encoding="utf-8") as file:
            rows = [{k: _num(v) for k, v in row.items()} for row in csv.DictReader(file)]
        _cache[name] = (mtime, rows)
        return rows


def _case(pipe_id: str) -> dict:
    if pipe_id not in CASES:
        raise KeyError(pipe_id)
    return CASES[pipe_id]


def pipes() -> list[dict]:
    out = []
    for pipe_id, info in CASES.items():
        values = [r["thickness_pred"] for r in table("result") if str(r["id"]) == pipe_id]
        out.append({
            "id": pipe_id,
            "case": info["case"],
            "t_date": info["t_date"],
            "segments": len(values),
            "mean_thickness": round(sum(values) / len(values), 4) if values else None,
            "min_thickness": round(min(values), 4) if values else None,
        })
    return out


def profile(pipe_id: str) -> list[dict]:
    info = _case(pipe_id)
    bands = {r["segment_id"]: r for r in table("intervals") if str(r["id"]) == pipe_id}
    rows = []
    for r in table("result"):
        if str(r["id"]) != pipe_id:
            continue
        band = bands.get(r["segment_id"], {})
        rows.append({
            "segment_id": r["segment_id"],
            "thickness_pred": r["thickness_pred"],
            "p10": band.get("p10"),
            "p50": band.get("p50"),
            "p90": band.get("p90"),
            "thk_prev": band.get("thk_prev") if info["case"] == 1 else None,
        })
    return sorted(rows, key=lambda row: row["segment_id"])


def top10(pipe_id: str) -> list[dict]:
    _case(pipe_id)
    rows = [{"segment_id": r["segment_id"], "thickness_pred": r["thickness_pred"]}
            for r in table("top10") if str(r["id"]) == pipe_id]
    return sorted(rows, key=lambda row: row["thickness_pred"])


def _pick(rows: list[dict], approach: str, scheme: str) -> dict | None:
    for r in rows:
        if str(r["approach"]).startswith(approach) and str(r["scheme"]).startswith(scheme):
            return r
    return None


def metrics() -> dict:
    rows = table("metrics")
    cases = []
    for pipe_id, info in CASES.items():
        rule = FINAL_ROWS[info["case"]]
        final = _pick(rows, *rule["r2"]) or {}
        cov = _pick(rows, *rule["coverage"]) or {}
        cases.append({
            "id": pipe_id,
            "case": info["case"],
            "t_date": info["t_date"],
            "final_approach": final.get("approach"),
            "r2": final.get("r2"),
            "mae": final.get("mae"),
            "validation": final.get("scheme"),
            "n_test": final.get("n_test"),
            "interval": cov.get("approach"),
            "interval_coverage": cov.get("coverage"),
        })
    return {"cases": cases, "approaches": rows, "experiments": table("experiments")}


def importance(pipe_id: str) -> list[dict]:
    rows = table(_case(pipe_id)["importance"])
    return sorted(({"feature": r["feature"], "importance": r["importance"]} for r in rows),
                  key=lambda row: -row["importance"])


_run_lock = threading.Lock()
_section_lock = threading.Lock()
_section_forecasts = {}


def section_forecast(pipe_id):
    """Reuse an on-demand forecast for this workbook and section."""
    workbook = PROJECT_ROOT / "data" / "raw_csv" / "all.xlsx"
    if not workbook.is_file():
        raise MissingResults("Для расчёта ML нужен data/raw_csv/all.xlsx.")
    version = workbook.stat().st_mtime_ns
    with _section_lock:
        cached = _section_forecasts.get(pipe_id)
        if cached and cached[0] == version:
            return cached[1]
        try:
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", str(PROJECT_ROOT / "src" / "predict_section.py"), pipe_id],
                cwd=PROJECT_ROOT, capture_output=True, encoding="utf-8", timeout=900,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("Расчёт ML превысил лимит времени.") from error
        if proc.returncode:
            raise RuntimeError(proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "Ошибка расчёта ML.")
        result = json.loads(proc.stdout)
        if "profile" in result:
            _section_forecasts[pipe_id] = version, result
        return result


def run_pipeline(timeout: int = 900) -> dict:
    """Run main.py with the current interpreter; one run at a time."""
    if not _run_lock.acquire(blocking=False):
        return {"status": "busy", "duration_s": 0.0, "returncode": None, "log_tail": ""}
    try:
        start = time.perf_counter()
        try:
            proc = subprocess.run([sys.executable, str(PROJECT_ROOT / "main.py")], cwd=PROJECT_ROOT,
                                  capture_output=True, text=True, timeout=timeout)
            status, code, log = ("ok" if proc.returncode == 0 else "error"), proc.returncode, proc.stdout + proc.stderr
        except subprocess.TimeoutExpired as error:
            status, code, log = "timeout", None, str(error.stdout or "")
        return {"status": status, "duration_s": round(time.perf_counter() - start, 2),
                "returncode": code, "log_tail": "\n".join(str(log).splitlines()[-20:])}
    finally:
        _run_lock.release()
