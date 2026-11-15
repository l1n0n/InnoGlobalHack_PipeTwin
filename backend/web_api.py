import csv
import io
import math
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .corrosion import co2_rate
from .submission import result_csv

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "tests"
PASSPORT = ROOT / "data" / "passport.csv"
OISPIPE = "Данные по протяжённости в структуре сети OISPIPE Паспортизация_"
PASSPORT_ID = OISPIPE + "ID простого участка трубопровода"
PASSPORT_NAME = OISPIPE + "Наименование простого участка трубопровода"
PASSPORT_LENGTH = OISPIPE + "L простого участка, км"
PASSPORT_WALL = "Общие данные_Толщина  стенки по исполнительной доку-ии, мм"
PASSPORT_START = "Общие данные_Дата ввода"
OISPIPE_PASSPORT_FIELDS = {PASSPORT_ID, PASSPORT_LENGTH, PASSPORT_WALL, PASSPORT_START}
COMPACT_PASSPORT_FIELDS = {"id", "length_m", "nominal_thickness_mm", "commissioned_at"}
FRONTEND = ROOT / "frontend"
app = FastAPI(title="PipeTwin")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def passport_names():
    if not PASSPORT.is_file():
        return {}
    with PASSPORT.open(encoding="utf-8-sig", newline="") as file:
        return {
            row[PASSPORT_ID].strip().removesuffix(".0"): row[PASSPORT_NAME].strip()
            for row in csv.DictReader(file) if row.get(PASSPORT_ID) and row.get(PASSPORT_NAME)
        }


def read_vtd(reader, as_of=None):
    latest = {}
    for row in reader:
        try:
            pipe = row["id"].strip()
            day = date.fromisoformat(row["date"])
        except (ValueError, TypeError, AttributeError):
            continue
        if not pipe:
            continue
        if as_of and day > as_of:
            continue
        previous, records = latest.get(pipe, (day, []))
        if day < previous:
            continue
        if day > previous:
            records = []
        latest[pipe] = day, records
        try:
            thickness = float(row["Residual Wall Thickness"])
        except (ValueError, TypeError):
            continue
        if math.isfinite(thickness) and thickness > 0:
            records.append({
                "segment_id": len(records) + 1, "thickness_pred": thickness,
                "inspection_date": day.isoformat(), "measurement_ref": row.get("row_id") or "",
            })
    pipes = [
        {"id": pipe, "coordinate_basis": "inspection_order", "segments": records}
        for pipe, (_, records) in sorted(latest.items()) if records
    ]
    if not pipes:
        raise ValueError("На последних доступных датах ВТД нет корректных замеров толщины.")
    return pipes


def read_measurements(reader, distance_field, thickness_field):
    measurements = {}
    for row in reader:
        try:
            pipe = row["id"].strip()
            day = date.fromisoformat(row["date"])
            distance = float(row[distance_field])
            thickness = float(row[thickness_field])
            if not pipe or not 0 <= distance <= 2**53 - 1 or not math.isfinite(thickness) or thickness <= 0:
                continue
        except (ValueError, TypeError, AttributeError):
            continue
        days = measurements.setdefault(pipe, {}).setdefault(int(distance // 10), {})
        days[day] = min(thickness, days.get(day, thickness))
    if not measurements:
        raise ValueError("В файле нет замеров с корректной датой, дистанцией и остаточной толщиной.")
    pipes = []
    for pipe, segments in sorted(measurements.items()):
        rows = []
        for segment_id, days in sorted(segments.items()):
            latest, *older = sorted(days, reverse=True)
            thickness = days[latest]
            row = {"segment_id": segment_id, "thickness_pred": thickness, "inspection_date": latest.isoformat()}
            if older and days[older[0]] >= thickness:
                row["rate"] = (days[older[0]] - thickness) * 365.25 / (latest - older[0]).days
            rows.append(row)
        pipes.append({"id": pipe, "segments": rows})
    return pipes


def read_passport(reader, *, compact=False):
    pipes = {}
    id_field, length_field, wall_field, date_field, name_field = (
        ("id", "length_m", "nominal_thickness_mm", "commissioned_at", "pipe_name")
        if compact else (PASSPORT_ID, PASSPORT_LENGTH, PASSPORT_WALL, PASSPORT_START, PASSPORT_NAME)
    )
    for line, row in enumerate(reader, start=2):
        try:
            pipe = row[id_field].strip()
            length = float(row[length_field])
            wall = float(row[wall_field])
            if compact:
                commissioned = date.fromisoformat(row[date_field])
            else:
                pipe = pipe.removesuffix(".0")
                length = round(length * 1000, 6)
                commissioned = datetime.strptime(row[date_field], "%d.%m.%Y").date()
            if not pipe or not 0 < length <= 1_000_000 or not math.isfinite(wall) or wall <= 0 or pipe in pipes:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError(f"Некорректный ID, номинальная стенка, длина или дата ввода в строке паспорта {line}.") from error
        pipes[pipe] = {"id": pipe, "name": row.get(name_field) or "", "length_m": length,
                       "commissioned_at": commissioned.isoformat(), "source_kind": "nominal",
                       "segments": [{"segment_id": index, "thickness_pred": wall}
                                    for index in range(math.ceil(length / 10))]}
    if not pipes:
        raise ValueError("В паспорте нет участков.")
    return list(pipes.values())


def read_forecast(content, as_of=None):
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
    fields = set(reader.fieldnames or [])
    if OISPIPE_PASSPORT_FIELDS <= fields:
        return read_passport(reader)
    if COMPACT_PASSPORT_FIELDS <= fields:
        return read_passport(reader, compact=True)
    if PASSPORT_ID in fields or "nominal_thickness_mm" in fields:
        expected = OISPIPE_PASSPORT_FIELDS if PASSPORT_ID in fields else COMPACT_PASSPORT_FIELDS
        raise ValueError("В паспорте отсутствуют столбцы: " + ", ".join(sorted(expected - fields)) + ".")
    if {"id", "date", "Distance", "Residual Wall Thickness"} <= fields:
        return read_vtd(reader, as_of)
    if {"id", "date", "start_distance", "residual_wall_thickness"} <= fields:
        return read_measurements(reader, "start_distance", "residual_wall_thickness")
    if not {"id", "segment_id", "thickness_pred"} <= fields:
        raise ValueError("Загрузите паспорт, ВТД, УЗТ или профиль PipeTwin. Схемы столбцов указаны в README.")
    pipes = {}
    seen = set()
    metadata = {}
    for line, row in enumerate(reader, start=2):
        try:
            pipe = row["id"].strip()
            segment = int(row["segment_id"])
            thickness = float(row["thickness_pred"])
            if not pipe or not 0 <= segment <= (2**53 - 1) // 10 or not math.isfinite(thickness) or thickness < 0:
                raise ValueError
            record = {"segment_id": segment, "thickness_pred": thickness}
            basis = row.get("coordinate_basis") or "distance"
            if basis not in {"distance", "inspection_order"}:
                raise ValueError
            info = {"coordinate_basis": basis}
            if row.get("length_m"):
                length = float(row["length_m"])
                if not math.isfinite(length) or length <= 0:
                    raise ValueError
                info["length_m"] = length
            if row.get("pipe_name"):
                info["name"] = row["pipe_name"]
            if row.get("source_kind") == "nominal":
                if basis != "distance" or not row.get("commissioned_at"):
                    raise ValueError
                info["source_kind"] = "nominal"
                info["commissioned_at"] = date.fromisoformat(row["commissioned_at"]).isoformat()
            if metadata.setdefault(pipe, info) != info:
                raise ValueError
            if row.get("measurement_ref"):
                record["measurement_ref"] = row["measurement_ref"]
            if row.get("inspection_date"):
                record["inspection_date"] = date.fromisoformat(row["inspection_date"]).isoformat()
            if row.get("rate"):
                rate = float(row["rate"])
                if not math.isfinite(rate) or rate < 0:
                    raise ValueError
                record["rate"] = rate
            if row.get("replacement_date"):
                if basis == "inspection_order":
                    raise ValueError
                replacement_date = date.fromisoformat(row["replacement_date"]).isoformat()
                wall = float(row["replacement_wall_mm"])
                diameter = float(row["replacement_diameter_mm"])
                if not math.isfinite(diameter) or not 0 < wall < diameter / 2:
                    raise ValueError
                record["replacement"] = {
                    "date": replacement_date, "wall_mm": wall, "diameter_mm": diameter,
                    "steel": row.get("replacement_steel") or "",
                    "coating": row.get("replacement_coating") or "",
                    "notes": row.get("replacement_notes") or "",
                }
        except (ValueError, TypeError, AttributeError, KeyError) as error:
            raise ValueError(f"Некорректные данные в строке {line}.") from error
        key = (pipe, segment)
        if key in seen:
            raise ValueError(f"Повтор сегмента в строке {line}.")
        seen.add(key)
        pipes.setdefault(pipe, []).append(record)
    if not pipes:
        raise ValueError("В файле нет сегментов.")
    return [
        {"id": pipe, **metadata[pipe],
         "segments": sorted(pipes[pipe], key=lambda row: row["segment_id"])}
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
async def upload_forecast(request: Request, as_of: date | None = None):
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 50 * 1024 * 1024:
            raise HTTPException(413, "Размер файла должен быть не больше 50 МБ.")
    try:
        pipes = await run_in_threadpool(read_forecast, content, as_of)
        names = passport_names()
        for pipe in pipes:
            if (name := names.get(pipe["id"].removesuffix(".0"))) and not pipe.get("name"):
                pipe["name"] = name
        return pipes
    except (ValueError, csv.Error) as error:
        raise HTTPException(422, str(error)) from error


@app.get("/api/corrosion/co2")
def corrosion_rate(
    temperature_c: float,
    total_pressure_bar: float,
    co2_mol_percent: float,
    ph: float,
    flow_velocity_ms: float,
    diameter_mm: float,
):
    try:
        return {"rate": co2_rate(
            temperature_c, total_pressure_bar, co2_mol_percent, ph, flow_velocity_ms, diameter_mm
        )}
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error


class ResultSegment(BaseModel):
    segment_id: int = Field(ge=0)
    thickness_pred: float = Field(ge=0, allow_inf_nan=False)


class ResultPipe(BaseModel):
    id: str = Field(min_length=1)
    coordinate_basis: str = "distance"
    length_m: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    segments: list[ResultSegment]


@app.post("/api/result")
def export_result(pipes: list[ResultPipe]):
    try:
        rows = [pipe.model_dump() for pipe in pipes]
        if PASSPORT.is_file():
            with PASSPORT.open(encoding="utf-8-sig", newline="") as file:
                lengths = {
                    row[PASSPORT_ID].strip().removesuffix(".0"): round(float(row[PASSPORT_LENGTH]) * 1000, 6)
                    for row in csv.DictReader(file) if row.get(PASSPORT_ID) and row.get(PASSPORT_LENGTH)
                }
            for pipe in rows:
                if pipe["id"] in lengths:
                    pipe["length_m"] = lengths[pipe["id"]]
        data = result_csv(rows)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return Response(
        data, media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="result.csv"',
            "X-Result-Coverage": "passport" if all(pipe.get("length_m") for pipe in rows) else "sequence",
            "Access-Control-Expose-Headers": "X-Result-Coverage",
        },
    )


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")
