"""
Loads daily readings into SENSOR_READING from two sources:

  live   - pulls recent days from the same Open-Meteo endpoints and variables
           the models were trained on (archive API for weather, flood API for
           river discharge) and cleans them the same way as the training data.
  replay - feeds historical days from data/processed/master_dataset.csv into
           the database one day at a time, as if they were arriving live.
           Used to demonstrate the April-May 2024 floods.

Every insert uses INSERT IGNORE on the (region_id, timestamp) unique key, so
re-running either mode never creates duplicate readings. Rows with a missing
value are skipped, because the model cannot score an incomplete reading.
"""
import time
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func, insert

from app.extensions import db
from app.models import Alert, Anomaly, Feedback, Region, SensorReading
from src.data_pipeline.clean_and_consolidate import COLUMN_RENAME, PROCESSED_DIR
from src.data_pipeline.fetch_raw_data import (
    FLOOD_DAILY_VARS,
    FLOOD_URL,
    WEATHER_DAILY_VARS,
    WEATHER_URL,
    fetch,
)

READING_COLUMNS = ["rainfall", "river_discharge", "humidity", "temperature", "soil_moisture"]
MASTER_CSV = PROCESSED_DIR / "master_dataset.csv"
ARCHIVE_FALLBACK_DAYS = 7  # ERA5 reanalysis runs a few days behind real time


def all_region_slugs() -> list[str]:
    return list(db.session.execute(db.select(Region.slug).order_by(Region.region_id)).scalars())


def get_region(slug: str) -> Region:
    region = db.session.execute(db.select(Region).filter_by(slug=slug)).scalar_one_or_none()
    if region is None:
        raise ValueError(f"Unknown region '{slug}'. Known regions: {', '.join(all_region_slugs())}")
    return region


def count_readings(region_id: int) -> int:
    return db.session.scalar(
        db.select(func.count()).select_from(SensorReading).filter_by(region_id=region_id)
    )


def insert_readings(slug: str, readings: pd.DataFrame) -> int:
    """Insert daily readings (columns: date + READING_COLUMNS). Returns how many were new."""
    region = get_region(slug)
    complete = readings.dropna(subset=READING_COLUMNS)
    if complete.empty:
        return 0
    rows = [
        {
            "region_id": region.region_id,
            "timestamp": pd.Timestamp(row.date).to_pydatetime(),
            **{col: float(getattr(row, col)) for col in READING_COLUMNS},
        }
        for row in complete.itertuples(index=False)
    ]
    before = count_readings(region.region_id)
    db.session.execute(insert(SensorReading).prefix_with("IGNORE"), rows)
    db.session.commit()
    return count_readings(region.region_id) - before


def _daily_frame(payload: dict) -> pd.DataFrame:
    df = pd.DataFrame(payload["daily"]).rename(columns={"time": "date"})
    df["date"] = pd.to_datetime(df["date"])
    return df


def clean_live(df: pd.DataFrame) -> pd.DataFrame:
    """
    Same cleaning rules as clean_and_consolidate.py: numeric coercion, clipping
    impossible values, and filling gaps of up to 3 days. The one difference is
    limit_area="inside": only gaps BETWEEN real values are filled, so the days
    at the end that Open-Meteo has not published yet stay empty (and are
    skipped) instead of being invented. Today is never pulled because its
    daily totals are still incomplete.
    """
    df = df.sort_values("date").reset_index(drop=True)
    for col in READING_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["rainfall"] = df["rainfall"].clip(lower=0)
    df["river_discharge"] = df["river_discharge"].clip(lower=0)
    df["humidity"] = df["humidity"].clip(lower=0, upper=100)
    for col in READING_COLUMNS:
        df[col] = df[col].interpolate(method="linear", limit=3, limit_direction="both", limit_area="inside")
    return df[["date", *READING_COLUMNS]]


def fetch_live_readings(region: Region, days: int) -> pd.DataFrame:
    end = date.today() - timedelta(days=1)  # today is still incomplete
    start = end - timedelta(days=days - 1)
    base = {
        "latitude": float(region.latitude),
        "longitude": float(region.longitude),
        "start_date": start.isoformat(),
        "timezone": "Africa/Nairobi",
    }

    def pull(end_day: date):
        params = {**base, "end_date": end_day.isoformat()}
        weather = fetch(WEATHER_URL, {**params, "daily": ",".join(WEATHER_DAILY_VARS)})
        flood = fetch(FLOOD_URL, {**params, "daily": ",".join(FLOOD_DAILY_VARS)})
        return weather, flood

    try:
        weather, flood = pull(end)
    except RuntimeError:
        weather, flood = pull(date.today() - timedelta(days=ARCHIVE_FALLBACK_DAYS))

    merged = _daily_frame(weather).merge(_daily_frame(flood), on="date", how="outer")
    return clean_live(merged.rename(columns=COLUMN_RENAME))


def ingest_live(slugs: list[str], days: int = 30, echo=print) -> dict[str, int]:
    results = {}
    for slug in slugs:
        region = get_region(slug)
        readings = fetch_live_readings(region, days)
        results[slug] = insert_readings(slug, readings)
        latest = readings.dropna(subset=READING_COLUMNS)["date"].max()
        latest_text = latest.date() if pd.notna(latest) else "none"
        echo(f"{slug:16s} +{results[slug]} new readings (latest complete day: {latest_text})")
        time.sleep(1)
    return results


def replay(slugs: list[str], start, end, history_days: int = 30, delay: float = 0.0, echo=print) -> int:
    """
    Load `history_days` days before `start` in one go (so the model has
    context for lags and rolling windows), then feed `start`..`end` one day
    at a time, waiting `delay` seconds between days.
    """
    start, end = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    if end < start:
        raise ValueError("--end must be on or after --start")
    for slug in slugs:
        get_region(slug)

    master = pd.read_csv(MASTER_CSV, parse_dates=["date"])
    first, last = master["date"].min(), master["date"].max()
    if start - pd.Timedelta(days=history_days) < first or end > last:
        raise ValueError(
            f"Replay data covers {first.date()} to {last.date()}; "
            f"choose dates inside that range (history included)."
        )

    window = master[
        (master["date"] >= start - pd.Timedelta(days=history_days)) & (master["date"] <= end)
    ]
    total = 0

    if history_days:
        history = window[window["date"] < start]
        loaded = {slug: insert_readings(slug, history[history["region"] == slug]) for slug in slugs}
        total += sum(loaded.values())
        echo(f"history {history_days} days before {start.date()}: "
             + "  ".join(f"{slug} +{n}" for slug, n in loaded.items()))

    for day in pd.date_range(start, end, freq="D"):
        today_rows = window[window["date"] == day]
        added = {slug: insert_readings(slug, today_rows[today_rows["region"] == slug]) for slug in slugs}
        total += sum(added.values())
        echo(f"{day.date()}  " + "  ".join(f"{slug} +{n}" for slug, n in added.items()))
        if delay and day < end:
            time.sleep(delay)
    return total


def reset_readings() -> dict[str, int]:
    """Delete every reading and everything that depends on it, children first."""
    deleted = {}
    for model in (Feedback, Alert, Anomaly, SensorReading):
        deleted[model.__tablename__] = db.session.execute(db.delete(model)).rowcount
    db.session.commit()
    return deleted
