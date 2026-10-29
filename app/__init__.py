"""
Flask application factory. Builds the app, points SQLAlchemy at the MySQL
database described in .env, sets up login sessions, and registers the API
routes and the command-line commands (ingest-live, replay, score, notify, users ...).

Start the server from the project root with: python run.py
"""
from datetime import timedelta

from flask import Flask

from app.config import database_url, mail_settings, secret_key
from app.extensions import db, login_manager


def create_app(config_overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 3600}
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
    app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=14)
    if config_overrides:
        app.config.update(config_overrides)
    if app.config.get("TESTING"):
        app.config.setdefault("MAIL_BACKEND", "memory")  # tests never send real email
    else:
        for key, value in mail_settings().items():
            app.config.setdefault(key, value)
    if "SQLALCHEMY_DATABASE_URI" not in app.config:
        app.config["SQLALCHEMY_DATABASE_URI"] = database_url()
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = secret_key() or ("test-only-secret" if app.config.get("TESTING") else None)
    if not app.config["SECRET_KEY"]:
        raise RuntimeError("Missing SECRET_KEY in .env - it signs login sessions. See .env.example.")

    db.init_app(app)
    login_manager.init_app(app)

    from app import models  # noqa: F401  registers the tables with SQLAlchemy
    from app.auth import auth
    from app.cli import register_cli
    from app.pages import pages
    from app.replay import replay_api
    from app.routes import api
    app.register_blueprint(api)
    app.register_blueprint(auth)
    app.register_blueprint(pages)
    app.register_blueprint(replay_api)
    register_cli(app)

    return app
