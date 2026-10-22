"""Shared Flask extensions, created once and bound to the app in create_app()."""
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
login_manager = LoginManager()
