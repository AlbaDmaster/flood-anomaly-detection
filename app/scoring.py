"""
Scores every reading that has no ANOMALY row yet, using the trained
Isolation Forest for its region (src/model/predict.py), and saves the result:

  ANOMALY - one row per scored reading: anomaly score and severity
            (normal, moderate or high, from the per-region thresholds).
  ALERT   - one row per moderate or high anomaly, status 'pending', shown on
            the dashboard. HIGH alerts are then emailed to responders,
            authorities and administrators (app/notifications.py), which
            sets their status to 'sent' or 'failed'.

A reading is scored only when the 8 consecutive days ending on it are all in
SENSOR_READING (the lag-7 and 7-day rolling features need them). Readings
without that history stay unscored and are picked up automatically once the
missing days arrive. Re-running never scores a reading twice.
"""
from datetime import timedelta

import pandas as pd
from flask import current_app

from app.extensions import db
from app.ingestion import READING_COLUMNS, all_region_slugs, get_region
from app.models import Alert, Anomaly, SensorReading
from app.notifications import send_alert_emails
from src.model.predict import MIN_HISTORY_DAYS, predict_anomaly

ALERT_SEVERITIES = ("moderate", "high")


def alert_message(region, reading, result) -> str:
    return (
        f"{result['severity'].upper()} flood anomaly in {region.name} on {result['date']}: "
        f"score {result['anomaly_score']:.3f}, rainfall {reading.rainfall:.1f} mm, "
        f"river discharge {reading.river_discharge:.1f} m3/s"
    )


def _region_frame(region_id: int, since) -> pd.DataFrame:
    rows = db.session.execute(
        db.select(SensorReading)
        .where(SensorReading.region_id == region_id, SensorReading.timestamp >= since)
        .order_by(SensorReading.timestamp)
    ).scalars().all()
    frame = pd.DataFrame(
        [{"date": r.timestamp, **{col: getattr(r, col) for col in READING_COLUMNS}} for r in rows]
    )
    return frame.set_index("date")


def score_pending(slugs: list[str] | None = None, notify: bool = True) -> list[dict]:
    """
    Score all unscored readings for the given regions (default: all) and email
    any new HIGH alerts (unless notify=False). Returns one dict per new score;
    alert results carry "email": "sent", "failed" or None (not emailed).
    """
    results = []
    new_alerts = []
    for slug in slugs or all_region_slugs():
        region = get_region(slug)
        pending = db.session.execute(
            db.select(SensorReading)
            .outerjoin(Anomaly, Anomaly.reading_id == SensorReading.reading_id)
            .where(SensorReading.region_id == region.region_id, Anomaly.anomaly_id.is_(None))
            .order_by(SensorReading.timestamp)
        ).scalars().all()
        if not pending:
            continue

        history_span = timedelta(days=MIN_HISTORY_DAYS - 1)
        frame = _region_frame(region.region_id, pending[0].timestamp - history_span)

        for reading in pending:
            window = frame.loc[reading.timestamp - history_span: reading.timestamp]
            if len(window) != MIN_HISTORY_DAYS:  # a missing day inside the 8-day window
                continue
            result = predict_anomaly(slug, window.reset_index())
            anomaly = Anomaly(
                reading_id=reading.reading_id,
                anomaly_score=result["anomaly_score"],
                severity_level=result["severity"],
            )
            db.session.add(anomaly)
            raised = result["severity"] in ALERT_SEVERITIES
            alert = None
            if raised:
                alert = Alert(anomaly=anomaly, message=alert_message(region, reading, result))
                db.session.add(alert)
                new_alerts.append(alert)
            results.append({**result, "alert": raised, "_alert": alert})
        db.session.commit()

    if notify and new_alerts:
        try:
            send_alert_emails(new_alerts)
        except Exception:  # email must never stop scoring; the alerts stay pending for `flask --app run notify`
            db.session.rollback()
            current_app.logger.exception("Sending alert emails failed")
    for r in results:
        alert = r.pop("_alert")
        r["email"] = alert.status if alert is not None and alert.status in ("sent", "failed") else None
    return results
