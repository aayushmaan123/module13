"""
In-process API tests for app/main.py using FastAPI's TestClient.

The e2e suite exercises these same routes, but it does so against a uvicorn
subprocess, which pytest-cov cannot instrument. Running the app in-process
covers the route handlers - including the error branches that are awkward to
trigger through a browser.
"""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.user import User

VALID_PASSWORD = "SecurePass123!"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def make_user_payload() -> dict:
    suffix = uuid4().hex[:10]
    return {
        "first_name": "Api",
        "last_name": "Tester",
        "email": f"api_{suffix}@example.com",
        "username": f"api_{suffix}",
        "password": VALID_PASSWORD,
        "confirm_password": VALID_PASSWORD,
    }


def register_user(client: TestClient) -> dict:
    payload = make_user_payload()
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 201, response.text
    return payload


def auth_headers(client: TestClient) -> dict:
    payload = register_user(client)
    response = client.post(
        "/auth/login",
        json={"username": payload["username"], "password": payload["password"]},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# ---------------------------------------------------------------------------
# Web pages and health
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/", "/login", "/register", "/dashboard"])
def test_web_pages_render(client: TestClient, path: str):
    response = client.get(path)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_health_endpoint(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def test_register_returns_created_user(client: TestClient):
    payload = make_user_payload()
    response = client.post("/auth/register", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert data["username"] == payload["username"]
    assert data["email"] == payload["email"]
    assert data["is_active"] is True
    assert data["is_verified"] is False
    assert "password" not in data


def test_register_duplicate_returns_400(client: TestClient):
    payload = register_user(client)

    response = client.post("/auth/register", json=payload)

    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


def test_register_weak_password_returns_422(client: TestClient):
    payload = make_user_payload()
    payload["password"] = payload["confirm_password"] = "weakpass"

    response = client.post("/auth/register", json=payload)

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Login (JSON)
# ---------------------------------------------------------------------------
def test_login_returns_token_payload(client: TestClient):
    payload = register_user(client)

    response = client.post(
        "/auth/login",
        json={"username": payload["username"], "password": payload["password"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["token_type"] == "bearer"
    assert data["access_token"].count(".") == 2
    assert data["refresh_token"].count(".") == 2
    assert data["username"] == payload["username"]
    assert data["is_active"] is True


def test_login_wrong_password_returns_401(client: TestClient):
    payload = register_user(client)

    response = client.post(
        "/auth/login",
        json={"username": payload["username"], "password": "WrongPass123!"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"


def test_login_normalizes_naive_expiry(client: TestClient, monkeypatch):
    """
    A naive expires_at from authenticate() is given a UTC timezone rather than
    being replaced with the default window.
    """
    payload = register_user(client)
    naive_expiry = datetime.utcnow() + timedelta(minutes=42)
    original_authenticate = User.authenticate.__func__

    def authenticate_with_naive_expiry(cls, db, username_or_email, password):
        result = original_authenticate(cls, db, username_or_email, password)
        if result is not None:
            result["expires_at"] = naive_expiry
        return result

    monkeypatch.setattr(
        User, "authenticate", classmethod(authenticate_with_naive_expiry)
    )

    response = client.post(
        "/auth/login",
        json={"username": payload["username"], "password": payload["password"]},
    )

    assert response.status_code == 200
    assert response.json()["expires_at"].startswith(naive_expiry.isoformat()[:19])


# ---------------------------------------------------------------------------
# Login (form, used by the Swagger UI)
# ---------------------------------------------------------------------------
def test_token_endpoint_returns_bearer_token(client: TestClient):
    payload = register_user(client)

    response = client.post(
        "/auth/token",
        data={"username": payload["username"], "password": payload["password"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["token_type"] == "bearer"
    assert data["access_token"].count(".") == 2


def test_token_endpoint_wrong_password_returns_401(client: TestClient):
    payload = register_user(client)

    response = client.post(
        "/auth/token",
        data={"username": payload["username"], "password": "WrongPass123!"},
    )

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Calculations - create
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "calc_type,inputs,expected",
    [
        ("addition", [10.5, 3, 2], 15.5),
        ("subtraction", [10, 3, 2], 5),
        ("multiplication", [2, 3, 4], 24),
        ("division", [100, 2, 5], 10),
    ],
)
def test_create_calculation(client: TestClient, calc_type, inputs, expected):
    headers = auth_headers(client)

    response = client.post(
        "/calculations",
        json={"type": calc_type, "inputs": inputs, "user_id": "ignored"},
        headers=headers,
    )

    assert response.status_code == 201, response.text
    assert response.json()["result"] == expected


def test_create_calculation_requires_authentication(client: TestClient):
    response = client.post(
        "/calculations", json={"type": "addition", "inputs": [1, 2]}
    )
    assert response.status_code == 401


def test_create_calculation_with_unsupported_type_returns_422(client: TestClient):
    headers = auth_headers(client)

    response = client.post(
        "/calculations",
        json={"type": "modulo", "inputs": [4, 2]},
        headers=headers,
    )

    assert response.status_code == 422


def test_create_calculation_division_by_zero_returns_422(client: TestClient):
    headers = auth_headers(client)

    response = client.post(
        "/calculations",
        json={"type": "division", "inputs": [4, 0]},
        headers=headers,
    )

    assert response.status_code == 422


def test_create_calculation_rejects_value_error_from_model(client: TestClient, monkeypatch):
    """A ValueError raised while building the calculation becomes a 400."""
    headers = auth_headers(client)

    from app.models.calculation import Calculation

    def raise_value_error(*args, **kwargs):
        raise ValueError("Unsupported calculation type: broken")

    monkeypatch.setattr(Calculation, "create", staticmethod(raise_value_error))

    response = client.post(
        "/calculations",
        json={"type": "addition", "inputs": [1, 2]},
        headers=headers,
    )

    assert response.status_code == 400
    assert "Unsupported calculation type" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Calculations - browse, read, edit, delete
# ---------------------------------------------------------------------------
def test_calculation_bread_lifecycle(client: TestClient):
    headers = auth_headers(client)

    created = client.post(
        "/calculations",
        json={"type": "multiplication", "inputs": [3, 4]},
        headers=headers,
    )
    assert created.status_code == 201
    calc_id = created.json()["id"]

    listed = client.get("/calculations", headers=headers)
    assert listed.status_code == 200
    assert any(item["id"] == calc_id for item in listed.json())

    fetched = client.get(f"/calculations/{calc_id}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["result"] == 12

    updated = client.put(
        f"/calculations/{calc_id}", json={"inputs": [5, 6]}, headers=headers
    )
    assert updated.status_code == 200
    assert updated.json()["result"] == 30

    deleted = client.delete(f"/calculations/{calc_id}", headers=headers)
    assert deleted.status_code == 204

    assert client.get(f"/calculations/{calc_id}", headers=headers).status_code == 404


def test_update_without_inputs_leaves_result_unchanged(client: TestClient):
    headers = auth_headers(client)
    created = client.post(
        "/calculations", json={"type": "addition", "inputs": [1, 2]}, headers=headers
    )
    calc_id = created.json()["id"]

    response = client.put(f"/calculations/{calc_id}", json={}, headers=headers)

    assert response.status_code == 200
    assert response.json()["result"] == 3


def test_list_only_returns_the_callers_calculations(client: TestClient):
    owner_headers = auth_headers(client)
    other_headers = auth_headers(client)

    created = client.post(
        "/calculations", json={"type": "addition", "inputs": [7, 8]}, headers=owner_headers
    )
    calc_id = created.json()["id"]

    other_list = client.get("/calculations", headers=other_headers)
    assert all(item["id"] != calc_id for item in other_list.json())

    assert client.get(f"/calculations/{calc_id}", headers=other_headers).status_code == 404


@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_malformed_calculation_id_returns_400(client: TestClient, method: str):
    headers = auth_headers(client)
    kwargs = {"headers": headers}
    if method == "put":
        kwargs["json"] = {"inputs": [1, 2]}

    response = getattr(client, method)("/calculations/not-a-uuid", **kwargs)

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid calculation id format."


@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_unknown_calculation_id_returns_404(client: TestClient, method: str):
    headers = auth_headers(client)
    kwargs = {"headers": headers}
    if method == "put":
        kwargs["json"] = {"inputs": [1, 2]}

    response = getattr(client, method)(f"/calculations/{uuid4()}", **kwargs)

    assert response.status_code == 404
    assert response.json()["detail"] == "Calculation not found."


# ---------------------------------------------------------------------------
# Module entrypoint
# ---------------------------------------------------------------------------
def test_module_runs_uvicorn_when_executed_as_script(monkeypatch):
    """Covers the `if __name__ == "__main__"` block without binding a port."""
    import runpy
    import uvicorn

    called = {}

    def fake_run(app_path, **kwargs):
        called["app"] = app_path
        called["kwargs"] = kwargs

    monkeypatch.setattr(uvicorn, "run", fake_run)
    runpy.run_module("app.main", run_name="__main__")

    assert called["app"] == "app.main:app"
    assert called["kwargs"]["port"] == 8001
