"""
Server-rendered pages of the dashboard (Jinja templates in app/templates).
The pages are thin shells: their data comes from the JSON API in routes.py,
fetched by the scripts in app/static/js, so the dashboard and the API can
never disagree. Navigation changes with the logged-in user's role.
"""
from urllib.parse import urlparse

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.auth import STAFF_ROLES, roles_required
from app.models import USER_ROLES

pages = Blueprint("pages", __name__)

NAV = [
    # (endpoint, label, roles allowed or None for every logged-in user)
    ("pages.overview", "Overview", None),
    ("pages.regions", "Regions", None),
    ("pages.alerts", "Alerts", None),
    ("pages.users", "Users", ("administrator",)),
]


@pages.app_context_processor
def navigation():
    if not current_user.is_authenticated:
        return {"nav": []}
    items = [(endpoint, label) for endpoint, label, roles in NAV if roles is None or current_user.role in roles]
    return {"nav": items}


def safe_next(target: str | None) -> str:
    """Only follow ?next= to a path on this site (no open redirects)."""
    if (target and target.startswith("/") and not target.startswith("//")
            and "\\" not in target and not urlparse(target).netloc):
        return target
    return url_for("pages.overview")


@pages.app_errorhandler(403)
def forbidden(exc):
    if request.path.startswith("/api/"):
        return {"error": "you do not have permission to do this"}, 403
    return render_template("forbidden.html", active=None), 403


@pages.get("/login")
def login():
    if current_user.is_authenticated:
        return redirect(safe_next(request.args.get("next")))
    return render_template("login.html", next_url=safe_next(request.args.get("next")))


@pages.get("/register")
def register():
    if current_user.is_authenticated:
        return redirect(url_for("pages.overview"))
    return render_template("register.html")


@pages.get("/")
@login_required
def overview():
    return render_template("overview.html", active="pages.overview")


@pages.get("/regions")
@login_required
def regions():
    return render_template("regions.html", active="pages.regions")


@pages.get("/alerts")
@login_required
def alerts():
    return render_template("alerts.html", active="pages.alerts",
                           can_act=current_user.role in STAFF_ROLES)


@pages.get("/users")
@roles_required("administrator")
def users():
    return render_template("users.html", active="pages.users", roles=USER_ROLES)
