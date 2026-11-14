import csv
import io
import math
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "tests"
FRONTEND = ROOT / "frontend"
app = FastAPI(title="PipeTwin")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def read_forecast(content):
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
    if not {"id", "segment_id", "thickness_pred"}.issubset(reader.fieldnames or []):
        raise ValueError("Нужны столбцы id, segment_id и thickness_pred.")
    pipes = {}
    seen = set()
    for line, row in enumerate(reader, start=2):
        try:
            pipe = row["id"].strip()
            segment = int(row["segment_id"])
            thickness = float(row["thickness_pred"])
            if not pipe or not 0 <= segment <= (2**53 - 1) // 10 or not math.isfinite(thickness) or thickness < 0:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError(f"Некорректные данные в строке {line}.") from error
        key = (pipe, segment)
        if key in seen:
            raise ValueError(f"Повтор сегмента в строке {line}.")
        seen.add(key)
        pipes.setdefault(pipe, []).append({"segment_id": segment, "thickness_pred": thickness})
    if not pipes:
        raise ValueError("В файле нет сегментов.")
    return [
        {"id": pipe, "segments": sorted(pipes[pipe], key=lambda row: row["segment_id"])}
        for pipe in sorted(pipes)
    ]


@app.get("/api/demo")
def demo():
    pipes = read_forecast((DEMO / "result.csv").read_bytes())
    with (DEMO / "passport.csv").open(encoding="utf-8", newline="") as file:
        lengths = {row["id"]: int(row["length_m"]) for row in csv.DictReader(file)}
    history = {}
    with (DEMO / "vtd.csv").open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            key = row["id"], int(float(row["Distance"]) // 10)
            day = date.fromisoformat(row["date"])
            thickness = float(row["Residual Wall Thickness"])
            measurements = history.setdefault(key, {})
            measurements[day] = min(thickness, measurements.get(day, thickness))
    for pipe in pipes:
        pipe["length_m"] = lengths[pipe["id"]]
        for segment in pipe["segments"]:
            measurements = history.get((pipe["id"], segment["segment_id"]), {})
            days = sorted(measurements)[-2:]
            if len(days) == 2 and measurements[days[0]] >= measurements[days[1]]:
                years = (days[1] - days[0]).days / 365.25
                segment["rate"] = (measurements[days[0]] - measurements[days[1]]) / years
    return pipes


@app.post("/api/forecast")
async def upload_forecast(request: Request):
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(413, "Файл должен быть меньше 5 МБ.")
    try:
        return read_forecast(content)
    except (ValueError, csv.Error) as error:
        raise HTTPException(422, str(error)) from error


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")
