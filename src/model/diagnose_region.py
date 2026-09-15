"""
Diagnostic: computes threshold-independent ROC-AUC / average precision per
region against the four known flood windows, and prints the most anomalous
days with their raw values.

Run from the project root: python src/model/diagnose_region.py
"""
from pathlib import Path

import joblib
import pandas as pd
from sklearn.metrics import roc_auc_score, average_precision_score

ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = ROOT / "data" / "processed"
MODELS_DIR = ROOT / "models"

REGIONS = ["nairobi", "kisumu", "tana_river_hola"]
FLOOD_WINDOWS = [
    ("2018-03-01", "2018-06-30"),
    ("2019-10-01", "2019-12-31"),
    ("2020-04-01", "2020-07-31"),
    ("2023-10-01", "2024-05-31"),
]


def label_flood_windows(dates: pd.Series) -> pd.Series:
    is_flood = pd.Series(False, index=dates.index)
    for start, end in FLOOD_WINDOWS:
        is_flood |= (dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))
    return is_flood


def main():
    df = pd.read_csv(PROCESSED_DIR / "features.csv", parse_dates=["date"])

    print("=" * 70)
    print("THRESHOLD-INDEPENDENT SEPARABILITY (ROC-AUC / Average Precision)")
    print("(0.50 AUC = no better than random; 1.00 = perfect separation)")
    print("=" * 70)

    for region in REGIONS:
        region_df = df[df["region"] == region].sort_values("date").reset_index(drop=True)
        loaded = joblib.load(MODELS_DIR / f"isolation_forest_{region}.joblib")
        model, feature_cols = loaded["model"], loaded["feature_cols"]

        X = region_df[feature_cols]
        anomaly_score = -model.score_samples(X)
        ground_truth = label_flood_windows(region_df["date"])

        auc = roc_auc_score(ground_truth, anomaly_score)
        ap = average_precision_score(ground_truth, anomaly_score)
        print(f"\n{region}: ROC-AUC = {auc:.3f}   Average Precision = {ap:.3f}")

        region_df["anomaly_score"] = anomaly_score
        top15 = region_df.sort_values("anomaly_score", ascending=False).head(15)
        print(f"  Top 15 most anomalous days for {region}:")
        cols_to_show = ["date", "rainfall", "river_discharge", "soil_moisture", "anomaly_score"]
        print(top15[cols_to_show].to_string(index=False))


if __name__ == "__main__":
    main()
