"""
Loads the raw per-region JSON pulled by fetch_raw_data.py, merges weather +
river discharge for each region, cleans it, and writes one consolidated
master table to data/processed/master_dataset.csv.

Run from the project root: python src/data_pipeline/clean_and_consolidate.py
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

REGIONS = ["nairobi", "kisumu", "tana_river_hola"]

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

# Rename Open-Meteo's field names to the schema names used in the ERD/report.
# Note: "river_discharge" (m3/s) is what we can actually get for free -- it
# fills the same role as the "river_level" field in the original design, but
# it measures FLOW, not water height. Worth a one-line callout in the report
# if it comes up, since they are physically different units.
COLUMN_RENAME = {
    "precipitation_sum": "rainfall",
    "temperature_2m_mean": "temperature",
    "relative_humidity_2m_mean": "humidity",
    "soil_moisture_0_to_10cm_mean": "soil_moisture",
    "river_discharge": "river_discharge",
}


def load_daily(path: Path) -> pd.DataFrame:
    with open(path) as f:
        payload = json.load(f)
    daily = payload["daily"]
    df = pd.DataFrame(daily)
    df["time"] = pd.to_datetime(df["time"])
    return df.rename(columns={"time": "date"})


def load_region(region: str) -> pd.DataFrame:
    weather = load_daily(RAW_DIR / f"{region}_weather.json")
    flood = load_daily(RAW_DIR / f"{region}_flood.json")
    merged = weather.merge(flood, on="date", how="outer")
    merged = merged.rename(columns=COLUMN_RENAME)
    merged.insert(1, "region", region)
    return merged


def clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["region", "date"]).reset_index(drop=True)

    numeric_cols = ["rainfall", "temperature", "humidity", "soil_moisture", "river_discharge"]

    # Force every numeric column to a real numeric dtype first. A column that
    # is entirely null (e.g. a coordinate that missed the modelled river
    # channel) is read by pandas as generic "object" type, not float64 -- and
    # .interpolate() refuses to run on "object" columns. Coercing up front
    # makes the rest of this function work the same regardless of whether a
    # column is partially or completely missing.
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    before = df.isna().sum()

    # Physically impossible values -> clip rather than drop the whole row.
    df["rainfall"] = df["rainfall"].clip(lower=0)
    df["river_discharge"] = df["river_discharge"].clip(lower=0)
    df["humidity"] = df["humidity"].clip(lower=0, upper=100)

    # Short gaps (<=3 days): linear interpolation, per region.
    # Longer gaps: left as NaN on purpose rather than invented -- flagged below.
    for col in numeric_cols:
        df[col] = df.groupby("region")[col].transform(
            lambda s: s.interpolate(method="linear", limit=3, limit_direction="both")
        )

    df = df.drop_duplicates(subset=["region", "date"])

    after = df.isna().sum()

    print("\nMissing values before -> after short-gap interpolation:")
    for col in numeric_cols:
        print(f"  {col:15s} {before.get(col, 0):6d} -> {after.get(col, 0):6d}")

    # Flag any region/column combination that is still heavily null after
    # interpolation -- this is the real signal that a coordinate likely
    # missed the modelled river channel (see the Flood API's 5km-grid caveat).
    print("\nPer-region missing-value check (post-interpolation):")
    flagged = False
    for region, group in df.groupby("region"):
        n = len(group)
        for col in numeric_cols:
            missing = group[col].isna().sum()
            pct = 100 * missing / n
            if pct > 5:
                flagged = True
                print(f"  FLAG: {region:16s} {col:15s} {pct:5.1f}% missing ({missing}/{n} rows)")
    if not flagged:
        print("  none -- every region/column is under 5% missing.")

    return df


def main():
    frames = [load_region(r) for r in REGIONS]
    df = pd.concat(frames, ignore_index=True)
    df = clean(df)

    out_path = PROCESSED_DIR / "master_dataset.csv"
    df.to_csv(out_path, index=False)

    print(f"\nSaved {len(df):,} rows across {df['region'].nunique()} regions to {out_path}")
    print(f"Date range: {df['date'].min().date()} to {df['date'].max().date()}")
    print("\nRows per region:")
    print(df["region"].value_counts().to_string())


if __name__ == "__main__":
    main()
