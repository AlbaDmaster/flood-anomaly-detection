"""
Flask application factory. Builds the app, points SQLAlchemy at the MySQL
database described in .env, and registers the API routes and the
command-line ingestion commands (flask --app run ingest-live / replay).

Start the server from the project root with: python run.py
"""
from flask import Flask

from app.config import database_url
from app.extensions import db


def create_app(config_overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 3600}
    if config_overrides:
        app.config.update(config_overrides)
    if "SQLALCHEMY_DATABASE_URI" not in app.config:
        app.config["SQLALCHEMY_DATABASE_URI"] = database_url()

    db.init_app(app)

    from app import models  # noqa: F401  registers the tables with SQLAlchemy
    from app.cli import register_cli
    from app.routes import api
    app.register_blueprint(api)
    register_cli(app)

    return app
