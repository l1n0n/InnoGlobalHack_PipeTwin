import pandas as pd

path = 'data/raw_csv/all.xlsx'

sheets = pd.read_excel(path, sheet_name=["profile", "passport", "regime", "uzt", "vtd"], engine="calamine")

profile = sheets["profile"]
passport = sheets["passport"]
regime = sheets["regime"]
uzt = sheets["uzt"]
vtd = sheets["vtd"]

print(profile)
print(passport)
print(regime)
print(uzt)
print(vtd)
