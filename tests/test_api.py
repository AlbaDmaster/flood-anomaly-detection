"""
Tests for the REST API. They use the separate test database: each test
starts from the 2018 Tana River flood replay (which raises a known HIGH
alert on 2018-05-07) plus one public, one responder and one administrator
account.
"""
import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from app import create_app, ingestion, scoring
from app.auth import create_user
from app.config import database_url
from app.extensions import db
from app.models import Alert, Feedback, User

TEST_DB = "flood_anomaly_detection_test"
PASSWORD = "correct-horse-1"


@pytest.fixture(scope="module")
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": database_url(TEST_DB)})
    with app.app_context():
        try:
            db.session.execute(db.text("SELECT COUNT(*) FROM REGION"))
        except (OperationalError, ProgrammingError) as exc:
            pytest.skip(f"Test database not ready - run the one-time test database setup: {exc.orig}")
    return app


@pytest.fixture(autouse=True)
def seeded(app):
    with app.app_context():
        assert db.engine.url.database == TEST_DB  # never wipe the real database
        ingestion.reset_readings()
        db.session.execute(db.delete(User))
        db.session.commit()
        ingestion.replay(["tana_river_hola"], "2018-05-01", "2018-05-08", history_days=10,
                         echo=lambda *_: None, after_day=lambda: scoring.score_pending(["tana_river_hola"]))
        for role in ("public", "responder", "administrator"):
            create_user(role.title(), f"{role}@example.com", PASSWORD, role=role)
    yield  # outside the app context, so every test request gets a fresh one like in production


@pytest.fixture
def client(app):
    return app.test_client()


def as_role(client, role):
    response = client.post("/api/auth/login", json={"email": f"{role}@example.com", "password": PASSWORD})
    assert response.status_code == 200
    return client


def high_alert_id(client):
    alerts = client.get("/api/alerts?severity=high").get_json()["alerts"]
    return next(a["alert_id"] for a in alerts if a["date"] == "2018-05-07")


@pytest.mark.parametrize("method, path", [
    ("get", "/api/regions"), ("get", "/api/regions/nairobi/readings"), ("get", "/api/alerts"),
    ("get", "/api/alerts/1"), ("post", "/api/alerts/1/acknowledge"), ("post", "/api/alerts/1/feedback"),
    ("get", "/api/admin/users"), ("post", "/api/admin/users"), ("patch", "/api/admin/users/1"),
])
def test_everything_needs_login(client, method, path):
    assert getattr(client, method)(path).status_code == 401


def test_health_stays_open(client):
    assert client.get("/api/health").status_code == 200


def test_regions_show_latest_scored_reading_and_open_alerts(client):
    regions = {r["slug"]: r for r in as_role(client, "public").get("/api/regions").get_json()["regions"]}
    assert set(regions) == {"nairobi", "kisumu", "tana_river_hola"}
    tana = regions["tana_river_hola"]
    assert tana["latest_reading"]["date"] == "2018-05-08"
    assert tana["latest_scored"]["severity"] in {"normal", "moderate", "high"}
    assert tana["open_alerts"] > 0
    assert regions["nairobi"]["latest_reading"] is None and regions["nairobi"]["open_alerts"] == 0


def test_readings_include_scores_and_validate_dates(client):
    as_role(client, "public")
    body = client.get("/api/regions/tana_river_hola/readings?start=2018-05-01&end=2018-05-08").get_json()
    assert [r["date"] for r in body["readings"]][0] == "2018-05-01" and len(body["readings"]) == 8
    assert body["thresholds"]["high"] > body["thresholds"]["moderate"] > 0
    peak = next(r for r in body["readings"] if r["date"] == "2018-05-07")
    assert peak["severity"] == "high" and peak["river_discharge"] > 1000
    default = client.get("/api/regions/tana_river_hola/readings").get_json()
    assert default["end"] == "2018-05-08" and len(default["readings"]) == 18
    assert client.get("/api/regions/mombasa/readings").status_code == 404
    assert client.get("/api/regions/tana_river_hola/readings?start=yesterday").status_code == 400
    assert client.get("/api/regions/tana_river_hola/readings?start=2018-05-08&end=2018-05-01").status_code == 400
    assert client.get("/api/regions/tana_river_hola/readings?start=2015-01-01&end=2018-01-01").status_code == 400
    assert client.get("/api/regions/tana_river_hola/readings?end=0001-01-01").status_code == 400


def test_alert_list_filters_and_pagination(client):
    as_role(client, "public")
    everything = client.get("/api/alerts").get_json()
    assert everything["total"] > 2
    dates = [a["date"] for a in everything["alerts"]]
    assert dates == sorted(dates, reverse=True)
    assert all(a["severity"] == "high" for a in client.get("/api/alerts?severity=high").get_json()["alerts"])
    page = client.get("/api/alerts?per_page=2&page=1").get_json()
    assert len(page["alerts"]) == 2 and page["total"] == everything["total"]
    assert client.get("/api/alerts?region=nairobi").get_json()["total"] == 0
    assert client.get("/api/alerts?severity=extreme").status_code == 400
    assert client.get("/api/alerts?status=closed").status_code == 400
    assert client.get("/api/alerts?page=two").status_code == 400
    assert client.get("/api/alerts?page=99999999999999999999999").status_code == 200
    assert client.get("/api/alerts?region=mombasa").status_code == 404
    assert client.get("/api/alerts/999999").status_code == 404


def test_public_cannot_acknowledge_or_give_feedback(client):
    as_role(client, "public")
    alert_id = high_alert_id(client)
    assert client.post(f"/api/alerts/{alert_id}/acknowledge").status_code == 403
    assert client.post(f"/api/alerts/{alert_id}/feedback", json={"is_confirmed": True}).status_code == 403


def test_responder_acknowledges_and_gives_feedback(client, app):
    as_role(client, "responder")
    alert_id = high_alert_id(client)
    response = client.post(f"/api/alerts/{alert_id}/acknowledge")
    assert response.status_code == 200 and response.get_json()["alert"]["status"] == "acknowledged"
    assert client.get("/api/alerts?status=acknowledged").get_json()["total"] == 1

    assert client.post(f"/api/alerts/{alert_id}/feedback", json={"comment": "no verdict"}).status_code == 400
    assert client.post(f"/api/alerts/{alert_id}/feedback", json=[1, 2]).status_code == 400
    assert client.post(f"/api/alerts/{alert_id}/feedback", json={"is_confirmed": True, "comment": {"a": 1}}).status_code == 400
    response = client.post(f"/api/alerts/{alert_id}/feedback",
                           json={"is_confirmed": True, "comment": "River burst its banks near Hola"})
    assert response.status_code == 201
    detail = client.get(f"/api/alerts/{alert_id}").get_json()["alert"]
    assert detail["feedback_count"] == 1
    assert detail["feedback"][0]["is_confirmed"] is True and detail["feedback"][0]["user"] == "Responder"
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Feedback)) == 1
        assert db.session.get(Alert, alert_id).status == "acknowledged"


def test_only_administrators_manage_users(client):
    as_role(client, "responder")
    assert client.get("/api/admin/users").status_code == 403
    as_role(client, "administrator")
    users = {u["email"]: u for u in client.get("/api/admin/users").get_json()["users"]}
    assert set(users) == {"public@example.com", "responder@example.com", "administrator@example.com"}

    public_id = users["public@example.com"]["user_id"]
    response = client.patch(f"/api/admin/users/{public_id}", json={"role": "authority"})
    assert response.status_code == 200 and response.get_json()["user"]["role"] == "authority"
    assert client.patch(f"/api/admin/users/{public_id}", json={"role": "king"}).status_code == 400
    assert client.patch(f"/api/admin/users/{public_id}", json=["authority"]).status_code == 400
    assert client.post("/api/admin/users", json="not an object").status_code == 400
    assert client.patch("/api/admin/users/999999", json={"role": "public"}).status_code == 404
    own_id = users["administrator@example.com"]["user_id"]
    assert client.patch(f"/api/admin/users/{own_id}", json={"role": "public"}).status_code == 400

    response = client.post("/api/admin/users", json={
        "name": "County Officer", "email": "officer@example.com", "password": PASSWORD, "role": "authority"})
    assert response.status_code == 201 and response.get_json()["user"]["role"] == "authority"
    again = client.post("/api/admin/users", json={
        "name": "County Officer", "email": "officer@example.com", "password": PASSWORD, "role": "authority"})
    assert again.status_code == 409


def test_unknown_api_paths_return_json(client):
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404 and response.get_json()["error"] == "not found"


def test_admin_edits_and_deactivates_users(client):
    as_role(client, "administrator")
    users = {u["email"]: u for u in client.get("/api/admin/users").get_json()["users"]}
    assert all(u["is_active"] for u in users.values())
    responder_id = users["responder@example.com"]["user_id"]

    response = client.patch(f"/api/admin/users/{responder_id}", json={"name": "  Field Team A ", "phone": "0712345678"})
    assert response.status_code == 200
    assert response.get_json()["user"]["name"] == "Field Team A" and response.get_json()["user"]["phone"] == "0712345678"
    assert client.patch(f"/api/admin/users/{responder_id}", json={"name": ""}).status_code == 400
    assert client.patch(f"/api/admin/users/{responder_id}", json={"phone": "0" * 21}).status_code == 400
    assert client.patch(f"/api/admin/users/{responder_id}", json={"is_active": "no"}).status_code == 400
    assert client.patch(f"/api/admin/users/{responder_id}", json={}).status_code == 400

    own_id = users["administrator@example.com"]["user_id"]
    assert client.patch(f"/api/admin/users/{own_id}", json={"is_active": False}).status_code == 400
    assert client.patch(f"/api/admin/users/{own_id}", json={"name": "Chief Admin"}).status_code == 200

    response = client.patch(f"/api/admin/users/{responder_id}", json={"is_active": False})
    assert response.status_code == 200 and response.get_json()["user"]["is_active"] is False


def test_deactivated_user_cannot_sign_in_and_is_signed_out(app):
    responder, admin = app.test_client(), app.test_client()
    login = {"email": "responder@example.com", "password": PASSWORD}
    assert responder.post("/api/auth/login", json=login).status_code == 200
    assert responder.get("/api/auth/me").status_code == 200
    as_role(admin, "administrator")
    responder_id = next(u["user_id"] for u in admin.get("/api/admin/users").get_json()["users"]
                        if u["email"] == "responder@example.com")
    assert admin.patch(f"/api/admin/users/{responder_id}", json={"is_active": False}).status_code == 200

    assert responder.get("/api/auth/me").status_code == 401          # existing session ends
    again = responder.post("/api/auth/login", json=login)
    assert again.status_code == 403 and "deactivated" in again.get_json()["error"]

    admin.patch(f"/api/admin/users/{responder_id}", json={"is_active": True})
    assert responder.post("/api/auth/login", json=login).status_code == 200
