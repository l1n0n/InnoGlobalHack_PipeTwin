"""Chronological holdout and error metrics for the baseline forecast."""

import math
from datetime import date

from .corrosion import remaining_thickness
from .forecasting import INSPECTED_PIPE_ID, first_survey_rate, interpolate_wall
from .pipeline_data import load_inspection_minima, read_pipeline_csv


def forecast_metrics(actual, predicted):
    count = len(actual)
    errors = [estimate - truth for truth, estimate in zip(actual, predicted, strict=True)]
    actual_mean, predicted_mean = sum(actual) / count, sum(predicted) / count
    pearson_r2 = None
    if min(actual) < max(actual) and min(predicted) < max(predicted):
        a = [value - actual_mean for value in actual]
        p = [value - predicted_mean for value in predicted]
        covariance = math.fsum(x * y for x, y in zip(a, p, strict=True))
        pearson_r2 = covariance**2 / (math.fsum(x*x for x in a) * math.fsum(y*y for y in p))
    return {
        "pearson_r2": pearson_r2,
        "mae": sum(abs(error) for error in errors) / count,
        "rmse": math.sqrt(sum(error**2 for error in errors) / count),
        "bias": sum(errors) / count,
    }


def validate_baseline_forecast(data_dir):
    """Forecast the second VTD from the first and score measured holdout bins."""
    passport = read_pipeline_csv((data_dir / "passport.csv").read_bytes())
    pipe = next(row for row in passport if row["id"] == INSPECTED_PIPE_ID)
    surveys = load_inspection_minima(data_dir, pipe)
    if len(surveys) < 2:
        raise ValueError("Для исторической проверки нужны два обследования ВТД.")
    first, held_out = sorted(surveys)[:2]
    measured = surveys[first]
    expected = surveys[held_out]
    measured_ids = sorted(measured)
    evaluated_ids = sorted(expected)
    actual = [expected[segment_id] for segment_id in evaluated_ids]
    persistence = [
        measured[segment_id] if segment_id in measured
        else interpolate_wall(segment_id, measured, measured_ids)
        for segment_id in evaluated_ids
    ]
    years = (held_out - first).days / 365.25
    rate = first_survey_rate(pipe, first, measured)
    predicted = [remaining_thickness(wall, rate, years) for wall in persistence]
    nominal = pipe["segments"][0]["thickness_pred"]
    from_commissioning_years = (held_out - date.fromisoformat(pipe["commissioned_at"])).days / 365.25
    no_vtd_baseline = [remaining_thickness(nominal, rate, from_commissioning_years)] * len(actual)
    return {
        "first": first, "held_out": held_out, "years": years,
        "measured_both": len(measured.keys() & expected.keys()),
        "actual": actual, "predicted": predicted,
        "metrics": {
            "Без изменения": forecast_metrics(actual, persistence),
            "Линейное утонение": forecast_metrics(actual, predicted),
            "От ввода, без локальных замеров": forecast_metrics(actual, no_vtd_baseline),
        },
    }
