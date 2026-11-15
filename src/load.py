"""Шаг 1: чтение all.xlsx с кэшем в parquet."""
import pandas as pd

from config import CACHE, RAW_XLSX

SHEETS = ["profile", "passport", "sample_result", "regime", "uzt", "vtd"]


def load(name: str) -> pd.DataFrame:
    path = CACHE / f"{name}.parquet"
    if not path.exists():
        load_all()
    return pd.read_parquet(path)


def load_all() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    if all((CACHE / f"{s}.parquet").exists() for s in SHEETS):
        return
    sheets = pd.read_excel(RAW_XLSX, sheet_name=SHEETS, engine="calamine")
    for name, df in sheets.items():
        # смешанные типы в object-колонках ломают pyarrow, приводим к строкам
        for c in df.columns[df.dtypes == object]:
            if df[c].map(type).nunique() > 1:
                df[c] = df[c].astype("string")
        df.to_parquet(CACHE / f"{name}.parquet", index=False)


if __name__ == "__main__":
    load_all()
    for s in SHEETS:
        print(s, load(s).shape)
