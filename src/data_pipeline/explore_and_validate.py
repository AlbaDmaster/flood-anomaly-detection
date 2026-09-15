"""
Explores the cleaned master dataset and sanity-checks it against known flood
events. Saves plots to reports/figures/ and prints a quantitative comparison
of rainfall/river discharge during known flood windows vs. normal periods.

Run from the project root: python src/data_pipeline/explore_and_validate.py
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless backend -- saves files, needs no display
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = ROOT / "data" / "processed"
FIGURES_DIR = ROOT / "reports" / "figures"
FIGURES_DIR.mkdir(parents=True, exist_ok=True)

REGIONS = ["nairobi", "kisumu", "tana_river_hola"]
VARIABLES = ["rainfall", "temperature", "humidity", "soil_moisture", "river_discharge"]

# Known, well-documented flood windows -- evaluation only, never used in training.
FLOOD_WINDOWS = [
    ("2018 East Africa floods", "2018-03-01", "2018-06-30"),
    ("2023-2024 El Nino floods", "2023-10-01", "2024-05-31"),
]


def load_data() -> pd.DataFrame:
    path = PROCESSED_DIR / "master_dataset.csv"
    return pd.read_csv(path, parse_dates=["date"])


def print_missingness(df: pd.DataFrame) -> None:
    print("=" * 60)
    print("MISSING VALUES PER REGION")
    print("=" * 60)
    for region in REGIONS:
        sub = df[df["region"] == region]
        n = len(sub)
        print(f"\n{region} ({n} rows):")
        for col in VARIABLES:
            missing = sub[col].isna().sum()
            pct = 100 * missing / n
            flag = "  <-- still missing after cleaning" if pct > 5 else ""
            print(f"  {col:15s} {missing:5d} missing ({pct:5.1f}%){flag}")


def plot_timeseries(df: pd.DataFrame) -> None:
    for var in VARIABLES:
        fig, axes = plt.subplots(len(REGIONS), 1, figsize=(12, 8), sharex=True)
        fig.suptitle(f"{var} over time, by region")
        for ax, region in zip(axes, REGIONS):
            sub = df[df["region"] == region]
            ax.plot(sub["date"], sub[var], linewidth=0.6)
            ax.set_ylabel(region)
            for label, start, end in FLOOD_WINDOWS:
                ax.axvspan(pd.Timestamp(start), pd.Timestamp(end), color="red", alpha=0.15)
        axes[-1].set_xlabel("date")
        fig.tight_layout()
        out_path = FIGURES_DIR / f"{var}_timeseries.png"
        fig.savefig(out_path, dpi=120)
        plt.close(fig)
        print(f"saved {out_path}")


def print_flood_window_comparison(df: pd.DataFrame) -> None:
    print("\n" + "=" * 60)
    print("FLOOD WINDOW SANITY CHECK")
    print("(mean value during the flood window vs. the rest of the record)")
    print("=" * 60)
    for label, start, end in FLOOD_WINDOWS:
        start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
        print(f"\n{label} ({start} to {end}):")
        for region in REGIONS:
            sub = df[df["region"] == region]
            in_window = sub[(sub["date"] >= start_ts) & (sub["date"] <= end_ts)]
            outside = sub[(sub["date"] < start_ts) | (sub["date"] > end_ts)]
            if in_window.empty:
                continue
            print(f"  {region}:")
            for col in ["rainfall", "river_discharge"]:
                in_mean = in_window[col].mean()
                out_mean = outside[col].mean()
                if pd.isna(in_mean) or pd.isna(out_mean) or out_mean == 0:
                    print(f"    {col:15s} not enough data to compare")
                    continue
                ratio = in_mean / out_mean
                flag = "  <-- looks elevated, good sign" if ratio > 1.15 else "  <-- NOT elevated, worth a closer look"
                print(f"    {col:15s} flood-window mean {in_mean:8.2f} vs baseline {out_mean:8.2f} ({ratio:.2f}x){flag}")


def main():
    df = load_data()
    print_missingness(df)
    plot_timeseries(df)
    print_flood_window_comparison(df)
    print(f"\nAll plots saved to {FIGURES_DIR}/")


if __name__ == "__main__":
    main()
