"""
Tests for the replay demo controls. A real background replay runs against the
test database: with no delay it must load and score every day (raising the
known HIGH alert on 2018-05-07 for Tana River), and with a delay it must
pause, resume and stop on command. Only administrators may control it.
"""
import time

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from app import create_app, ingestion
from app.auth import create_user
from app.config import database_url
from app.extensions import db
from app.models import Feedback, SensorReading, User
from app.replay import runner

TEST_DB = "flood_anomaly_detection_test"
PASSWORD = "correct-horse-1"
TANA_2018 = {"start": "2018-05-01", "end": "2018-05-08", "regions": ["tana_river_hola"], "history_days": 10}


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
def clean(app):
    with app.app_context():
        assert db.engine.url.database == TEST_DB  # never wipe the real database
        ingestion.reset_readings()
        db.session.execute(db.delete(Feedback))
        db.session.execute(db.delete(User))
        db.session.commit()
        for role in ("public", "administrator"):
            create_user(role.title(), f"{role}@example.com", PASSWORD, role=role)
    yield
    if runner.is_active():
        runner.stop()
    assert runner.wait(30)


def signed_in(app, role):
    client = app.test_client()
    assert client.post("/api/auth/login", json={"email": f"{role}@example.com", "password": PASSWORD}).status_code == 200
    return client


def wait_for(client, condition, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get("/api/replay").get_json()["replay"]
        if condition(status):
            return status
        time.sleep(0.1)
    raise AssertionError(f"replay did not reach the expected state: {status}")


def test_replay_runs_to_the_end_and_raises_the_2018_high_alert(app):
    admin = signed_in(app, "administrator")
    response = admin.post("/api/replay/start", json={**TANA_2018, "delay": 0})
    assert response.status_code == 202
    status = wait_for(admin, lambda s: s["state"] == "finished")
    assert status["day"] == status["total_days"] == 8 and status["current_date"] == "2018-05-08"
    assert status["new_readings"] == 18 and status["alerts"] > 0
    assert any(a["date"] == "2018-05-07" and a["severity"] == "high" for a in status["recent_alerts"])
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(SensorReading)) == 18


def test_pause_resume_and_stop(app):
    admin = signed_in(app, "administrator")
    assert admin.post("/api/replay/start", json={**TANA_2018, "end": "2018-05-31", "delay": 0.3}).status_code == 202
    wait_for(admin, lambda s: s["state"] == "running" and s["day"] >= 1)
    assert admin.post("/api/replay/start", json={**TANA_2018, "delay": 0}).status_code == 409  # one at a time

    paused = admin.post("/api/replay/pause").get_json()["replay"]
    assert paused["state"] == "paused"
    time.sleep(1.0)
    day_while_paused = admin.get("/api/replay").get_json()["replay"]["day"]
    time.sleep(1.0)
    assert admin.get("/api/replay").get_json()["replay"]["day"] == day_while_paused
    assert admin.post("/api/replay/pause").status_code == 409

    assert admin.post("/api/replay/resume").get_json()["replay"]["state"] == "running"
    wait_for(admin, lambda s: s["day"] > day_while_paused)
    assert admin.post("/api/replay/stop").status_code == 200
    status = wait_for(admin, lambda s: s["state"] == "stopped")
    assert status["day"] < status["total_days"]
    assert admin.post("/api/replay/stop").status_code == 409


def test_reset_clears_existing_readings_first(app):
    with app.app_context():
        ingestion.replay(["nairobi"], "2024-04-01", "2024-04-05", history_days=0, echo=lambda *_: None)
    admin = signed_in(app, "administrator")
    admin.post("/api/replay/start", json={**TANA_2018, "delay": 0, "reset": True})
    wait_for(admin, lambda s: s["state"] == "finished")
    with app.app_context():
        nairobi = ingestion.get_region("nairobi")
        assert db.session.scalar(db.select(db.func.count()).select_from(SensorReading)
                                 .filter_by(region_id=nairobi.region_id)) == 0


@pytest.mark.parametrize("body, message", [
    ({"start": "yesterday", "end": "2018-05-08"}, "start must be a date"),
    ({"start": "2018-05-08", "end": "2018-05-01"}, "end must be on or after start"),
    ({"start": "2026-01-01", "end": "2026-01-05"}, "Replay data covers"),
    ({**TANA_2018, "delay": 99}, "delay must be between"),
    ({**TANA_2018, "regions": ["mombasa"]}, "Unknown region"),
    ({**TANA_2018, "history_days": -1}, "history_days"),
    ({**TANA_2018, "reset": "yes"}, "reset must be"),
    ({**TANA_2018, "regions": []}, "non-empty list"),
    ({**TANA_2018, "delay": "2"}, "delay must be between"),
])
def test_start_validates_input(app, body, message):
    response = signed_in(app, "administrator").post("/api/replay/start", json=body)
    assert response.status_code == 400 and message in response.get_json()["error"]


def test_only_administrators_control_the_replay(app):
    anonymous = app.test_client()
    assert anonymous.get("/api/replay").status_code == 401
    public = signed_in(app, "public")
    assert public.get("/api/replay").get_json()["replay"]["state"] in ("idle", "finished", "stopped")
    for action in ("start", "pause", "resume", "stop", "speed"):
        assert public.post(f"/api/replay/{action}", json=TANA_2018).status_code == 403
    assert public.get("/replay").status_code == 403


def test_speed_can_change_while_running(app):
    admin = signed_in(app, "administrator")
    admin.post("/api/replay/start", json={**TANA_2018, "end": "2018-05-31", "delay": 2})
    wait_for(admin, lambda s: s["state"] == "running")
    assert admin.post("/api/replay/speed", json={"delay": 99}).status_code == 400
    assert admin.post("/api/replay/speed", json={"delay": 0}).get_json()["replay"]["delay"] == 0
    status = wait_for(admin, lambda s: s["state"] == "finished", timeout=30)   # finishes quickly once delay is 0
    assert status["day"] == status["total_days"]
    assert admin.post("/api/replay/speed", json={"delay": 1}).status_code == 409


def test_replaying_days_already_loaded_explains_why_nothing_changed(app):
    admin = signed_in(app, "administrator")
    admin.post("/api/replay/start", json={**TANA_2018, "delay": 0})
    wait_for(admin, lambda s: s["state"] == "finished")
    admin.post("/api/replay/start", json={**TANA_2018, "delay": 0})
    status = wait_for(admin, lambda s: s["state"] == "finished" and s["new_readings"] == 0)
    assert "already loaded" in status["message"]
