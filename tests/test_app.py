"""
Tests for the Flask skeleton. The health and schema tests use the real MySQL
database from .env and are skipped (not failed) if MySQL is not running, so
the model tests still run on their own.
"""
import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError

from app import create_app
from app.extensions import db

EXPECTED_SLUGS = {"nairobi", "kisumu", "tana_river_hola"}


@pytest.fixture(scope="module")
def app():
    app = create_app({"TESTING": True})
    with app.app_context():
        try:
            db.session.execute(db.text("SELECT 1"))
        except OperationalError as exc:
            pytest.skip(f"MySQL not usable (is it running? sudo service mysql start): {exc.orig}")
    return app


def test_health_reports_db_and_all_three_models(app):
    response = app.test_client().get("/api/health")
    body = response.get_json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert {r["slug"] for r in body["regions"]} == EXPECTED_SLUGS
    assert all(r["model_available"] for r in body["regions"])


def test_models_match_database_schema(app):
    with app.app_context():
        inspector = inspect(db.engine)
        for table in db.metadata.sorted_tables:
            db_columns = {c["name"] for c in inspector.get_columns(table.name)}
            model_columns = {c.name for c in table.columns}
            assert db_columns == model_columns, f"{table.name} differs from database/schema.sql"


def test_health_returns_503_when_database_is_down():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "mysql+pymysql://nobody:nothing@127.0.0.1:1/none",
    })
    response = app.test_client().get("/api/health")
    assert response.status_code == 503
    assert response.get_json()["database"] == "unreachable"
