"""
Shows what a trained regional Isolation Forest is and how it scores days.
Run from the project root:  python src/model/show_model.py [region] [--plot]
region: nairobi, kisumu or tana_river_hola (default tana_river_hola)
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.model.predict import MIN_HISTORY_DAYS, load_model, predict_anomaly  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
region = args[0] if args else "tana_river_hola"
bundle = load_model(region)
model = bundle["model"]

print(f"\n=== Isolation Forest for {region} (models/isolation_forest_{region}.joblib) ===")
print(f"Trees (n_estimators):     {model.n_estimators}")
print(f"Days sampled per tree:    {model.max_samples_}")
print(f"Contamination:            {bundle['contamination']}")
print(f"Random seed:              {model.random_state}")
print(f"Moderate threshold (P90): {bundle['moderate_threshold']:.4f}")
print(f"High threshold (P99):     {bundle['high_threshold']:.4f}")
print(f"Features ({len(bundle['feature_cols'])}):")
cols = bundle["feature_cols"]
for i in range(0, len(cols), 4):
    print("   " + ", ".join(cols[i:i + 4]))

tree = model.estimators_[0].tree_
print(f"\nTree 1 of {model.n_estimators}: {tree.node_count} nodes, depth {tree.max_depth}. First splits:")
for node in (0, tree.children_left[0], tree.children_right[0]):
    if tree.feature[node] >= 0:
        print(f"   node {node}: {cols[tree.feature[node]]} <= {tree.threshold[node]:.3f}")

master = pd.read_csv(ROOT / "data/processed/master_dataset.csv", parse_dates=["date"])
series = master[master["region"] == region].sort_values("date").reset_index(drop=True)

def score(day):
    end = series.index[series["date"] == pd.Timestamp(day)][0]
    return predict_anomaly(region, series.iloc[end - MIN_HISTORY_DAYS + 1:end + 1])

print("\nScoring single days (a higher score means more anomalous):")
examples = {"tana_river_hola": ["2018-02-01", "2018-04-25", "2018-05-07"],
            "nairobi": ["2024-02-01", "2024-04-15", "2024-04-30"],
            "kisumu": ["2024-02-01", "2024-04-15", "2024-05-02"]}.get(region, [])
for day in examples:
    r = score(day)
    row = series[series["date"] == pd.Timestamp(day)].iloc[0]
    print(f"   {day}: score {r['anomaly_score']:.3f} -> {r['severity'].upper():8s} "
          f"(rain {row['rainfall']:.1f} mm, discharge {row['river_discharge']:.1f} m3/s)")

if "--plot" in sys.argv:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    days = series["date"][(series["date"] >= "2018-01-01") & (series["date"] <= "2018-08-31")]
    scores = [score(d)["anomaly_score"] for d in days]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(days, scores, lw=1.2, label="Anomaly score")
    ax.axhline(bundle["moderate_threshold"], ls="--", c="orange", label="Moderate threshold")
    ax.axhline(bundle["high_threshold"], ls="--", c="red", label="High threshold")
    ax.set_title(f"{region}: daily anomaly score, January to August 2018")
    ax.legend(); fig.tight_layout()
    out = ROOT / "reports/figures" / f"model_scores_{region}_2018.png"
    fig.savefig(out, dpi=120)
    print(f"\nChart saved to {out.relative_to(ROOT)}")
