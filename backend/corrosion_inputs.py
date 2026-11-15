"""Resolve corrosion inputs for an uploaded pipe profile."""

import csv
import io
import math
from datetime import date

from .pipeline_data import DATA_DIR, PASSPORT_ID, PASSPORT_WALL

FIELDS = {
    "temperature_c", "total_pressure_bar", "co2_mol_percent", "ph",
    "flow_velocity_ms", "diameter_mm",
}


def number(row, field):
    try:
        value = float(row[field])
    except (KeyError, TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def pipe_id(value):
    return (value or "").strip().removesuffix(".0")


def local_rows(filename):
    path = DATA_DIR / filename
    if path.is_file():
        with path.open(encoding="utf-8-sig", newline="") as file:
            yield from csv.DictReader(file)


def attach_corrosion_inputs(pipes, content, as_of=None):
    inputs = {pipe_id(pipe["id"]): {} for pipe in pipes}
    sources = {key: set() for key in inputs}
    limits = {}
    for pipe in pipes:
        dates = [row["inspection_date"] for row in pipe["segments"] if row.get("inspection_date")]
        limits[pipe_id(pipe["id"])] = as_of or (date.fromisoformat(max(dates)) if dates else date.max)

    def diameter(row):
        outer = number(row, "Общие данные_D, мм")
        wall = number(row, PASSPORT_WALL)
        if outer is None:
            outer = number(row, "diameter_mm")
            wall = number(row, "nominal_thickness_mm")
        if outer and wall and 10 <= outer - 2 * wall <= 2000:
            return outer - 2 * wall
        return None

    for row in local_rows("passport.csv"):
        key = pipe_id(row.get(PASSPORT_ID) or row.get("id"))
        if key in inputs and (value := diameter(row)) is not None:
            inputs[key]["diameter_mm"] = value
            sources[key].add("Внутренний диаметр по паспорту")

    for filename, id_field, date_field, fields in (
        ("regime.csv", "simple_section_id", "calc_date", (
            ("temperature_c", "t_mix", 5, 150), ("flow_velocity_ms", "v_mix", 0, 30),
        )),
        ("repcorr_south.csv", "Simple Section ID", "Sample Removal Date", (
            ("ph", "pH", 0, 14),
        )),
    ):
        latest = {}
        for row in local_rows(filename):
            key = pipe_id(row.get(id_field))
            if key not in inputs:
                continue
            stamp = row.get(date_field) or ""
            try:
                day = date.fromisoformat(stamp[:10])
            except ValueError:
                continue
            if day > limits[key]:
                continue
            for target, field, low, high in fields:
                value = number(row, field)
                if value is not None and low <= value <= high and stamp > latest.get((key, target), ""):
                    latest[key, target] = stamp
                    inputs[key][target] = value
        for (key, field), stamp in latest.items():
            label = {"temperature_c": "Температура", "flow_velocity_ms": "Скорость потока", "ph": "pH"}[field]
            sources[key].add(f"{label}: {filename}, {stamp[:10]}")

    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""))
    columns = FIELDS.intersection(reader.fieldnames or ())
    passport = PASSPORT_WALL in (reader.fieldnames or ()) or "nominal_thickness_mm" in (reader.fieldnames or ())
    if columns or passport:
        for row in reader:
            key = pipe_id(row.get(PASSPORT_ID) or row.get("id"))
            if key not in inputs:
                continue
            if passport and (value := diameter(row)) is not None:
                inputs[key]["diameter_mm"] = value
                sources[key].add("Внутренний диаметр по паспорту")
            for field in columns - ({"diameter_mm"} if passport else set()):
                if (value := number(row, field)) is not None:
                    inputs[key][field] = value
                    sources[key].add("Параметры NORSOK из загруженного CSV")

    for pipe in pipes:
        key = pipe_id(pipe["id"])
        pipe["co2_inputs"] = inputs[key]
        pipe["co2_sources"] = sorted(sources[key])
