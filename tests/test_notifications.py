"""
Tests for emailing HIGH alerts. Messages go to an in-memory outbox (the
'memory' backend used whenever TESTING is on), or to a fake SMTP server for
the tests that check the real SMTP path, so no email ever leaves the machine.
The May 2018 Tana River replay raises a known HIGH alert on 2018-05-07.
"""
import smtplib

import pandas as pd
import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from app import create_app, ingestion, notifications, scoring
from app.auth import create_user
from app.config import database_url
from app.extensions import db
from app.models import Alert, Anomaly, Feedback, SensorReading, User

TEST_DB = "flood_anomaly_detection_test"
PASSWORD = "correct-horse-1"
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
        db.session.execute(db.delete(Feedback))
        db.session.execute(db.delete(User))
        db.session.commit()
        notifications.outbox().clear()
        yield


def add_users(*roles, inactive=()):
    for role in roles:
        create_user(role.title(), f"{role}@example.com", PASSWORD, role=role)
    for role in inactive:
        user = create_user(f"Former {role}", f"former-{role}@example.com", PASSWORD, role=role)
        user.is_active = False
    db.session.commit()


def replay_tana_2018(notify=True):
    ingestion.replay(["tana_river_hola"], "2018-04-21", "2018-05-08", history_days=0, **QUIET)
    return scoring.score_pending(["tana_river_hola"], notify=notify)


def alerts_by(severity):
    return db.session.execute(
        db.select(Alert).join(Anomaly).where(Anomaly.severity_level == severity)
    ).scalars().all()


def test_high_alerts_are_emailed_to_active_staff_only():
    add_users("responder", "authority", "administrator", "public", inactive=("responder",))
    results = replay_tana_2018()

    high = alerts_by("high")
    assert high and len(notifications.outbox()) == len(high)
    subjects = [m["Subject"] for m in notifications.outbox()]
    assert "HIGH flood alert: Tana River (Hola) on 2018-05-07" in subjects
    msg = notifications.outbox()[0]
    assert sorted(msg["Bcc"].split(", ")) == ["administrator@example.com", "authority@example.com", "responder@example.com"]
    body = msg.get_content()
    assert "Anomaly score" in body and "River discharge" in body and "/alerts" in body

    for alert in high:
        assert (alert.channel, alert.status) == ("email", "sent") and alert.sent_at is not None
    for alert in alerts_by("moderate"):
        assert (alert.channel, alert.status, alert.sent_at) == ("dashboard", "pending", None)
    assert {r["email"] for r in results if r["severity"] == "high"} == {"sent"}
    assert {r["email"] for r in results if r["severity"] != "high"} == {None}


def test_an_alert_is_emailed_only_once(app):
    add_users("responder")
    replay_tana_2018()
    sent = len(notifications.outbox())
    assert sent > 0
    assert scoring.score_pending(["tana_river_hola"]) == []  # nothing new to score
    result = app.test_cli_runner().invoke(args=["notify"])
    assert "No unsent high alerts" in result.output
    assert len(notifications.outbox()) == sent


def test_acknowledged_alerts_are_not_emailed(app):
    add_users("responder")
    replay_tana_2018(notify=False)
    for alert in alerts_by("high"):
        alert.status = "acknowledged"
    db.session.commit()
    app.test_cli_runner().invoke(args=["notify"])
    assert notifications.outbox() == []


def test_without_recipients_alerts_stay_pending(app):
    add_users("public")
    results = replay_tana_2018()
    assert notifications.outbox() == []
    assert {a.status for a in alerts_by("high")} == {"pending"}
    assert {r["email"] for r in results} == {None}


class FakeSMTP:
    """Stands in for smtplib.SMTP and records what would have been sent."""
    sent, logins, closed, fail_connect, fail_login = [], [], [], False, False

    def __init__(self, host, port, timeout=None):
        if FakeSMTP.fail_connect:
            raise ConnectionRefusedError(111, "Connection refused")
        self.host, self.port = host, port

    def starttls(self, context=None):
        self.tls = True

    def login(self, user, password):
        if FakeSMTP.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
        FakeSMTP.logins.append((user, password))

    def send_message(self, msg):
        assert self.tls
        FakeSMTP.sent.append(msg)

    def quit(self):
        pass

    def close(self):
        FakeSMTP.closed.append(self)


@pytest.fixture
def fake_smtp(app, monkeypatch):
    FakeSMTP.sent, FakeSMTP.logins, FakeSMTP.closed = [], [], []
    FakeSMTP.fail_connect = FakeSMTP.fail_login = False
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    for key, value in {"MAIL_BACKEND": None, "MAIL_SERVER": "smtp.example.com", "MAIL_PORT": "587",
                       "MAIL_USERNAME": "ews@example.com", "MAIL_PASSWORD": "app-password"}.items():
        monkeypatch.setitem(app.config, key, value)
    return FakeSMTP


def test_smtp_settings_from_env_are_used(fake_smtp):
    add_users("responder", "authority")
    replay_tana_2018()
    high = alerts_by("high")
    assert len(fake_smtp.sent) == len(high) and fake_smtp.logins[0] == ("ews@example.com", "app-password")
    assert fake_smtp.sent[0]["From"] == "ews@example.com"
    assert {a.status for a in high} == {"sent"}


def test_failed_emails_are_marked_and_can_be_retried(app, fake_smtp):
    add_users("responder")
    fake_smtp.fail_connect = True
    results = replay_tana_2018()
    high = alerts_by("high")
    assert {(a.channel, a.status, a.sent_at) for a in high} == {("email", "failed", None)}
    assert {r["email"] for r in results if r["severity"] == "high"} == {"failed"}

    fake_smtp.fail_connect = False
    result = app.test_cli_runner().invoke(args=["notify"])
    assert f"{len(high)} sent, 0 failed" in result.output
    db.session.expire_all()
    assert {a.status for a in alerts_by("high")} == {"sent"}
    assert len(fake_smtp.sent) == len(high)


def test_wrong_password_marks_alerts_failed_and_closes_the_connection(fake_smtp):
    add_users("responder")
    fake_smtp.fail_login = True
    replay_tana_2018()
    assert {a.status for a in alerts_by("high")} == {"failed"}
    assert len(fake_smtp.closed) == 1 and fake_smtp.sent == []


def test_a_bad_mail_setting_does_not_stop_scoring(app, fake_smtp, monkeypatch):
    add_users("responder")
    monkeypatch.setitem(app.config, "MAIL_PORT", "five-eight-seven")
    replay_tana_2018()
    assert {a.status for a in alerts_by("high")} == {"failed"}


def test_scoring_still_works_when_email_fails(fake_smtp):
    add_users("responder")
    fake_smtp.fail_connect = True
    replay_tana_2018()
    assert db.session.scalar(db.select(db.func.count()).select_from(Anomaly)) > 0
    assert alerts_by("high")


def test_console_backend_prints_the_test_email(app, monkeypatch):
    monkeypatch.setitem(app.config, "MAIL_BACKEND", "console")
    result = app.test_cli_runner().invoke(args=["send-test-email", "--to", "someone@example.com"])
    assert result.exit_code == 0
    assert "Subject: Flood EWS test email" in result.output and "printed above" in result.output


def test_test_email_reports_smtp_problems(app, fake_smtp):
    fake_smtp.fail_connect = True
    result = app.test_cli_runner().invoke(args=["send-test-email", "--to", "someone@example.com"])
    assert result.exit_code != 0 and "Connection refused" in result.output


def test_mail_settings_are_read_from_env(monkeypatch, tmp_path):
    from app import config
    env = tmp_path / ".env"
    env.write_text("MAIL_SERVER=smtp.gmail.com\nMAIL_PORT=587\nMAIL_USERNAME=me@gmail.com\nMAIL_FROM=\n")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    for key in config.MAIL_KEYS:
        monkeypatch.delenv(key, raising=False)
    settings = config.mail_settings()
    assert settings == {"MAIL_SERVER": "smtp.gmail.com", "MAIL_PORT": "587", "MAIL_USERNAME": "me@gmail.com"}


def test_replay_status_counts_emails(app):
    from app.replay import runner
    add_users("responder")
    runner.start(app, ["tana_river_hola"], pd.Timestamp("2018-05-01"), pd.Timestamp("2018-05-08"),
                 delay=0, history_days=10, reset=False)
    assert runner.wait(60)
    status = runner.status()
    assert status["state"] == "finished"
    db.session.rollback()  # end this test's transaction so it sees what the replay thread wrote
    assert status["emails_sent"] == len(alerts_by("high")) == len(notifications.outbox()) > 0
    assert status["emails_failed"] == 0
