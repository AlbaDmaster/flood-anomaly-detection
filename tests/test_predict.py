"""
Unit tests for the inference function used by the Flask app. Uses the
already-trained region models on disk (from train_and_evaluate.py) rather
than mocking, since the whole point is to test the real training/serving
path end to end.
"""
from pathlib import Path

import pandas as pd
import pytest

from src.model.predict import predict_anomaly, MIN_HISTORY_DAYS

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def kisumu_history():
    master = pd.read_csv(ROOT / "data" / "processed" / "master_dataset.csv", parse_dates=["date"])
    kisumu = master[master.region == "kisumu"].sort_values("date").reset_index(drop=True)
    return kisumu.tail(15)[["date", "rainfall", "temperature", "humidity", "soil_moisture", "river_discharge"]]


def test_normal_reading_returns_well_formed_result(kisumu_history):
    result = predict_anomaly("kisumu", kisumu_history)
    assert result["severity"] in {"normal", "moderate", "high"}
    assert isinstance(result["anomaly_score"], float)
    assert isinstance(result["is_anomaly"], bool)


def test_insufficient_history_raises_value_error(kisumu_history):
    with pytest.raises(ValueError):
        predict_anomaly("kisumu", kisumu_history.tail(MIN_HISTORY_DAYS - 1))


def test_unknown_region_raises_file_not_found(kisumu_history):
    with pytest.raises(FileNotFoundError):
        predict_anomaly("mombasa", kisumu_history)


def test_extreme_spike_is_flagged_anomalous(kisumu_history):
    spiked = kisumu_history.copy()
    spiked.iloc[-1, spiked.columns.get_loc("rainfall")] = 500.0
    spiked.iloc[-1, spiked.columns.get_loc("river_discharge")] = spiked["river_discharge"].max() * 20 + 1
    result = predict_anomaly("kisumu", spiked)
    assert result["is_anomaly"] is True
    assert result["severity"] in {"moderate", "high"}


def test_result_date_matches_last_input_row(kisumu_history):
    result = predict_anomaly("kisumu", kisumu_history)
    expected_date = kisumu_history["date"].iloc[-1].strftime("%Y-%m-%d")
    assert result["date"] == expected_date
