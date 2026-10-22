"""
API routes. For now only a health check, which proves the three layers are
wired together: Flask is serving, MySQL answers, and every seeded region has
a trained model bundle that predict.py actually loads.
"""
from flask import Blueprint, jsonify
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models import Region
from src.model.predict import load_model

api = Blueprint("api", __name__, url_prefix="/api")


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
        {
            "slug": region.slug,
            "name": region.name,
            "model_available": _model_loads(region.slug),
        }
        for region in regions
    ]
    healthy = bool(region_status) and all(r["model_available"] for r in region_status)
    return jsonify(
        status="ok" if healthy else "degraded",
        database="ok",
        regions=region_status,
    ), (200 if healthy else 503)
