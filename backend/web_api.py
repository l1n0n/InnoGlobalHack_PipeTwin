"""HTTP endpoints and static frontend for PipeTwin."""

import csv
from datetime import date

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .corrosion import co2_rate
from .pipeline_data import (
    PROJECT_ROOT,
    load_demo_profiles,
    passport_lengths,
    passport_names,
    read_pipeline_csv,
)
from .submission import result_csv

FRONTEND = PROJECT_ROOT / "frontend"
app = FastAPI(title="PipeTwin")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/api/demo")
def demo():
    return load_demo_profiles()


@app.post("/api/forecast")
async def upload_forecast(request: Request, as_of: date | None = None):
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 50 * 1024 * 1024:
            raise HTTPException(413, "Размер файла должен быть не больше 50 МБ.")
    try:
        pipes = await run_in_threadpool(read_pipeline_csv, content, as_of)
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
        lengths = passport_lengths()
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
