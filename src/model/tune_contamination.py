"""
Sweeps a range of contamination values per region and reports how precision,
recall, F1 and false-positive rate trade off against each other, so a
defensible operating point can be chosen per region rather than reusing one
blanket value everywhere.

Run from the project root: python src/model/tune_contamination.py
"""
from pathlib import Path

import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import precision_score, recall_score, f1_score

ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = ROOT / "data" / "processed"

REGIONS = ["nairobi", "kisumu", "tana_river_hola"]
CONTAMINATION_VALUES = [0.02, 0.03, 0.05, 0.07, 0.10]
N_ESTIMATORS = 200
RANDOM_STATE = 42
NON_FEATURE_COLS = {"date", "region"}

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


def evaluate_one(df: pd.DataFrame, feature_cols: list, contamination: float) -> tuple:
    X = df[feature_cols]
    model = IsolationForest(
        n_estimators=N_ESTIMATORS,
        max_samples="auto",
        contamination=contamination,
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    model.fit(X)
    predicted = pd.Series(model.predict(X) == -1, index=df.index)
    ground_truth = label_flood_windows(df["date"])

    precision = precision_score(ground_truth, predicted, zero_division=0)
    recall = recall_score(ground_truth, predicted, zero_division=0)
    f1 = f1_score(ground_truth, predicted, zero_division=0)
    fp = (predicted & ~ground_truth).sum()
    tn = (~predicted & ~ground_truth).sum()
    fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
    return precision, recall, f1, fpr, int(predicted.sum())


def main():
    df = pd.read_csv(PROCESSED_DIR / "features.csv", parse_dates=["date"])

    for region in REGIONS:
        region_df = df[df["region"] == region].sort_values("date").reset_index(drop=True)
        feature_cols = [c for c in region_df.columns if c not in NON_FEATURE_COLS]

        print(f"\n{region}")
        print(f"{'contamination':>13s} {'flagged':>8s} {'precision':>10s} {'recall':>8s} {'f1':>6s} {'fpr':>6s}")
        for c in CONTAMINATION_VALUES:
            precision, recall, f1, fpr, n_flagged = evaluate_one(region_df, feature_cols, c)
            print(f"{c:13.2f} {n_flagged:8d} {precision:10.3f} {recall:8.3f} {f1:6.3f} {fpr:6.3f}")


if __name__ == "__main__":
    main()
