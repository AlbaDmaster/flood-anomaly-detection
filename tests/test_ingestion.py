"""
Tests for live and replay ingestion. They run against the separate test
database (flood_anomaly_detection_test), never the real one, and empty its
reading tables before every test. Open-Meteo is replaced by fake responses
so the tests are fast and do not need the internet.
"""
from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from app import create_app, ingestion
from app.config import database_url
from app.extensions import db
from app.models import SensorReading
from src.data_pipeline.fetch_raw_data import WEATHER_URL

TEST_DB = "flood_anomaly_detection_test"


@pytest.fixture(scope="module")
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": database_url(TEST_DB)})
    with app.app_context():
        try:
            db.session.execute(db.text("SELECT COUNT(*) FROM REGION"))
        except (OperationalError, ProgrammingError) as exc:
            pytest.skip(f"Test database not ready - run the one-time test database setup: {exc.orig}")
    return app


@pytest.fixture(autouse=True)
def ctx(app):
    with app.app_context():
        assert db.engine.url.database == TEST_DB  # never wipe the real database
        ingestion.reset_readings()
        yield


def readings_for(slug):
    region = ingestion.get_region(slug)
    return db.session.execute(
        db.select(SensorReading).filter_by(region_id=region.region_id).order_by(SensorReading.timestamp)
    ).scalars().all()


def sample(days=3, start="2024-01-01"):
    return pd.DataFrame({
        "date": pd.date_range(start, periods=days, freq="D"),
        "rainfall": [1.0] * days, "river_discharge": [2.0] * days, "humidity": [60.0] * days,
        "temperature": [25.0] * days, "soil_moisture": [0.3] * days,
    })


def test_insert_is_idempotent():
    assert ingestion.insert_readings("nairobi", sample()) == 3
    assert ingestion.insert_readings("nairobi", sample()) == 0
    assert len(readings_for("nairobi")) == 3


def test_incomplete_rows_are_skipped():
    df = sample()
    df.loc[1, "rainfall"] = None
    assert ingestion.insert_readings("kisumu", df) == 2


def test_unknown_region_raises():
    with pytest.raises(ValueError, match="Unknown region"):
        ingestion.insert_readings("mombasa", sample())


def test_replay_loads_history_then_window_with_csv_values():
    total = ingestion.replay(["nairobi"], "2024-04-20", "2024-04-25", history_days=10, echo=lambda *_: None)
    rows = readings_for("nairobi")
    assert total == len(rows) == 16
    assert rows[0].timestamp.date() == date(2024, 4, 10)
    assert rows[-1].timestamp.date() == date(2024, 4, 25)

    master = pd.read_csv(ingestion.MASTER_CSV, parse_dates=["date"])
    expected = master[(master.region == "nairobi") & (master.date == "2024-04-24")].iloc[0]
    stored = next(r for r in rows if r.timestamp.date() == date(2024, 4, 24))
    for col in ingestion.READING_COLUMNS:
        assert getattr(stored, col) == pytest.approx(expected[col], rel=1e-5)


def test_replay_rejects_dates_outside_the_data():
    with pytest.raises(ValueError, match="Replay data covers"):
        ingestion.replay(["nairobi"], "2026-01-01", "2026-01-05", echo=lambda *_: None)


def fake_open_meteo(days=6, fail_when_end_is_today=False):
    calls = []

    def fake_fetch(url, params):
        calls.append(params["end_date"])
        if fail_when_end_is_today and params["end_date"] == (date.today() - timedelta(days=1)).isoformat():
            raise RuntimeError("archive rejected end_date")
        start = date.fromisoformat(params["start_date"])
        end = date.fromisoformat(params["end_date"])
        times = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
        n = len(times)
        if url == WEATHER_URL:
            rain = [float(i + 1) for i in range(n)]
            rain[2] = None              # interior gap -> filled by interpolation
            unpublished = [None] * 1    # newest day not published yet -> skipped
            return {"daily": {
                "time": times,
                "precipitation_sum": rain[:-1] + unpublished,
                "temperature_2m_mean": [25.0] * (n - 1) + unpublished,
                "relative_humidity_2m_mean": [70.0] * (n - 1) + unpublished,
                "soil_moisture_0_to_7cm_mean": [0.3] * (n - 1) + unpublished,
            }}
        return {"daily": {"time": times, "river_discharge": [5.0] * n}}

    return fake_fetch, calls


def test_live_ingest_fills_inner_gaps_and_skips_unpublished_days(monkeypatch):
    fake, _ = fake_open_meteo()
    monkeypatch.setattr(ingestion, "fetch", fake)
    monkeypatch.setattr(ingestion.time, "sleep", lambda *_: None)
    results = ingestion.ingest_live(["tana_river_hola"], days=6, echo=lambda *_: None)
    rows = readings_for("tana_river_hola")
    assert results == {"tana_river_hola": 5} and len(rows) == 5
    assert rows[2].rainfall == pytest.approx(3.0)   # (2 + 4) / 2
    assert rows[-1].timestamp.date() == date.today() - timedelta(days=2)


def test_live_ingest_falls_back_when_archive_rejects_today(monkeypatch):
    fake, calls = fake_open_meteo(fail_when_end_is_today=True)
    monkeypatch.setattr(ingestion, "fetch", fake)
    monkeypatch.setattr(ingestion.time, "sleep", lambda *_: None)
    ingestion.ingest_live(["kisumu"], days=10, echo=lambda *_: None)
    assert calls[-1] == (date.today() - timedelta(days=ingestion.ARCHIVE_FALLBACK_DAYS)).isoformat()
    assert len(readings_for("kisumu")) > 0


def test_cli_replay_and_reset(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["replay", "--start", "2024-04-20", "--end", "2024-04-22",
                                 "--region", "nairobi", "--history-days", "5"])
    assert result.exit_code == 0, result.output
    assert "Done: 8 new readings." in result.output

    result = runner.invoke(args=["reset-readings", "--yes"])
    assert result.exit_code == 0, result.output
    assert len(readings_for("nairobi")) == 0
