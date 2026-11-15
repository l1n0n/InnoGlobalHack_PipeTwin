"""Validation and serialization of forecasts in result.csv format."""

import csv
import io
import math


RESULT_COLUMNS = ("id", "segment_id", "thickness_pred")


def result_csv(pipes):
    if not pipes or len({pipe["id"] for pipe in pipes}) != len(pipes):
        raise ValueError("Для result.csv нужен хотя бы один участок без повторов.")
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(RESULT_COLUMNS)
    for pipe in sorted(pipes, key=lambda item: item["id"]):
        if pipe.get("coordinate_basis") == "inspection_order":
            raise ValueError("Порядковые номера записей ВТД нельзя использовать как segment_id.")
        segments = sorted(pipe["segments"], key=lambda row: row["segment_id"])
        expected = math.ceil(pipe["length_m"] / 10) if pipe.get("length_m") else len(segments)
        if not segments or len(segments) != expected or any(
            segment["segment_id"] != index for index, segment in enumerate(segments)
        ):
            raise ValueError(f"Участок {pipe['id']}: нужны все сегменты от 0 без пропусков и повторов.")
        for segment in segments:
            value = segment["thickness_pred"]
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"Участок {pipe['id']}: недопустимая толщина в сегменте {segment['segment_id']}.")
            writer.writerow((pipe["id"], segment["segment_id"], value))
    return output.getvalue()
