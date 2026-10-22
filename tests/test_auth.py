"""
Tests for login, registration and role-based access. They use the separate
test database and empty the USER table (and FEEDBACK, which points at it)
before every test. A throwaway route protected by roles_required() checks
the 401 / 403 / 200 behaviour for each role.
"""
import pytest
from flask import jsonify
from sqlalchemy.exc import OperationalError, ProgrammingError

from app import create_app
from app.auth import STAFF_ROLES, create_user, roles_required
from app.config import database_url
from app.extensions import db
from app.models import Feedback, User

TEST_DB = "flood_anomaly_detection_test"
PASSWORD = "correct-horse-1"


@pytest.fixture(scope="module")
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": database_url(TEST_DB)})

    @app.get("/api/test/staff-only")
    @roles_required(*STAFF_ROLES)
    def staff_only():
        return jsonify(ok=True)

    with app.app_context():
        try:
            db.session.execute(db.text("SELECT COUNT(*) FROM `USER`"))
        except (OperationalError, ProgrammingError) as exc:
            pytest.skip(f"Test database not ready - run the one-time test database setup: {exc.orig}")
    return app


@pytest.fixture(autouse=True)
def clean_users(app):
    with app.app_context():
        assert db.engine.url.database == TEST_DB  # never wipe the real database
        db.session.execute(db.delete(Feedback))
        db.session.execute(db.delete(User))
        db.session.commit()
        yield


@pytest.fixture
def client(app):
    return app.test_client()


def make_user(role, email=None):
    return create_user("Test User", email or f"{role}@example.com", PASSWORD, role=role)


def login(client, email, password=PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_register_creates_public_user_and_logs_in(client):
    response = client.post("/api/auth/register", json={
        "name": "Amina", "email": "Amina@Example.com", "password": PASSWORD, "role": "administrator",
    })
    assert response.status_code == 201
    assert response.get_json()["user"]["role"] == "public"          # cannot self-promote
    assert response.get_json()["user"]["email"] == "amina@example.com"
    assert client.get("/api/auth/me").status_code == 200


def test_password_is_stored_hashed():
    user = make_user("public")
    assert user.password_hash != PASSWORD and len(user.password_hash) > 50


@pytest.mark.parametrize("body, status", [
    ({"name": "", "email": "a@example.com", "password": PASSWORD}, 400),
    ({"name": "A", "email": "not-an-email", "password": PASSWORD}, 400),
    ({"name": "A", "email": "a@example.com", "password": "short"}, 400),
    ({"name": "A" * 151, "email": "a@example.com", "password": PASSWORD}, 400),
    ({"name": "A", "email": "a@example.com", "password": PASSWORD, "phone": "0" * 21}, 400),
])
def test_register_validates_input(client, body, status):
    assert client.post("/api/auth/register", json=body).status_code == status


def test_duplicate_email_is_rejected_case_insensitively(client):
    make_user("public", "dup@example.com")
    response = client.post("/api/auth/register", json={"name": "B", "email": "DUP@example.com", "password": PASSWORD})
    assert response.status_code == 409


def test_login_logout_and_me(client):
    make_user("responder")
    assert login(client, "responder@example.com", "wrong-password").status_code == 401
    assert login(client, "nobody@example.com").status_code == 401
    assert login(client, "RESPONDER@example.com").status_code == 200
    assert client.get("/api/auth/me").get_json()["user"]["role"] == "responder"
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_roles_required_returns_401_403_200(client):
    assert client.get("/api/test/staff-only").status_code == 401
    make_user("public")
    login(client, "public@example.com")
    assert client.get("/api/test/staff-only").status_code == 403
    for role in STAFF_ROLES:
        make_user(role)
        login(client, f"{role}@example.com")
        assert client.get("/api/test/staff-only").status_code == 200, role


def test_cli_create_and_list_users(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["users", "create", "--name", "Admin", "--email", "admin@example.com",
                                 "--role", "administrator", "--password", PASSWORD])
    assert result.exit_code == 0, result.output
    assert "Created administrator account" in result.output
    result = runner.invoke(args=["users", "list"])
    assert "administrator" in result.output and "admin@example.com" in result.output
    result = runner.invoke(args=["users", "create", "--name", "Admin", "--email", "admin@example.com",
                                 "--role", "administrator", "--password", PASSWORD])
    assert result.exit_code != 0 and "already exists" in result.output
