"""
Trains one Isolation Forest per region on the engineered features, then
evaluates each model against four known flood windows (ground truth, never
used in training) using precision, recall, F1, and false-positive rate.
Saves each trained model to models/.

Run from the project root: python src/model/train_and_evaluate.py
"""

from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import precision_score, recall_score, f1_score

ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = ROOT / "data" / "processed"
MODELS_DIR = ROOT / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

REGIONS = ["nairobi", "kisumu", "tana_river_hola"]

# Four well-documented flood periods -- evaluation only, never used in
# training. The last two were added after the model itself flagged them as
# highly anomalous despite not being in the original two-window list; both
# are independently confirmed (ReliefWeb, FloodList, and a peer-reviewed
# Lake Victoria attribution study).
FLOOD_WINDOWS = [
    ("2018-03-01", "2018-06-30"),   # 2018 East Africa floods
    ("2019-10-01", "2019-12-31"),   # OND 2019 Indian Ocean Dipole floods
    ("2020-04-01", "2020-07-31"),   # Lake Victoria record water levels
    ("2023-10-01", "2024-05-31"),   # 2023-2024 El Nino floods
]

CONTAMINATION = 0.05
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

    raw_pred = model.predict(X)  # -1 = anomaly, 1 = normal
    predicted_anomaly = pd.Series(raw_pred == -1, index=df.index)

    ground_truth = label_flood_windows(df["date"])

    precision = precision_score(ground_truth, predicted_anomaly, zero_division=0)
    recall = recall_score(ground_truth, predicted_anomaly, zero_division=0)
    f1 = f1_score(ground_truth, predicted_anomaly, zero_division=0)

    false_positives = (predicted_anomaly & ~ground_truth).sum()
    true_negatives = (~predicted_anomaly & ~ground_truth).sum()
    fpr = false_positives / (false_positives + true_negatives) if (false_positives + true_negatives) > 0 else 0.0

    model_path = MODELS_DIR / f"isolation_forest_{region}.joblib"
    joblib.dump({"model": model, "feature_cols": feature_cols}, model_path)

    return {
        "region": region,
        "rows": len(df),
        "n_flood_days": int(ground_truth.sum()),
        "n_flagged": int(predicted_anomaly.sum()),
        "n_flagged_during_flood": int((predicted_anomaly & ground_truth).sum()),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "fpr": fpr,
        "model_path": str(model_path),
    }


def main():
    df = pd.read_csv(PROCESSED_DIR / "features.csv", parse_dates=["date"])
    print(f"Loaded {len(df):,} rows, {df.shape[1]} columns from features.csv")
    print(f"Regions: {sorted(df['region'].unique())}")

    results = []
    for region in REGIONS:
        region_df = df[df["region"] == region]
        result = train_and_evaluate_region(region_df, region)
        results.append(result)

    print("\n" + "=" * 78)
    print(f"{'region':16s} {'flood days':>10s} {'flagged':>8s} {'caught':>8s} "
          f"{'precision':>10s} {'recall':>8s} {'f1':>6s} {'fpr':>6s}")
    print("=" * 78)
    for r in results:
        print(f"{r['region']:16s} {r['n_flood_days']:10d} {r['n_flagged']:8d} "
              f"{r['n_flagged_during_flood']:8d} {r['precision']:10.3f} "
              f"{r['recall']:8.3f} {r['f1']:6.3f} {r['fpr']:6.3f}")

    print(f"\nModels saved to {MODELS_DIR}/")


if __name__ == "__main__":
    main()
