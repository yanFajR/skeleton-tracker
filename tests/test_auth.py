from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import auth
from app import app

CONFIGURED_PASSWORD = os.environ.get("MOTION_TRACKER_PASSWORD", auth.DEFAULT_PASSWORD)


@pytest.fixture
def client(tmp_path: Path):
    original_directory = app.state.upload_directory
    original_output_directory = app.state.output_directory
    original_metadata_file = app.state.metadata_file
    original_session_file = app.state.session_file
    original_auth_secret_file = app.state.auth_secret_file
    app.state.upload_directory = tmp_path / "uploads"
    app.state.output_directory = tmp_path / "outputs"
    app.state.metadata_file = tmp_path / "data" / "comparisons.json"
    app.state.session_file = tmp_path / "data" / "sessions.json"
    app.state.auth_secret_file = tmp_path / "data" / ".auth_secret"
    with TestClient(app) as test_client:
        yield test_client
    app.state.upload_directory = original_directory
    app.state.output_directory = original_output_directory
    app.state.metadata_file = original_metadata_file
    app.state.session_file = original_session_file
    app.state.auth_secret_file = original_auth_secret_file


def attempt_login(
    client: TestClient,
    username: str | None = None,
    password: str | None = None,
    next_url: str = "/",
):
    return client.post(
        "/login",
        data={
            "username": app.state.auth_username if username is None else username,
            "password": CONFIGURED_PASSWORD if password is None else password,
            "next": next_url,
        },
        follow_redirects=False,
    )


def test_login_page_is_public(client: TestClient) -> None:
    response = client.get("/login")

    assert response.status_code == 200
    assert "Motion Tracker" in response.text
    assert 'id="login-username"' in response.text
    assert 'id="login-password"' in response.text
    assert 'action="/login"' in response.text


def test_protected_page_redirects_to_login_with_next_target(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2F"


def test_protected_api_returns_unauthorized(client: TestClient) -> None:
    assert client.get(f"/api/sessions/{'a' * 32}").status_code == 401
    assert client.post(f"/api/videos/{'a' * 32}.webm/pose").status_code == 401
    assert client.get(f"/videos/{'a' * 32}.webm").status_code == 401


def test_valid_credentials_create_httponly_session_cookie(client: TestClient) -> None:
    response = attempt_login(client)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("motion_tracker_session=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert f"Max-Age={auth.SESSION_TTL_SECONDS}" in cookie
    assert "Path=/" in cookie
    assert client.get("/").status_code == 200


def test_invalid_password_is_rejected_with_generic_message(client: TestClient) -> None:
    response = attempt_login(client, password="salah-sekali")

    assert response.status_code == 401
    assert "Nama pengguna atau kata sandi salah." in response.text
    assert "motion_tracker_session=" not in response.headers.get("set-cookie", "")
    assert client.get("/", follow_redirects=False).status_code == 303


@pytest.mark.parametrize(
    "payload",
    [
        "admin' OR '1'='1' --",
        "admin'--",
        "' OR 1=1 --",
        "admin' UNION SELECT 'admin' --",
    ],
)
def test_sql_injection_payloads_cannot_authenticate(client: TestClient, payload: str) -> None:
    username_injection = attempt_login(client, username=payload)
    password_injection = attempt_login(client, password=payload)

    assert username_injection.status_code == 401
    assert password_injection.status_code == 401
    assert client.get("/", follow_redirects=False).status_code == 303


def test_tampered_session_cookie_is_rejected(client: TestClient) -> None:
    attempt_login(client)

    response = client.get(
        "/",
        headers={"cookie": f"{'motion_tracker_session'}=forged-token"},
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_session_cookie_from_another_secret_is_rejected(client: TestClient) -> None:
    forged = auth.create_session_token(app.state.auth_username, b"attacker-secret-value")
    response = client.get(
        "/",
        headers={"cookie": f"motion_tracker_session={forged}"},
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_authenticated_login_page_redirects_to_target(client: TestClient) -> None:
    attempt_login(client, next_url="/?comparison_id=abc")

    response = client.get("/login?next=%2F%3Fcomparison_id%3Dabc", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/?comparison_id=abc"


def test_open_redirect_target_is_ignored(client: TestClient) -> None:
    response = attempt_login(client, next_url="https://evil.example/steal")

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_logout_clears_the_session(client: TestClient) -> None:
    attempt_login(client)

    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert "motion_tracker_session=" in response.headers["set-cookie"]
    assert client.get("/", follow_redirects=False).status_code == 303


def test_login_is_rate_limited_after_repeated_failures(client: TestClient) -> None:
    for _ in range(app_module_failure_limit()):
        assert attempt_login(client, password="salah").status_code == 401

    blocked = attempt_login(client)

    assert blocked.status_code == 429
    assert "Terlalu banyak percobaan" in blocked.text


def app_module_failure_limit() -> int:
    from app import LOGIN_MAX_FAILURES

    return LOGIN_MAX_FAILURES


def test_password_hashes_are_salted_and_verifiable() -> None:
    first = auth.hash_password("AdminAman123", iterations=1000)
    second = auth.hash_password("AdminAman123", iterations=1000)

    assert first != second
    assert auth.verify_password("AdminAman123", first)
    assert auth.verify_password("AdminAman123", second)
    assert not auth.verify_password("adminaman123", first)
    assert not auth.verify_password("", first)
    assert not auth.verify_password("AdminAman123", "not-a-valid-hash")


def test_session_tokens_reject_expiry_and_foreign_secrets() -> None:
    secret = b"tests-only-secret-key-value-32bytes"
    issued_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    token = auth.create_session_token(
        "admin", secret, ttl_seconds=60, now=issued_at
    )

    assert auth.verify_session_token(token, secret, now=issued_at) == "admin"
    assert (
        auth.verify_session_token(
            token, secret, now=issued_at + timedelta(seconds=61)
        )
        is None
    )
    assert auth.verify_session_token(token, b"another-secret") is None
    assert auth.verify_session_token("garbage", secret) is None
