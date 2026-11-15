from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_XLSX = ROOT / "data" / "raw_csv" / "all.xlsx"
CACHE = ROOT / "data" / "cache"
REPORTS = ROOT / "reports"

T_DATE = "2023-12-01"            # дата оцениваемого ВТД
TARGET_IDS = [1250022871]        # участки, для которых пишем result.csv
CASE1_IDS = [1250009490, 1250022871, 1250011604]
WT_NOM = 8.0
SEG_LEN = 10.0
SEED = 42
