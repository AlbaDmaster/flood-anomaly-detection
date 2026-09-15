"""
Engineers temporal features (lags, rolling stats, rate-of-change) from the
cleaned master dataset, plus a cyclical seasonal encoding. Isolation Forest
scores each row independently, so these features are what let it see how
conditions are trending, not just a single day's snapshot.

Run from the project root: python src/features/build_features.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = ROOT / "data" / "processed"

RAW_VARS = ["rainfall", "temperature", "humidity", "soil_moisture", "river_discharge"]
LAGS = [1, 7]
ROLLING_WINDOWS = [3, 7]


def add_features_for_region(group: pd.DataFrame) -> pd.DataFrame:
    group = group.sort_values("date").reset_index(drop=True)
    for var in RAW_VARS:
        for lag in LAGS:
            group[f"{var}_lag{lag}"] = group[var].shift(lag)
        for window in ROLLING_WINDOWS:
            group[f"{var}_roll_mean{window}"] = group[var].rolling(window, min_periods=1).mean()
        group[f"{var}_roll_std7"] = group[var].rolling(7, min_periods=2).std()
        group[f"{var}_rate1"] = group[var].diff(1)
    return group


def add_seasonal_features(df: pd.DataFrame) -> pd.DataFrame:
    day_of_year = df["date"].dt.dayofyear
    df["season_sin"] = np.sin(2 * np.pi * day_of_year / 365.25)
    df["season_cos"] = np.cos(2 * np.pi * day_of_year / 365.25)
    return df


def main():
    df = pd.read_csv(PROCESSED_DIR / "master_dataset.csv", parse_dates=["date"])

    df = (
        df.groupby("region", group_keys=False)[df.columns]
        .apply(add_features_for_region)
        .reset_index(drop=True)
    )
    df = add_seasonal_features(df)

    # Lags/rolling windows leave NaNs at the start of each region's series --
    # there's no way to have a 7-day lag on day 1. Drop those rows rather
    # than fabricate values for them.
    before = len(df)
    df = df.dropna().reset_index(drop=True)
    dropped = before - len(df)

    out_path = PROCESSED_DIR / "features.csv"
    df.to_csv(out_path, index=False)

    print(f"Engineered dataset has {df.shape[1]} columns from {len(RAW_VARS)} raw variables.")
    print(f"Dropped {dropped} warm-up rows with incomplete rolling windows "
          f"({dropped / before * 100:.2f}% of {before}).")
    print(f"Saved {len(df):,} rows to {out_path}")
    print("\nRows per region after dropping warm-up rows:")
    print(df["region"].value_counts().to_string())


if __name__ == "__main__":
    main()
