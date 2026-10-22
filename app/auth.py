"""
Login, logout, self-registration and role-based access control.

Sessions are cookie-based (Flask-Login); passwords are stored only as salted
hashes (werkzeug's scrypt). Roles come from USER.role:

  public         read-only: region status and alerts
  responder      + acknowledge alerts and give feedback on them
  authority      same as responder (county / disaster-management officers)
  administrator  everything, including managing users

Anyone can register, but always as 'public'. Responder, authority and
administrator accounts are created from the command line with:
    flask --app run users create
"""
import re
from functools import wraps

import click
from flask import Blueprint, abort, jsonify, redirect, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, login_manager
from app.models import USER_ROLES, User

STAFF_ROLES = ("responder", "authority", "administrator")
MIN_PASSWORD_LENGTH = 8
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DUMMY_HASH = generate_password_hash("timing-equaliser")  # keeps login timing the same for unknown emails

auth = Blueprint("auth", __name__, url_prefix="/api/auth", cli_group="users")


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))


@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith("/api/"):
        return jsonify(error="login required"), 401
    return redirect(url_for("pages.login", next=request.full_path.rstrip("?")))


def roles_required(*roles):
    """Route decorator: 401 if not logged in, 403 if logged in with a role not in `roles`."""
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if getattr(current_user, "role", None) not in roles:
                if not request.path.startswith("/api/"):
                    abort(403)
                return jsonify(error="you do not have permission to do this"), 403
            return view(*args, **kwargs)
        return wrapped
    return decorator


def find_user(email: str):
    return db.session.execute(
        db.select(User).where(User.email == email.strip().lower())
    ).scalar_one_or_none()


def validate_new_user(name: str, email: str, password: str, phone: str = "") -> str | None:
    if not name.strip():
        return "name is required"
    if len(name.strip()) > 150:
        return "name must be at most 150 characters"
    if not EMAIL_PATTERN.match(email.strip()) or len(email.strip()) > 150:
        return "a valid email is required"
    if len(phone) > 20:
        return "phone must be at most 20 characters"
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"password must be at least {MIN_PASSWORD_LENGTH} characters"
    if find_user(email):
        return "an account with this email already exists"
    return None


def create_user(name: str, email: str, password: str, role: str = "public", phone: str | None = None) -> User:
    user = User(name=name.strip(), email=email.strip().lower(), phone=(phone or None),
                role=role, password_hash=generate_password_hash(password))
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:  # two sign-ups with the same email at the same moment
        db.session.rollback()
        raise ValueError("an account with this email already exists")
    return user


@auth.post("/register")
def register():
    data = request.get_json(silent=True) or {}
    name, email, password = str(data.get("name", "")), str(data.get("email", "")), str(data.get("password", ""))
    phone = str(data.get("phone") or "").strip()
    error = validate_new_user(name, email, password, phone)
    if error:
        return jsonify(error=error), (409 if "already exists" in error else 400)
    try:
        user = create_user(name, email, password, role="public", phone=phone or None)
    except ValueError as exc:
        return jsonify(error=str(exc)), 409
    login_user(user)
    return jsonify(user=user.to_dict()), 201


@auth.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    user = find_user(str(data.get("email", "")))
    password_ok = check_password_hash(user.password_hash if user else _DUMMY_HASH, str(data.get("password", "")))
    if user is None or not password_ok:
        return jsonify(error="incorrect email or password"), 401
    login_user(user, remember=bool(data.get("remember")))
    return jsonify(user=user.to_dict())


@auth.post("/logout")
@login_required
def logout():
    logout_user()
    return jsonify(message="logged out")


@auth.get("/me")
@login_required
def me():
    return jsonify(user=current_user.to_dict())


@auth.cli.command("create")
@click.option("--name", prompt=True)
@click.option("--email", prompt=True)
@click.option("--role", type=click.Choice(USER_ROLES), prompt=True, default="administrator", show_default=True)
@click.option("--phone", default="", help="Optional phone number.")
@click.password_option(confirmation_prompt=True)
def create_user_command(name, email, role, phone, password):
    """Create a user with any role (use this for the first administrator)."""
    error = validate_new_user(name, email, password, phone)
    if error:
        raise click.ClickException(error)
    try:
        user = create_user(name, email, password, role=role, phone=phone or None)
    except ValueError as exc:
        raise click.ClickException(str(exc))
    click.echo(f"Created {user.role} account #{user.user_id} for {user.email}")


@auth.cli.command("list")
def list_users_command():
    """List all users and their roles."""
    for user in db.session.execute(db.select(User).order_by(User.user_id)).scalars():
        click.echo(f"#{user.user_id:<4} {user.role:14s} {user.email}  ({user.name})")
