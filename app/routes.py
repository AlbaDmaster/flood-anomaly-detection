"""
REST API used by the dashboard. Every endpoint returns JSON.

  GET   /api/health                        no login: Flask, MySQL and models are up
  GET   /api/regions                       latest reading, score and open alerts per region
  GET   /api/regions/<slug>/readings       readings with anomaly scores (?start=&end=)
  GET   /api/alerts                        newest first (?region=&severity=&status=&page=&per_page=)
  GET   /api/alerts/<id>                   one alert with its feedback
  POST  /api/alerts/<id>/acknowledge       staff
  POST  /api/alerts/<id>/feedback          staff: {"is_confirmed": true|false, "comment": "..."}
  GET   /api/admin/users                   administrator
  POST  /api/admin/users                   administrator: create a user with any role
  PATCH /api/admin/users/<id>              administrator: {"role": "..."}

All endpoints except /api/health need a logged-in user (any role); the ones
marked staff need responder, authority or administrator.
"""
from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload, selectinload

from app.auth import STAFF_ROLES, create_user, roles_required, validate_new_user
from app.extensions import db
from app.models import (
    ALERT_STATUSES, SEVERITY_LEVELS, USER_ROLES,
    Alert, Anomaly, Feedback, Region, SensorReading, User,
)
from src.model.predict import load_model

api = Blueprint("api", __name__, url_prefix="/api")

READING_FIELDS = ("rainfall", "river_discharge", "humidity", "temperature", "soil_moisture")
DEFAULT_READING_DAYS = 90
MAX_READING_DAYS = 366
MAX_PER_PAGE = 100
OPEN_STATUSES = ("pending", "sent", "failed")


def error(message: str, status: int):
    return jsonify(error=message), status


def json_body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@api.app_errorhandler(404)
def not_found(_exc):
    if request.path.startswith("/api/"):
        return error("not found", 404)
    return _exc


@api.app_errorhandler(405)
def method_not_allowed(_exc):
    if request.path.startswith("/api/"):
        return error("method not allowed", 405)
    return _exc


# ---------- serialisers ----------

def reading_dict(reading: SensorReading) -> dict:
    anomaly = reading.anomaly
    return {
        "reading_id": reading.reading_id,
        "date": reading.timestamp.date().isoformat(),
        **{field: getattr(reading, field) for field in READING_FIELDS},
        "anomaly_score": anomaly.anomaly_score if anomaly else None,
        "severity": anomaly.severity_level if anomaly else None,
    }


def alert_dict(alert: Alert, with_feedback: bool = False) -> dict:
    reading = alert.anomaly.reading
    data = {
        "alert_id": alert.alert_id,
        "region": reading.region.slug,
        "region_name": reading.region.name,
        "date": reading.timestamp.date().isoformat(),
        "severity": alert.anomaly.severity_level,
        "anomaly_score": alert.anomaly.anomaly_score,
        "message": alert.message,
        "channel": alert.channel,
        "status": alert.status,
        "sent_at": alert.sent_at.isoformat() if alert.sent_at else None,
        "feedback_count": len(alert.feedback),
    }
    if with_feedback:
        data["feedback"] = [
            {
                "feedback_id": fb.feedback_id,
                "user": fb.user.name,
                "is_confirmed": fb.is_confirmed,
                "comment": fb.comment,
                "created_at": fb.created_at.isoformat() if fb.created_at else None,
            }
            for fb in sorted(alert.feedback, key=lambda fb: fb.feedback_id)
        ]
    return data


def parse_date(value: str | None, name: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} must be a date like 2024-05-01")


def model_thresholds(slug: str) -> dict | None:
    try:
        bundle = load_model(slug)
    except Exception:
        return None
    return {"moderate": bundle["moderate_threshold"], "high": bundle["high_threshold"]}


def get_alert_or_404(alert_id: int):
    alert = db.session.get(Alert, alert_id)
    return alert, (None if alert else error("alert not found", 404))


# ---------- health ----------

def _model_loads(slug: str) -> bool:
    try:
        load_model(slug)
        return True
    except Exception:
        return False


@api.get("/health")
def health():
    try:
        regions = db.session.execute(db.select(Region).order_by(Region.region_id)).scalars().all()
    except SQLAlchemyError as exc:
        return jsonify(status="error", database="unreachable", detail=type(exc).__name__), 503

    region_status = [
        {"slug": region.slug, "name": region.name, "model_available": _model_loads(region.slug)}
        for region in regions
    ]
    healthy = bool(region_status) and all(r["model_available"] for r in region_status)
    return jsonify(
        status="ok" if healthy else "degraded",
        database="ok",
        regions=region_status,
    ), (200 if healthy else 503)


# ---------- regions and readings ----------

@api.get("/regions")
@login_required
def list_regions():
    regions = db.session.execute(db.select(Region).order_by(Region.region_id)).scalars().all()
    payload = []
    for region in regions:
        latest = db.session.execute(
            db.select(SensorReading).filter_by(region_id=region.region_id)
            .order_by(SensorReading.timestamp.desc()).limit(1)
        ).scalar_one_or_none()
        latest_scored = db.session.execute(
            db.select(SensorReading).join(Anomaly).filter(SensorReading.region_id == region.region_id)
            .order_by(SensorReading.timestamp.desc()).limit(1)
        ).scalar_one_or_none()
        open_alerts = db.session.scalar(
            db.select(db.func.count()).select_from(Alert).join(Anomaly).join(SensorReading)
            .where(SensorReading.region_id == region.region_id, Alert.status.in_(OPEN_STATUSES))
        )
        payload.append({
            "slug": region.slug,
            "name": region.name,
            "county": region.county,
            "latitude": float(region.latitude),
            "longitude": float(region.longitude),
            "latest_reading": reading_dict(latest) if latest else None,
            "latest_scored": reading_dict(latest_scored) if latest_scored else None,
            "open_alerts": open_alerts,
        })
    return jsonify(regions=payload)


@api.get("/regions/<slug>/readings")
@login_required
def region_readings(slug):
    region = db.session.execute(db.select(Region).filter_by(slug=slug)).scalar_one_or_none()
    if region is None:
        return error("region not found", 404)
    try:
        start = parse_date(request.args.get("start"), "start")
        end = parse_date(request.args.get("end"), "end")
    except ValueError as exc:
        return error(str(exc), 400)

    if end is None:
        latest = db.session.scalar(
            db.select(db.func.max(SensorReading.timestamp)).filter_by(region_id=region.region_id)
        )
        end = latest.date() if latest else date.today()
    if start is None:
        try:
            start = end - timedelta(days=DEFAULT_READING_DAYS - 1)
        except OverflowError:
            return error("end is too early", 400)
    if end < start:
        return error("end must be on or after start", 400)
    if (end - start).days + 1 > MAX_READING_DAYS:
        return error(f"choose a range of at most {MAX_READING_DAYS} days", 400)

    readings = db.session.execute(
        db.select(SensorReading)
        .where(SensorReading.region_id == region.region_id,
               SensorReading.timestamp >= datetime.combine(start, datetime.min.time()),
               SensorReading.timestamp <= datetime.combine(end, datetime.min.time()))
        .order_by(SensorReading.timestamp)
    ).scalars().all()
    return jsonify(region=slug, start=start.isoformat(), end=end.isoformat(),
                   thresholds=model_thresholds(slug),
                   readings=[reading_dict(r) for r in readings])


# ---------- alerts ----------

@api.get("/alerts")
@login_required
def list_alerts():
    query = db.select(Alert).join(Anomaly).join(SensorReading).join(Region)
    region, severity, status = (request.args.get(k) for k in ("region", "severity", "status"))
    if region:
        if db.session.execute(db.select(Region).filter_by(slug=region)).scalar_one_or_none() is None:
            return error("region not found", 404)
        query = query.where(Region.slug == region)
    if severity:
        if severity not in SEVERITY_LEVELS:
            return error(f"severity must be one of {', '.join(SEVERITY_LEVELS)}", 400)
        query = query.where(Anomaly.severity_level == severity)
    if status:
        if status not in ALERT_STATUSES:
            return error(f"status must be one of {', '.join(ALERT_STATUSES)}", 400)
        query = query.where(Alert.status == status)

    try:
        page = min(max(int(request.args.get("page", 1)), 1), 1_000_000)
        per_page = min(max(int(request.args.get("per_page", 20)), 1), MAX_PER_PAGE)
    except ValueError:
        return error("page and per_page must be whole numbers", 400)

    total = db.session.scalar(db.select(db.func.count()).select_from(query.subquery()))
    alerts = db.session.execute(
        query.options(
            selectinload(Alert.feedback),
            joinedload(Alert.anomaly).joinedload(Anomaly.reading).joinedload(SensorReading.region),
        )
        .order_by(SensorReading.timestamp.desc(), Alert.alert_id.desc())
        .limit(per_page).offset((page - 1) * per_page)
    ).scalars().all()
    return jsonify(alerts=[alert_dict(a) for a in alerts], page=page, per_page=per_page, total=total)


@api.get("/alerts/<int:alert_id>")
@login_required
def get_alert(alert_id):
    alert, missing = get_alert_or_404(alert_id)
    return missing or jsonify(alert=alert_dict(alert, with_feedback=True))


@api.post("/alerts/<int:alert_id>/acknowledge")
@roles_required(*STAFF_ROLES)
def acknowledge_alert(alert_id):
    alert, missing = get_alert_or_404(alert_id)
    if missing:
        return missing
    alert.status = "acknowledged"
    db.session.commit()
    return jsonify(alert=alert_dict(alert))


@api.post("/alerts/<int:alert_id>/feedback")
@roles_required(*STAFF_ROLES)
def give_feedback(alert_id):
    alert, missing = get_alert_or_404(alert_id)
    if missing:
        return missing
    data = json_body()
    if not isinstance(data.get("is_confirmed"), bool):
        return error("is_confirmed must be true (real flood risk) or false (false alarm)", 400)
    if data.get("comment") is not None and not isinstance(data.get("comment"), str):
        return error("comment must be text", 400)
    comment = (data.get("comment") or "").strip() or None
    if comment and len(comment) > 2000:
        return error("comment must be at most 2000 characters", 400)
    feedback = Feedback(alert_id=alert.alert_id, user_id=current_user.user_id,
                        is_confirmed=data["is_confirmed"], comment=comment)
    db.session.add(feedback)
    db.session.commit()
    return jsonify(alert=alert_dict(alert, with_feedback=True)), 201


# ---------- user administration ----------

@api.get("/admin/users")
@roles_required("administrator")
def list_users():
    users = db.session.execute(db.select(User).order_by(User.user_id)).scalars().all()
    return jsonify(users=[u.to_dict() for u in users])


@api.post("/admin/users")
@roles_required("administrator")
def admin_create_user():
    data = json_body()
    name, email, password = str(data.get("name", "")), str(data.get("email", "")), str(data.get("password", ""))
    phone = str(data.get("phone") or "").strip()
    role = data.get("role", "public")
    if role not in USER_ROLES:
        return error(f"role must be one of {', '.join(USER_ROLES)}", 400)
    problem = validate_new_user(name, email, password, phone)
    if problem:
        return error(problem, 409 if "already exists" in problem else 400)
    try:
        user = create_user(name, email, password, role=role, phone=phone or None)
    except ValueError as exc:
        return error(str(exc), 409)
    return jsonify(user=user.to_dict()), 201


@api.patch("/admin/users/<int:user_id>")
@roles_required("administrator")
def admin_change_role(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        return error("user not found", 404)
    role = json_body().get("role")
    if role not in USER_ROLES:
        return error(f"role must be one of {', '.join(USER_ROLES)}", 400)
    if user.user_id == current_user.user_id and role != user.role:
        return error("you cannot change your own role", 400)
    user.role = role
    db.session.commit()
    return jsonify(user=user.to_dict())
