"""
Reusable inference function: given a region and a recent window of raw
daily readings (rainfall, temperature, humidity, soil_moisture,
river_discharge), computes the same engineered features used in training
and returns an anomaly score, a flag, and a severity tier for the most
recent day in that window.

This is the function the Flask API imports directly -- it does not retrain
anything, it only loads the already-trained model bundle for the requested
region and scores one new reading using the recent history for context.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import joblib
import pandas as pd

from src.features.build_features import add_features_for_region, add_seasonal_features, LAGS

MODELS_DIR = ROOT / "models"

MIN_HISTORY_DAYS = max(LAGS) + 1

_loaded_models = {}


def _load_model(region: str) -> dict:
    if region not in _loaded_models:
        path = MODELS_DIR / f"isolation_forest_{region}.joblib"
        if not path.exists():
            raise FileNotFoundError(f"No trained model found for region '{region}' at {path}")
        _loaded_models[region] = joblib.load(path)
    return _loaded_models[region]


def predict_anomaly(region: str, recent_readings: pd.DataFrame) -> dict:
    """
    region: one of "nairobi", "kisumu", "tana_river_hola"
    recent_readings: DataFrame with columns date, rainfall, temperature,
        humidity, soil_moisture, river_discharge, sorted oldest to newest.
        Must contain at least MIN_HISTORY_DAYS rows -- the LAST row is the
        reading that gets scored; earlier rows exist purely to give lag and
        rolling features enough history to compute.

    Returns a dict: date, region, anomaly_score, is_anomaly, severity.
    """
    if len(recent_readings) < MIN_HISTORY_DAYS:
        raise ValueError(
            f"Need at least {MIN_HISTORY_DAYS} days of history to score a new "
            f"reading (got {len(recent_readings)}). Fetch more recent rows "
            f"for this region from SENSOR_READING before calling this."
        )

    bundle = _load_model(region)
    model = bundle["model"]
    feature_cols = bundle["feature_cols"]
    moderate_threshold = bundle["moderate_threshold"]
    high_threshold = bundle["high_threshold"]

    readings = recent_readings.copy()
    readings["date"] = pd.to_datetime(readings["date"])
    readings = add_features_for_region(readings)
    readings = add_seasonal_features(readings)

    latest = readings.iloc[[-1]]
    if latest[feature_cols].isna().any(axis=1).iloc[0]:
        raise ValueError(
            "The most recent row is missing one or more engineered features "
            "after processing -- check that recent_readings has no gaps in "
            "its dates and enough leading history."
        )

    X = latest[feature_cols]
    anomaly_score = float(-model.score_samples(X)[0])
    is_anomaly = bool(model.predict(X)[0] == -1)

    if anomaly_score >= high_threshold:
        severity = "high"
    elif anomaly_score >= moderate_threshold:
        severity = "moderate"
    else:
        severity = "normal"

    return {
        "date": latest["date"].iloc[0].strftime("%Y-%m-%d"),
        "region": region,
        "anomaly_score": anomaly_score,
        "is_anomaly": is_anomaly,
        "severity": severity,
    }
