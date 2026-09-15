"""
Unit tests for the temporal feature engineering function. Verifies the lag,
rolling-mean, rolling-std, and rate-of-change calculations against values
computed by hand, so a future change to build_features.py that silently
breaks the math gets caught immediately rather than discovered after
retraining.
"""
import pandas as pd

from src.features.build_features import add_features_for_region, add_seasonal_features, RAW_VARS


def make_sample_df():
    dates = pd.date_range("2024-01-01", periods=10, freq="D")
    return pd.DataFrame({
        "date": dates,
        "rainfall": [0, 2, 4, 6, 8, 10, 12, 14, 16, 18],
        "temperature": [20] * 10,
        "humidity": [50] * 10,
        "soil_moisture": [0.2] * 10,
        "river_discharge": [5] * 10,
    })


def test_lag_1_shifts_correctly():
    df = add_features_for_region(make_sample_df())
    assert df["rainfall_lag1"].iloc[5] == df["rainfall"].iloc[4]
    assert pd.isna(df["rainfall_lag1"].iloc[0])


def test_lag_7_shifts_correctly():
    df = add_features_for_region(make_sample_df())
    assert df["rainfall_lag7"].iloc[8] == df["rainfall"].iloc[1]
    assert df["rainfall_lag7"].iloc[:7].isna().all()


def test_rolling_mean_matches_hand_calculation():
    df = add_features_for_region(make_sample_df())
    expected = (4 + 6 + 8) / 3
    assert abs(df["rainfall_roll_mean3"].iloc[4] - expected) < 1e-9


def test_rolling_std_matches_hand_calculation():
    df = add_features_for_region(make_sample_df())
    window = df["rainfall"].iloc[0:7]
    expected = window.std()
    assert abs(df["rainfall_roll_std7"].iloc[6] - expected) < 1e-9


def test_rate_of_change_is_simple_difference():
    df = add_features_for_region(make_sample_df())
    expected = df["rainfall"].iloc[3] - df["rainfall"].iloc[2]
    assert abs(df["rainfall_rate1"].iloc[3] - expected) < 1e-9


def test_seasonal_encoding_bounded():
    df = add_features_for_region(make_sample_df())
    df = add_seasonal_features(df)
    assert df["season_sin"].between(-1, 1).all()
    assert df["season_cos"].between(-1, 1).all()


def test_all_raw_vars_get_full_feature_set():
    df = add_features_for_region(make_sample_df())
    for var in RAW_VARS:
        for suffix in ["lag1", "lag7", "roll_mean3", "roll_mean7", "roll_std7", "rate1"]:
            assert f"{var}_{suffix}" in df.columns, f"missing {var}_{suffix}"
