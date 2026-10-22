"""
Tests for scoring readings and raising alerts. Like the ingestion tests they
use the separate test database and empty its reading tables before each test.
Scores are checked against predict_anomaly() run directly on the CSV, so the
database path must give exactly the same answer as the model on its own.
"""

import pandas as pd
import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from app import create_app, ingestion, scoring
from app.config import database_url
from app.extensions import db
from app.models import Alert, Anomaly, SensorReading
from src.model.predict import MIN_HISTORY_DAYS, predict_anomaly

TEST_DB = "flood_anomaly_detection_test"
QUIET = {"echo": lambda *_: None}


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


def count(model):
    return db.session.scalar(db.select(db.func.count()).select_from(model))


def anomaly_on(slug, day):
    region = ingestion.get_region(slug)
    return db.session.execute(
        db.select(Anomaly).join(SensorReading)
        .where(SensorReading.region_id == region.region_id, SensorReading.timestamp == pd.Timestamp(day))
    ).scalar_one_or_none()


def test_first_seven_days_lack_history_and_stay_unscored():
    ingestion.replay(["nairobi"], "2024-04-20", "2024-04-29", history_days=0, **QUIET)
    results = scoring.score_pending(["nairobi"])
    assert len(results) == 10 - (MIN_HISTORY_DAYS - 1)
    assert anomaly_on("nairobi", "2024-04-26") is None
    assert anomaly_on("nairobi", "2024-04-27") is not None


def test_scores_match_the_model_run_directly_on_the_csv():
    ingestion.replay(["kisumu"], "2020-05-01", "2020-05-12", history_days=0, **QUIET)
    scoring.score_pending(["kisumu"])
    master = pd.read_csv(ingestion.MASTER_CSV, parse_dates=["date"])
    window = master[(master.region == "kisumu") & (master.date.between("2020-05-04", "2020-05-11"))]
    expected = predict_anomaly("kisumu", window[["date", *ingestion.READING_COLUMNS]])
    stored = anomaly_on("kisumu", "2020-05-11")
    assert stored.anomaly_score == pytest.approx(expected["anomaly_score"], rel=1e-5)
    assert stored.severity_level == expected["severity"]


def test_scoring_twice_never_duplicates():
    ingestion.replay(["nairobi"], "2024-04-20", "2024-04-30", history_days=0, **QUIET)
    first = scoring.score_pending()
    assert first and scoring.score_pending() == []
    assert count(Anomaly) == len(first)


def test_missing_day_blocks_scoring_until_it_arrives():
    master = pd.read_csv(ingestion.MASTER_CSV, parse_dates=["date"])
    days = master[(master.region == "nairobi") & (master.date.between("2024-04-01", "2024-04-10"))]
    gap_day = pd.Timestamp("2024-04-05")
    ingestion.insert_readings("nairobi", days[days.date != gap_day])
    assert scoring.score_pending(["nairobi"]) == []          # every 8-day window crosses the gap
    ingestion.insert_readings("nairobi", days[days.date == gap_day])
    assert len(scoring.score_pending(["nairobi"])) == 3       # 8th, 9th and 10th of April


def test_known_flood_peak_raises_a_high_alert_and_normal_days_do_not():
    ingestion.replay(["tana_river_hola"], "2018-05-01", "2018-05-08", history_days=10,
                     after_day=lambda: scoring.score_pending(["tana_river_hola"]), **QUIET)
    peak = anomaly_on("tana_river_hola", "2018-05-07")
    assert peak.severity_level == "high"
    alert = db.session.execute(db.select(Alert).filter_by(anomaly_id=peak.anomaly_id)).scalar_one()
    assert alert.status == "pending" and alert.channel == "dashboard"
    assert "Tana River (Hola)" in alert.message and "2018-05-07" in alert.message
    flagged = db.session.scalar(
        db.select(db.func.count()).select_from(Anomaly).where(Anomaly.severity_level != "normal")
    )
    assert count(Alert) == flagged


def test_cli_replay_scores_and_reports_alerts(app):
    result = app.test_cli_runner().invoke(args=[
        "replay", "--start", "2018-05-05", "--end", "2018-05-08",
        "--region", "tana_river_hola", "--history-days", "10",
    ])
    assert result.exit_code == 0, result.output
    assert "ALERT HIGH" in result.output and "2018-05-07" in result.output
    assert "readings scored" in result.output


def test_cli_no_score_only_loads(app):
    result = app.test_cli_runner().invoke(args=[
        "replay", "--start", "2024-04-20", "--end", "2024-04-30", "--region", "nairobi",
        "--history-days", "0", "--no-score",
    ])
    assert result.exit_code == 0, result.output
    assert count(Anomaly) == 0 and count(SensorReading) == 11
