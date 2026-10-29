"""
Replay demo controls. Runs a historical flood replay in a background thread
so an administrator can start, pause, resume, stop and change its speed from
the dashboard. The overview page refreshes itself every few seconds while a
replay runs; the regions and alerts pages show the new data when reloaded.

  GET  /api/replay          any signed-in user: current replay status
  POST /api/replay/start    administrator: {"start", "end", "regions"?, "delay"?, "history_days"?, "reset"?}
  POST /api/replay/pause    administrator
  POST /api/replay/resume   administrator
  POST /api/replay/stop     administrator
  POST /api/replay/speed    administrator: {"delay": seconds per day}

Each day is loaded and scored exactly like `flask --app run replay`,
including the emails for HIGH alerts. The
runner lives in the web server process, so it is meant for the single-process
development server used in demos, not for a multi-worker deployment.
"""
import threading
from datetime import date, datetime

import pandas as pd
from flask import Blueprint, current_app, jsonify
from flask_login import login_required

from app import ingestion, scoring
from app.auth import roles_required
from app.extensions import db
from app.routes import error, json_body

MAX_DELAY_SECONDS = 10
MAX_HISTORY_DAYS = 90
RECENT_ALERTS_KEPT = 8

replay_api = Blueprint("replay_api", __name__, url_prefix="/api/replay")


class ReplayRunner:
    def __init__(self):
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._unpaused = threading.Event()
        self._thread = None
        self._delay = 0.0
        self._status = {"state": "idle"}

    def status(self) -> dict:
        with self._lock:
            status = dict(self._status)
            if "recent_alerts" in status:
                status["recent_alerts"] = list(status["recent_alerts"])
            return status

    def _update(self, **changes):
        with self._lock:
            self._status.update(changes)

    def is_active(self) -> bool:
        return self.status()["state"] in ("starting", "running", "paused")

    def _thread_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, app, slugs, start, end, delay, history_days, reset):
        start_ts, end_ts, window = ingestion.replay_window(slugs, start, end, history_days)
        days = list(pd.date_range(start_ts, end_ts, freq="D"))
        with self._lock:
            if self._status["state"] in ("starting", "running", "paused") and self._thread_alive():
                raise RuntimeError("a replay is already running - stop it first")
            self._delay = delay
            self._stop.clear()
            self._unpaused.set()
            self._status = {
                "state": "starting", "start": start_ts.date().isoformat(), "end": end_ts.date().isoformat(),
                "regions": slugs, "delay": delay, "history_days": history_days, "reset": reset,
                "day": 0, "total_days": len(days), "current_date": None,
                "new_readings": 0, "scored": 0, "alerts": 0, "emails_sent": 0, "emails_failed": 0,
                "recent_alerts": [],
                "message": None, "started_at": datetime.now().isoformat(timespec="seconds"),
            }
            self._thread = threading.Thread(
                target=self._run, args=(app, slugs, start_ts, window, days, delay, history_days, reset),
                name="flood-replay", daemon=True,
            )
            self._thread.start()

    def _record(self, readings: int, results: list[dict]):
        alerts = [r for r in results if r["alert"]]
        with self._lock:
            self._status["new_readings"] += readings
            self._status["scored"] += len(results)
            self._status["alerts"] += len(alerts)
            self._status["emails_sent"] += sum(r.get("email") == "sent" for r in alerts)
            self._status["emails_failed"] += sum(r.get("email") == "failed" for r in alerts)
            recent = [{"region": r["region"], "date": r["date"], "severity": r["severity"],
                       "anomaly_score": r["anomaly_score"]} for r in alerts]
            self._status["recent_alerts"] = (recent[::-1] + self._status["recent_alerts"])[:RECENT_ALERTS_KEPT]

    def _run(self, app, slugs, start_ts, window, days, delay, history_days, reset):
        with app.app_context():
            try:
                if reset:
                    ingestion.reset_readings()
                if history_days:
                    history = window[window["date"] < start_ts]
                    loaded = sum(ingestion.insert_readings(s, history[history["region"] == s]) for s in slugs)
                    self._record(loaded, scoring.score_pending(slugs))
                self._update(state="running")
                for index, day in enumerate(days, start=1):
                    self._unpaused.wait()
                    if self._stop.is_set():
                        break
                    rows = window[window["date"] == day]
                    added = sum(ingestion.insert_readings(s, rows[rows["region"] == s]) for s in slugs)
                    self._record(added, scoring.score_pending(slugs))
                    self._update(day=index, current_date=day.date().isoformat())
                    with self._lock:
                        wait = self._delay
                    if wait and index < len(days) and self._stop.wait(wait):
                        break
                stopped = self._stop.is_set()
                message = "Stopped by an administrator." if stopped else "Replay complete."
                if self.status()["emails_failed"]:
                    message += (f" {self.status()['emails_failed']} alert emails failed; check the mail"
                                " settings in .env, then run: flask --app run notify")
                if self.status()["new_readings"] == 0:
                    message += (" No new readings were added because these days were already loaded;"
                                " tick 'Clear all current readings' to replay them.")
                self._update(state="stopped" if stopped else "finished", message=message)
            except Exception as exc:  # report any failure on the dashboard instead of dying silently
                self._update(state="error", message=f"Replay failed: {exc}")
                try:
                    db.session.rollback()
                except Exception:
                    pass
            finally:
                try:
                    db.session.remove()
                except Exception:
                    pass

    def pause(self):
        with self._lock:
            if self._status["state"] != "running":
                raise RuntimeError("nothing is running to pause")
            self._unpaused.clear()
            self._status["state"] = "paused"

    def resume(self):
        with self._lock:
            if self._status["state"] != "paused":
                raise RuntimeError("the replay is not paused")
            self._status["state"] = "running"
            self._unpaused.set()

    def stop(self):
        with self._lock:
            if self._status["state"] not in ("starting", "running", "paused"):
                raise RuntimeError("no replay is running")
            self._stop.set()
            self._unpaused.set()
            if not self._thread_alive():  # the worker died without reporting; unstick the status
                self._status.update(state="stopped", message="Stopped.")

    def set_delay(self, delay: float):
        with self._lock:
            if self._status["state"] not in ("starting", "running", "paused"):
                raise RuntimeError("no replay is running")
            self._delay = delay
            self._status["delay"] = delay

    def wait(self, timeout=None) -> bool:
        """Block until the current replay thread ends (used by tests)."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return not self.is_active()


runner = ReplayRunner()


def parse_delay(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= MAX_DELAY_SECONDS:
        raise ValueError(f"delay must be between 0 and {MAX_DELAY_SECONDS} seconds")
    return float(value)


def parse_day(value, name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a date like 2024-04-01")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} must be a date like 2024-04-01")


@replay_api.get("")
@login_required
def replay_status():
    return jsonify(replay=runner.status())


@replay_api.post("/start")
@roles_required("administrator")
def start_replay():
    data = json_body()
    try:
        start = parse_day(data.get("start"), "start")
        end = parse_day(data.get("end"), "end")
        regions = data.get("regions")
        if regions is None:
            regions = ingestion.all_region_slugs()
        if not isinstance(regions, list) or not regions or not all(isinstance(r, str) for r in regions):
            raise ValueError("regions must be a non-empty list of region slugs")
        delay = parse_delay(data.get("delay", 2))
        history_days = data.get("history_days", 30)
        if isinstance(history_days, bool) or not isinstance(history_days, int) or not 0 <= history_days <= MAX_HISTORY_DAYS:
            raise ValueError(f"history_days must be a whole number from 0 to {MAX_HISTORY_DAYS}")
        reset = data.get("reset", False)
        if not isinstance(reset, bool):
            raise ValueError("reset must be true or false")
        runner.start(current_app._get_current_object(), list(dict.fromkeys(regions)), start, end,
                     delay, history_days, reset)
    except ValueError as exc:
        return error(str(exc), 400)
    except RuntimeError as exc:
        return error(str(exc), 409)
    return jsonify(replay=runner.status()), 202


def _control(action):
    try:
        action()
    except RuntimeError as exc:
        return error(str(exc), 409)
    return jsonify(replay=runner.status())


@replay_api.post("/pause")
@roles_required("administrator")
def pause_replay():
    return _control(runner.pause)


@replay_api.post("/resume")
@roles_required("administrator")
def resume_replay():
    return _control(runner.resume)


@replay_api.post("/stop")
@roles_required("administrator")
def stop_replay():
    return _control(runner.stop)


@replay_api.post("/speed")
@roles_required("administrator")
def change_speed():
    try:
        delay = parse_delay(json_body().get("delay"))
    except ValueError as exc:
        return error(str(exc), 400)
    return _control(lambda: runner.set_delay(delay))
