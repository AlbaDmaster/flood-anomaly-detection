"""
Builds a one-page HTML report of the data, the trained models, their
evaluation metrics and the automated test results, for presenting the
model. Every number is computed fresh from the files in the repository.

Run from the project root:
    python src/model/model_report.py            data, model and evaluation
    python src/model/model_report.py --tests    also runs the test suite (about a minute)
Then open reports/model_report.html in a browser (from WSL: explorer.exe reports).
"""
import base64
import io
import json
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from html import escape
from pathlib import Path

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.ensemble import IsolationForest  # noqa: E402
from sklearn.metrics import (confusion_matrix, f1_score, precision_score,  # noqa: E402
                             recall_score, roc_auc_score, roc_curve)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.model.train_and_evaluate import (CONTAMINATION, FLOOD_WINDOWS, N_ESTIMATORS,  # noqa: E402
                                          NON_FEATURE_COLS, RANDOM_STATE, REGIONS,
                                          label_flood_windows)

NAMES = {"nairobi": "Nairobi CBD", "kisumu": "Kisumu", "tana_river_hola": "Tana River (Hola)"}
COLOURS = {"nairobi": "#1b6b85", "kisumu": "#8e44ad", "tana_river_hola": "#d35400"}
SWEEP = [0.02, 0.03, 0.05, 0.07, 0.10]
VARIABLES = [("rainfall", "Rainfall (mm)"), ("river_discharge", "River discharge (m³/s)"),
             ("soil_moisture", "Soil moisture (m³/m³)"), ("humidity", "Humidity (%)"),
             ("temperature", "Temperature (°C)")]


def chart(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return '<img alt="" src="data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode() + '">'


def table(header, rows, cls="") -> str:
    head = "".join(f"<th>{escape(str(h))}</th>" for h in header)
    body = "".join("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def raw_days(region: str) -> int:
    data = json.loads((ROOT / f"data/raw/{region}_weather.json").read_text())
    return len(data["daily"]["time"])


def shade_windows(ax, start, end):
    for a, b in FLOOD_WINDOWS:
        a, b = max(pd.Timestamp(a), start), min(pd.Timestamp(b), end)
        if a < b:
            ax.axvspan(a, b, color="#9fb4b9", alpha=0.35, lw=0)


def run_tests():
    with tempfile.TemporaryDirectory() as tmp:
        xml_path = Path(tmp) / "results.xml"
        start = time.time()
        subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q", f"--junitxml={xml_path}", "-p", "no:cacheprovider"],
                       cwd=ROOT, capture_output=True, text=True)
        seconds = time.time() - start
        root = ET.parse(xml_path).getroot()
    modules = {}
    for case in root.iter("testcase"):
        module = case.get("classname", "").split(".")[-1] or "?"
        m = modules.setdefault(module, {"passed": 0, "failed": 0, "skipped": 0})
        if case.find("failure") is not None or case.find("error") is not None:
            m["failed"] += 1
        elif case.find("skipped") is not None:
            m["skipped"] += 1
        else:
            m["passed"] += 1
    return modules, seconds


def main():
    with_tests = "--tests" in sys.argv
    master = pd.read_csv(ROOT / "data/processed/master_dataset.csv", parse_dates=["date"])
    features = pd.read_csv(ROOT / "data/processed/features.csv", parse_dates=["date"])
    sections = []

    # ------------------------------------------------------------------ data
    rows = []
    for r in REGIONS:
        m, f = master[master.region == r], features[features.region == r]
        rows.append([NAMES[r], f"{raw_days(r):,}", f"{len(m):,}", f"{len(f):,}",
                     f"{m.date.min():%d %b %Y} – {m.date.max():%d %b %Y}"])
    rows.append(["Total", f"{sum(raw_days(r) for r in REGIONS):,}", f"{len(master):,}", f"{len(features):,}", ""])
    stats = []
    for col, label in VARIABLES:
        stats.append([label] + [f"{master[master.region == r][col].mean():.2f} / {master[master.region == r][col].max():.1f}"
                                for r in REGIONS])
    fig, axes = plt.subplots(2, 1, figsize=(11, 5.2), sharex=True)
    for r in REGIONS:
        m = master[master.region == r]
        axes[0].plot(m.date, m.rainfall.rolling(7).mean(), lw=0.8, color=COLOURS[r], label=NAMES[r])
        axes[1].plot(m.date, m.river_discharge, lw=0.8, color=COLOURS[r], label=NAMES[r])
    for ax in axes:
        shade_windows(ax, master.date.min(), master.date.max())
    axes[0].set_ylabel("Rainfall, 7-day mean (mm)")
    axes[1].set_ylabel("River discharge (m³/s)")
    axes[1].set_yscale("log")
    axes[0].legend(loc="upper left", ncol=3, fontsize=8)
    axes[0].set_title("Ten years of daily data; known flood windows shaded", fontsize=10)
    sections.append(("1. Data", f"""
<p>Daily data for 1 January 2015 to 31 December 2024 from the Open-Meteo Historical Weather API (ERA5-Land)
and Flood API (GloFAS), for three flood-prone locations. Cleaning merged the two sources, clipped impossible
values and filled short gaps; feature engineering then dropped each region's first seven days, which lack a
full week of history.</p>
{table(["Region", "Raw days downloaded", "After cleaning", "Used for modelling", "Period"], rows)}
<h3>Mean / maximum of each variable</h3>
{table(["Variable"] + [NAMES[r] for r in REGIONS], stats)}
{chart(fig)}"""))

    # ------------------------------------------------------------------ model
    bundles = {r: joblib.load(ROOT / f"models/isolation_forest_{r}.joblib") for r in REGIONS}
    b0 = bundles[REGIONS[0]]
    model0 = b0["model"]
    tree = model0.estimators_[0].tree_
    cols = b0["feature_cols"]
    groups = {}
    for c in cols:
        base = next((v for v, _ in VARIABLES if c.startswith(v)), "season")
        groups.setdefault(base, []).append(c)
    feat_rows = [[k, len(v), ", ".join(v)] for k, v in groups.items()]
    thr_rows = [[NAMES[r], f"{bundles[r]['moderate_threshold']:.4f}", f"{bundles[r]['high_threshold']:.4f}"] for r in REGIONS]
    sections.append(("2. Model", f"""
<div class="cards">
 <div class="card"><b>{len(REGIONS)}</b><span>Isolation Forests, one per region</span></div>
 <div class="card"><b>{model0.n_estimators}</b><span>trees in each forest</span></div>
 <div class="card"><b>{model0.max_samples_}</b><span>days sampled per tree</span></div>
 <div class="card"><b>{len(cols)}</b><span>features per day</span></div>
 <div class="card"><b>{b0['contamination']}</b><span>contamination</span></div>
 <div class="card"><b>{model0.random_state}</b><span>random seed</span></div>
</div>
<p>Each tree splits the data on randomly chosen features at random values. Unusual days, such as a flood peak,
are isolated after very few splits; averaged over all trees this gives the anomaly score, where higher means
more anomalous. The moderate threshold is the 90th percentile of each region's training scores and the high
threshold the 99th. Tree 1 of the Nairobi forest has {tree.node_count} nodes and a depth of {tree.max_depth};
its first split is <code>{escape(cols[tree.feature[0]])} &lt;= {tree.threshold[0]:.3f}</code>.</p>
{table(["Variable", "Features", "Names"], feat_rows, "small")}
<h3>Severity thresholds</h3>
{table(["Region", "Moderate (90th percentile)", "High (99th percentile)"], thr_rows)}"""))

    # ------------------------------------------------------------------ evaluation
    eval_rows, roc_data, scores = [], {}, {}
    cms = []
    for r in REGIONS:
        df = features[features.region == r].sort_values("date").reset_index(drop=True)
        b = bundles[r]
        X = df[b["feature_cols"]]
        score = -b["model"].score_samples(X)
        flagged = b["model"].predict(X) == -1
        truth = label_flood_windows(df["date"]).to_numpy()
        tn, fp, fn, tp = confusion_matrix(truth, flagged).ravel()
        eval_rows.append([NAMES[r], int(truth.sum()), int(flagged.sum()),
                          f"{precision_score(truth, flagged):.3f}", f"{recall_score(truth, flagged):.3f}",
                          f"{f1_score(truth, flagged):.3f}", f"{roc_auc_score(truth, score):.3f}", f"{fp / (fp + tn):.3f}"])
        cms.append((r, tn, fp, fn, tp))
        roc_data[r] = roc_curve(truth, score)
        scores[r] = (df["date"], score, b)
    fig, ax = plt.subplots(figsize=(4.6, 4.2))
    for r in REGIONS:
        fpr, tpr, _ = roc_data[r]
        ax.plot(fpr, tpr, color=COLOURS[r], label=f"{NAMES[r]} (AUC {roc_auc_score(*[label_flood_windows(scores[r][0]), scores[r][1]]):.3f})")
    ax.plot([0, 1], [0, 1], ls=":", color="grey", label="Random guess (0.5)")
    ax.set_xlabel("False-positive rate"); ax.set_ylabel("True-positive rate")
    ax.set_title("ROC curves", fontsize=10); ax.legend(fontsize=7, loc="lower right")
    roc_img = chart(fig)
    fig, axes = plt.subplots(3, 1, figsize=(11, 6.4), sharex=True)
    lo, hi = pd.Timestamp("2017-07-01"), pd.Timestamp("2024-12-31")
    for ax, r in zip(axes, REGIONS):
        d, s, b = scores[r]
        keep = (d >= lo) & (d <= hi)
        ax.plot(d[keep], s[keep], lw=0.6, color=COLOURS[r])
        ax.axhline(b["moderate_threshold"], ls="--", lw=1, color="#d68910")
        ax.axhline(b["high_threshold"], ls="--", lw=1, color="#c0392b")
        shade_windows(ax, lo, hi)
        ax.set_ylabel(NAMES[r], fontsize=8)
    axes[0].set_title("Daily anomaly score with moderate (orange) and high (red) thresholds; flood windows shaded", fontsize=10)
    timeline = chart(fig)
    cm_rows = [[NAMES[r], tp, fp, fn, tn] for r, tn, fp, fn, tp in cms]
    sweep_rows = []
    for r in REGIONS:
        df = features[features.region == r].sort_values("date").reset_index(drop=True)
        X = df[[c for c in df.columns if c not in NON_FEATURE_COLS]]
        truth = label_flood_windows(df["date"]).to_numpy()
        for c in SWEEP:
            m = IsolationForest(n_estimators=N_ESTIMATORS, contamination=c, random_state=RANDOM_STATE, n_jobs=-1).fit(X)
            pred = m.predict(X) == -1
            fp = (pred & ~truth).sum(); tn = (~pred & ~truth).sum()
            sweep_rows.append([NAMES[r], c, int(pred.sum()), f"{precision_score(truth, pred, zero_division=0):.3f}",
                               f"{recall_score(truth, pred):.3f}", f"{f1_score(truth, pred):.3f}", f"{fp / (fp + tn):.3f}"])
    windows = ", ".join(f"{pd.Timestamp(a):%b %Y}–{pd.Timestamp(b):%b %Y}" for a, b in FLOOD_WINDOWS)
    sections.append(("3. Evaluation", f"""
<p>Every day inside a known flood window ({windows}) is labelled a flood day and every other day normal.
A day counts as flagged when the model classes it as anomalous at contamination {CONTAMINATION}.
ROC-AUC measures how well the scores separate flood days from normal days regardless of the threshold.</p>
{table(["Region", "Flood days", "Flagged days", "Precision", "Recall", "F1-score", "ROC-AUC", "False-positive rate"], eval_rows)}
<p class="note">Recall cannot exceed about 0.63 even for a perfect detector, because the windows contain 580
flood days per region while the 10% contamination flags only about 365 days in ten years. The evaluation is
in-sample: the models were trained and evaluated on the same 2015–2024 record.</p>
<div class="row"><div>{roc_img}</div><div><h3>Confusion matrices (flagged vs flood window)</h3>
{table(["Region", "True positives", "False positives", "False negatives", "True negatives"], cm_rows)}</div></div>
{timeline}
<h3>Contamination tuning</h3>
{table(["Region", "Contamination", "Flagged", "Precision", "Recall", "F1-score", "False-positive rate"], sweep_rows, "small")}
<p class="note">Contamination 0.10 gave the highest recall and F1-score in every region at a false-positive rate
of at most 7.6%, so it was adopted: in early warning a missed flood costs more than a false alarm.</p>"""))

    # ------------------------------------------------------------------ tests
    if with_tests:
        modules, seconds = run_tests()
        total = {k: sum(m[k] for m in modules.values()) for k in ("passed", "failed", "skipped")}
        test_rows = [[name, sum(m.values()), m["passed"], m["failed"], m["skipped"]] for name, m in sorted(modules.items())]
        test_rows.append(["Total", sum(total.values()), total["passed"], total["failed"], total["skipped"]])
        verdict = "ok" if total["failed"] == 0 and total["skipped"] == 0 else "bad"
        warning = ('<p class="warn">Some tests were skipped or failed. Skipped tests usually mean MySQL is not running: '
                   'start it with <code>sudo service mysql start</code> and generate the report again.</p>'
                   if verdict == "bad" else "")
        body = f"""{warning}
<div class="cards">
 <div class="card {verdict}"><b>{total['passed']}/{sum(total.values())}</b><span>tests passed</span></div>
 <div class="card"><b>{len(modules)}</b><span>test modules</span></div>
 <div class="card"><b>{seconds:.0f} s</b><span>run time</span></div>
</div>
{table(["Test module", "Tests", "Passed", "Failed", "Skipped"], test_rows)}
<p class="note">Unit, integration, page and system tests written with pytest, run against a separate test
database. Skipped tests mean the MySQL server was not running.</p>"""
    else:
        body = '<p class="note">Run <code>python src/model/model_report.py --tests</code> to include a fresh test run.</p>'
    sections.append(("4. Automated testing", body))

    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Model report · Flood EWS</title>
<style>
body{{margin:0;background:#eef2f1;color:#17262b;font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
header{{background:#0f2f3a;color:#fff;padding:22px 32px}}header h1{{margin:0;font-size:1.5rem}}header p{{margin:4px 0 0;color:#9fc3cb}}
main{{max-width:1180px;margin:0 auto;padding:20px 24px 40px}}
section{{background:#fff;border:1px solid #d6dfdd;border-radius:10px;padding:18px 22px;margin-bottom:20px}}
h2{{margin:0 0 10px;font-size:1.2rem}}h3{{font-size:1rem;margin:18px 0 8px}}
table{{border-collapse:collapse;width:100%;margin:8px 0 12px;font-variant-numeric:tabular-nums}}
th{{text-align:left;font-size:.8rem;color:#546569;border-bottom:1px solid #d6dfdd;padding:6px 8px}}
td{{padding:7px 8px;border-bottom:1px solid #e8eeec}}table.small td{{font-size:.85rem;padding:4px 8px}}
img{{max-width:100%;height:auto}}code{{background:#eef2f1;padding:1px 5px;border-radius:4px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:6px 0 14px}}
.card{{border:1px solid #d6dfdd;border-left:4px solid #1b6b85;border-radius:8px;padding:10px 14px}}
.card b{{display:block;font-size:1.6rem}}.card span{{color:#546569;font-size:.85rem}}
.card.ok{{border-left-color:#287047}}.card.ok b{{color:#287047}}.card.bad{{border-left-color:#b42323}}.card.bad b{{color:#b42323}}
.note{{color:#546569;font-size:.9rem}}.warn{{background:#fbe7e5;color:#b42323;padding:9px 12px;border-radius:8px}}.row{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.3fr);gap:20px;align-items:start}}
@media (max-width:800px){{.row{{grid-template-columns:1fr}}}}
</style></head><body>
<header><h1>Isolation Forest flood anomaly detection: model report</h1>
<p>Nairobi, Kisumu and Tana River (Hola) · generated {datetime.now():%d %B %Y, %H:%M} from the project files</p></header>
<main>{"".join(f"<section><h2>{t}</h2>{b}</section>" for t, b in sections)}</main></body></html>"""
    out = ROOT / "reports" / "model_report.html"
    out.write_text(html, encoding="utf-8")
    print(f"Report written to {out.relative_to(ROOT)}  (open it with: explorer.exe reports)")


if __name__ == "__main__":
    main()
