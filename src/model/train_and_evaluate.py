"""
Trains one Isolation Forest per region on the full engineered feature set,
using contamination=0.10 (chosen after the Step 8 tuning sweep -- see the
report for the precision/recall trade-off table and the early-warning
reasoning for favouring recall over precision). Evaluates each model
against the four known flood windows, and saves each model together with
its feature column order and two severity thresholds so a single new
reading can be scored consistently at inference time.

Run from the project root: python src/model/train_and_evaluate.py
"""

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import precision_score, recall_score, f1_score, roc_auc_score

ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = ROOT / "data" / "processed"
MODELS_DIR = ROOT / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

REGIONS = ["nairobi", "kisumu", "tana_river_hola"]

FLOOD_WINDOWS = [
    ("2018-03-01", "2018-06-30"),
    ("2019-10-01", "2019-12-31"),
    ("2020-04-01", "2020-07-31"),
    ("2023-10-01", "2024-05-31"),
]

CONTAMINATION = 0.10
HIGH_SEVERITY_PERCENTILE = 99
N_ESTIMATORS = 200
RANDOM_STATE = 42

NON_FEATURE_COLS = {"date", "region"}


def label_flood_windows(dates: pd.Series) -> pd.Series:
    is_flood = pd.Series(False, index=dates.index)
    for start, end in FLOOD_WINDOWS:
        is_flood |= (dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))
    return is_flood


def train_and_evaluate_region(df: pd.DataFrame, region: str) -> dict:
    df = df.sort_values("date").reset_index(drop=True)
    feature_cols = [c for c in df.columns if c not in NON_FEATURE_COLS]
    X = df[feature_cols]

    model = IsolationForest(
        n_estimators=N_ESTIMATORS,
        max_samples="auto",
        contamination=CONTAMINATION,
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    model.fit(X)

    anomaly_score = -model.score_samples(X)
    predicted_anomaly = pd.Series(model.predict(X) == -1, index=df.index)
    ground_truth = label_flood_windows(df["date"])

    precision = precision_score(ground_truth, predicted_anomaly, zero_division=0)
    recall = recall_score(ground_truth, predicted_anomaly, zero_division=0)
    f1 = f1_score(ground_truth, predicted_anomaly, zero_division=0)
    auc = roc_auc_score(ground_truth, anomaly_score)

    fp = (predicted_anomaly & ~ground_truth).sum()
    tn = (~predicted_anomaly & ~ground_truth).sum()
    fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0

    moderate_threshold = float(np.percentile(anomaly_score, 100 * (1 - CONTAMINATION)))
    high_threshold = float(np.percentile(anomaly_score, HIGH_SEVERITY_PERCENTILE))

    model_path = MODELS_DIR / f"isolation_forest_{region}.joblib"
    joblib.dump(
        {
            "model": model,
            "feature_cols": feature_cols,
            "moderate_threshold": moderate_threshold,
            "high_threshold": high_threshold,
            "contamination": CONTAMINATION,
        },
        model_path,
    )

    return {
        "region": region,
        "n_flood_days": int(ground_truth.sum()),
        "n_flagged": int(predicted_anomaly.sum()),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "auc": auc,
        "fpr": fpr,
        "moderate_threshold": moderate_threshold,
        "high_threshold": high_threshold,
    }


def main():
    df = pd.read_csv(PROCESSED_DIR / "features.csv", parse_dates=["date"])
    print(f"Loaded {len(df):,} rows, {df.shape[1]} columns from features.csv")
    print(f"Using contamination={CONTAMINATION} (locked in during Step 8 tuning)\n")

    results = []
    for region in REGIONS:
        region_df = df[df["region"] == region]
        result = train_and_evaluate_region(region_df, region)
        results.append(result)

    print(f"{'region':16s} {'flood days':>10s} {'flagged':>8s} {'precision':>10s} "
          f"{'recall':>8s} {'f1':>6s} {'auc':>6s} {'fpr':>6s}")
    print("=" * 82)
    for r in results:
        print(f"{r['region']:16s} {r['n_flood_days']:10d} {r['n_flagged']:8d} "
              f"{r['precision']:10.3f} {r['recall']:8.3f} {r['f1']:6.3f} "
              f"{r['auc']:6.3f} {r['fpr']:6.3f}")

    print("\nSeverity thresholds saved per region (anomaly score scale, region-specific):")
    for r in results:
        print(f"  {r['region']:16s} moderate >= {r['moderate_threshold']:.4f}   "
              f"high >= {r['high_threshold']:.4f}")

    print(f"\nModels saved to {MODELS_DIR}/")


if __name__ == "__main__":
    main()
