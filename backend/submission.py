"""Validation and serialization of the jury's result.csv format."""

import csv
import io
import math


RESULT_COLUMNS = ("id", "segment_id", "thickness_pred")


def result_csv(pipes):
    if len(pipes) != 2 or len({pipe["id"] for pipe in pipes}) != 2:
        raise ValueError("Для result.csv нужны два разных трубопроводных участка.")
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
