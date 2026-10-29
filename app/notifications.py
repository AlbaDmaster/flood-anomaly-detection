"""
Email notifications for high-severity flood alerts.

When scoring raises a HIGH alert, every active responder, authority and
administrator is emailed once (one message, recipients in Bcc so they do not
see each other's addresses). The alert row then records the outcome:

  channel 'email', status 'sent',   sent_at = when it went out
  channel 'email', status 'failed', sent_at stays empty (retry with `flask --app run notify`)

Moderate alerts are shown on the dashboard only. An alert that has already
been sent or acknowledged is never emailed again.

Mail settings come from .env (see .env.example). Without MAIL_SERVER the
message is printed in the terminal instead of being sent, so the system and
the demo still work with no internet or mail account; tests keep messages in
memory. MAIL_BACKEND=smtp|console|memory overrides that choice.
"""
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import click
from flask import current_app
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Alert, Anomaly, SensorReading, User

NOTIFY_ROLES = ("responder", "authority", "administrator")
NOTIFY_SEVERITY = "high"
RETRY_STATUSES = ("pending", "failed")
SMTP_TIMEOUT_SECONDS = 15


def recipients() -> list[str]:
    users = db.session.execute(
        db.select(User).where(User.role.in_(NOTIFY_ROLES), User.is_active.is_(True)).order_by(User.user_id)
    ).scalars().all()
    return [u.email for u in users]


def backend() -> str:
    return current_app.config.get("MAIL_BACKEND") or ("smtp" if current_app.config.get("MAIL_SERVER") else "console")


def outbox() -> list[EmailMessage]:
    """Messages kept by the 'memory' backend (used by the tests)."""
    return current_app.extensions.setdefault("mail_outbox", [])


def sender() -> str:
    return current_app.config.get("MAIL_FROM") or current_app.config.get("MAIL_USERNAME") or "flood-ews@localhost"


def alert_email(alert: Alert, to: list[str]) -> EmailMessage:
    reading = alert.anomaly.reading
    region = reading.region
    day = reading.timestamp.date().isoformat()
    link = f"{current_app.config.get('APP_URL', 'http://127.0.0.1:5000').rstrip('/')}/alerts"
    msg = EmailMessage()
    msg["Subject"] = f"HIGH flood alert: {region.name} on {day}"
    msg["From"] = sender()
    msg["To"] = sender()
    msg["Bcc"] = ", ".join(to)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="flood-ews.local")
    msg.set_content(
        f"The flood anomaly detection system has raised a HIGH severity alert.\n\n"
        f"Region:           {region.name}, {region.county} County\n"
        f"Date of reading:  {day}\n"
        f"Anomaly score:    {alert.anomaly.anomaly_score:.3f}\n\n"
        f"Readings for that day:\n"
        f"  Rainfall          {reading.rainfall:.1f} mm\n"
        f"  River discharge   {reading.river_discharge:.1f} m3/s\n"
        f"  Soil moisture     {reading.soil_moisture:.3f} m3/m3\n"
        f"  Humidity          {reading.humidity:.0f} %\n"
        f"  Temperature       {reading.temperature:.1f} C\n\n"
        f"Please review and acknowledge the alert on the dashboard: {link}\n\n"
        f"This message was sent automatically by the Flood Anomaly Detection early warning system.\n"
        f"You receive it because your account is an active responder, authority or administrator.\n"
    )
    return msg


class Mailer:
    """Delivers messages through the configured backend, reusing one SMTP connection per batch."""

    def __init__(self):
        self.kind = backend()
        self.smtp = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        if self.smtp is not None:
            try:
                self.smtp.quit()
            except smtplib.SMTPException:
                pass
            except OSError:
                pass
            self.smtp = None

    def _connect(self):
        cfg = current_app.config
        host, port = cfg["MAIL_SERVER"], int(cfg.get("MAIL_PORT") or 587)
        context = ssl.create_default_context()
        if port == 465:
            smtp = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT_SECONDS, context=context)
        else:
            smtp = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS)
        try:
            if port != 465:
                smtp.starttls(context=context)
            if cfg.get("MAIL_USERNAME"):
                smtp.login(cfg["MAIL_USERNAME"], cfg.get("MAIL_PASSWORD") or "")
        except BaseException:
            smtp.close()  # do not leave the connection open when TLS or sign-in fails
            raise
        return smtp

    def send(self, msg: EmailMessage):
        if self.kind == "memory":
            outbox().append(msg)
        elif self.kind == "console":
            click.echo("\n" + "=" * 72)
            click.echo("EMAIL (printed because MAIL_SERVER is not set in .env)")
            for key in ("Subject", "From", "To", "Bcc", "Date"):
                if msg[key]:
                    click.echo(f"{key}: {msg[key]}")
            click.echo("")
            click.echo(msg.get_content().rstrip("\n"))
            click.echo("=" * 72)
        elif self.kind == "smtp":
            if self.smtp is None:
                self.smtp = self._connect()
            self.smtp.send_message(msg)  # Bcc addresses are used for delivery and stripped from the headers
        else:
            raise RuntimeError(f"Unknown MAIL_BACKEND '{self.kind}' - use smtp, console or memory")


def send_alert_emails(alerts: list[Alert]) -> dict:
    """Email each HIGH alert once and record the outcome. Returns {"sent": n, "failed": n, "error": str|None}."""
    outcome = {"sent": 0, "failed": 0, "error": None}
    alerts = [a for a in alerts if a.anomaly.severity_level == NOTIFY_SEVERITY and a.status in RETRY_STATUSES]
    if not alerts:
        return outcome
    to = recipients()
    if not to:
        outcome["error"] = "no active responder, authority or administrator accounts to email"
        current_app.logger.warning("High alerts not emailed: %s", outcome["error"])
        return outcome

    with Mailer() as mailer:
        for alert in alerts:
            alert.channel = "email"
            try:
                mailer.send(alert_email(alert, to))
            except Exception as exc:  # any mail problem marks the alert failed; scoring carries on
                alert.status = "failed"
                outcome["failed"] += 1
                outcome["error"] = f"{type(exc).__name__}: {exc}"
                current_app.logger.error("Email for alert %s failed: %s", alert.alert_id, exc)
                if mailer.smtp is None:  # could not connect or sign in: the rest would fail the same way
                    for rest in alerts[alerts.index(alert) + 1:]:
                        rest.channel, rest.status = "email", "failed"
                        outcome["failed"] += 1
                    break
                mailer.__exit__()  # drop the broken connection and reconnect for the next one
            else:
                alert.status = "sent"
                alert.sent_at = datetime.now()
                outcome["sent"] += 1
    db.session.commit()
    return outcome


def unsent_high_alerts() -> list[Alert]:
    return db.session.execute(
        db.select(Alert).join(Anomaly).join(SensorReading)
        .where(Anomaly.severity_level == NOTIFY_SEVERITY, Alert.status.in_(RETRY_STATUSES))
        .options(joinedload(Alert.anomaly).joinedload(Anomaly.reading).joinedload(SensorReading.region))
        .order_by(SensorReading.timestamp, Alert.alert_id)
    ).scalars().all()


def send_test_email(to: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = "Flood EWS test email"
    msg["From"] = sender()
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    msg.set_content("If you can read this, the Flood Anomaly Detection system can send alert emails.\n")
    with Mailer() as mailer:
        mailer.send(msg)
