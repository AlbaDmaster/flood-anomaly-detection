"""
Tests for the dashboard pages: logged-out visitors are sent to the login
page, the menu matches the user's role, and ?next= cannot redirect off-site.
The data on each page comes from the API, which tests/test_api.py covers.
"""
import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from app import create_app
from app.auth import create_user
from app.config import database_url
from app.extensions import db
from app.models import Feedback, User

TEST_DB = "flood_anomaly_detection_test"
PASSWORD = "correct-horse-1"


@pytest.fixture(scope="module")
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": database_url(TEST_DB)})
    with app.app_context():
        try:
            db.session.execute(db.text("SELECT COUNT(*) FROM `USER`"))
        except (OperationalError, ProgrammingError) as exc:
            pytest.skip(f"Test database not ready - run the one-time test database setup: {exc.orig}")
    return app


@pytest.fixture(autouse=True)
def users(app):
    with app.app_context():
        assert db.engine.url.database == TEST_DB  # never wipe the real database
        db.session.execute(db.delete(Feedback))
        db.session.execute(db.delete(User))
        db.session.commit()
        for role in ("public", "administrator"):
            create_user(role.title(), f"{role}@example.com", PASSWORD, role=role)
        yield


@pytest.fixture
def client(app):
    return app.test_client()


def sign_in(client, role):
    assert client.post("/api/auth/login", json={"email": f"{role}@example.com", "password": PASSWORD}).status_code == 200


@pytest.mark.parametrize("path", ["/", "/regions", "/alerts", "/users"])
def test_pages_redirect_to_login_when_signed_out(client, path):
    response = client.get(path)
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?next=")


def test_login_and_register_pages_render(client):
    login = client.get("/login")
    assert login.status_code == 200 and b'id="login-form"' in login.data
    register = client.get("/register")
    assert register.status_code == 200 and b'id="register-form"' in register.data


def test_public_user_sees_overview_without_admin_menu(client):
    sign_in(client, "public")
    page = client.get("/")
    assert page.status_code == 200
    assert b'id="kpi-regions"' in page.data and b'id="recent-alerts"' in page.data
    assert b'href="/alerts"' in page.data and b'href="/users"' not in page.data
    forbidden = client.get("/users")
    assert forbidden.status_code == 403 and b"cannot open this page" in forbidden.data


def test_administrator_sees_users_menu(client):
    sign_in(client, "administrator")
    page = client.get("/")
    assert b'href="/users"' in page.data
    assert client.get("/users").status_code == 200


def test_signed_in_user_is_sent_on_from_login_page(client):
    sign_in(client, "public")
    assert client.get("/login?next=/alerts").headers["Location"] == "/alerts"
    assert client.get("/register").headers["Location"] == "/"


@pytest.mark.parametrize("target", ["https://evil.example", "//evil.example", "/\\evil.example", "/\t/evil.example", "javascript:alert(1)"])
def test_next_cannot_redirect_off_site(client, target):
    sign_in(client, "public")
    assert client.get("/login", query_string={"next": target}).headers["Location"] == "/"


def test_static_files_are_served(client):
    assert client.get("/static/css/app.css").status_code == 200
    assert client.get("/static/js/api.js").status_code == 200
