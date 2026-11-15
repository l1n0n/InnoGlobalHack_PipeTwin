"""Baseline wall-thickness forecast for the two competition scenarios."""

import csv
from bisect import bisect_left
from datetime import date
from statistics import median

from .corrosion import remaining_thickness
from .pipeline_data import load_inspection_minima, read_pipeline_csv


INSPECTED_PIPE_ID = "1250022871"
UNINSPECTED_PIPE_ID = "1250009583"
SUBMISSION_PIPE_IDS = (INSPECTED_PIPE_ID, UNINSPECTED_PIPE_ID)


def interpolate_wall(segment_id, measurements, measured_ids):
    index = bisect_left(measured_ids, segment_id)
    if index == 0:
        return measurements[measured_ids[0]]
    if index == len(measured_ids):
        return measurements[measured_ids[-1]]
    left, right = measured_ids[index - 1:index + 1]
    return measurements[left] + (measurements[right] - measurements[left]) * (segment_id - left) / (right - left)


def first_survey_rate(pipe, first_day, first_measurements):
    commissioning = date.fromisoformat(pipe["commissioned_at"])
    age_days = (first_day - commissioning).days
    if age_days <= 0:
        raise ValueError("Первый ВТД должен быть позже ввода трубы в эксплуатацию.")
    nominal = pipe["segments"][0]["thickness_pred"]
    return max(0, nominal - median(first_measurements.values())) * 365.25 / age_days


def forecast_competition_pipelines(data_dir, target=None):
    """Return the baseline output rows and the inputs used for the run."""
    passport = {pipe["id"]: pipe for pipe in read_pipeline_csv((data_dir / "passport.csv").read_bytes())}
    inspected, uninspected = (passport[pipe_id] for pipe_id in SUBMISSION_PIPE_IDS)
    if target is None:
        with (data_dir / "regime.csv").open(encoding="utf-8-sig", newline="") as file:
            last_days = {}
            for row in csv.DictReader(file):
                pipe_id = row["simple_section_id"]
                if pipe_id in SUBMISSION_PIPE_IDS:
                    day = date.fromisoformat(row["calc_date"][:10])
                    last_days[pipe_id] = max(day, last_days.get(pipe_id, day))
        target = min(last_days[pipe_id] for pipe_id in SUBMISSION_PIPE_IDS)

    surveys = {day: values for day, values in load_inspection_minima(data_dir, inspected).items() if day < target}
    if len(surveys) < 2:
        raise ValueError("Для базового расчёта нужны два доступных ВТД до выбранной даты.")

    first, latest = min(surveys), max(surveys)
    rate = first_survey_rate(inspected, first, surveys[first])
    measured = surveys[latest]
    measured_ids = sorted(measured)
    predictions = []
    for pipe in (inspected, uninspected):
        start = latest if pipe is inspected else date.fromisoformat(pipe["commissioned_at"])
        years = (target - start).days / 365.25
        segments = []
        for row in pipe["segments"]:
            segment_id = row["segment_id"]
            wall = row["thickness_pred"]
            if pipe is inspected:
                wall = measured.get(segment_id)
                if wall is None:
                    wall = interpolate_wall(segment_id, measured, measured_ids)
            segments.append({"segment_id": segment_id, "thickness_pred": remaining_thickness(wall, rate, years)})
        predictions.append({**pipe, "segments": segments})
    return predictions, {
        "target": target, "first_vtd": first, "latest_vtd": latest, "rate": rate,
        "filled_segments": len(inspected["segments"]) - len(measured),
    }
