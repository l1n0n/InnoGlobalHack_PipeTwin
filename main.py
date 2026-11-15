"""Полный пайплайн кейса 1: шаги 1–7 по порядку."""
import runpy
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(SRC))

STEPS = ["load", "clean", "segment", "features", "train", "predict", "plots"]

if __name__ == "__main__":
    for step in STEPS:
        print(f"\n=== {step} ===")
        runpy.run_path(str(SRC / f"{step}.py"), run_name="__main__")
