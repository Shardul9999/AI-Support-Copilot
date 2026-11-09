import os
from dotenv import load_dotenv

load_dotenv()

import pytest
from starlette.testclient import TestClient
from app.main import app
from app.middleware.auth import sign_token

@pytest.fixture(scope="module")
def client():
    return TestClient(app)

def test_healthz_endpoint(client):
    """Liveness probe must be accessible without any authentication."""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

def test_readyz_endpoint(client):
    """Readiness probe verifies live DB pool and Redis connectivity without auth."""
    resp = client.get("/readyz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["database"] == "ok"
    assert data["redis"] == "ok"

def test_unauthenticated_request_rejected(client):
    """Requests with no authentication credentials or tenant headers must be rejected with 401."""
    resp = client.get("/tickets")
    assert resp.status_code == 401
    assert "Missing or invalid tenant credentials" in resp.json()["detail"]

def test_bearer_token_authenticated(client):
    """Valid HMAC-SHA256 Bearer token authenticates tenant."""
    valid_token = sign_token("acme")
    resp = client.get("/tickets", headers={"Authorization": f"Bearer {valid_token}"})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

def test_tampered_bearer_token_rejected(client):
    """Tampered token with invalid signature must be rejected."""
    tampered_token = "acme.invalid_tampered_signature"
    resp = client.get("/tickets", headers={"Authorization": f"Bearer {tampered_token}"})
    assert resp.status_code == 401

def test_api_key_authenticated(client):
    """Valid X-API-Key authenticates tenant."""
    resp = client.get("/tickets", headers={"X-API-Key": "test-key-acme"})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

def test_x_tenant_id_header(client):
    """Explicit X-Tenant-ID works for dev / backward compatibility."""
    resp = client.get("/tickets", headers={"X-Tenant-ID": "acme"})
    assert resp.status_code == 200
    if "X-RateLimit-Limit" in resp.headers:
        assert int(resp.headers["X-RateLimit-Limit"]) > 0
