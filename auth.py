"""Single-user authentication helpers.

The application does not use a SQL database; metadata lives in JSON files and is
never interpolated into a query. Passwords are stored as salted PBKDF2-HMAC-SHA256
digests and compared in constant time, and browser sessions use HMAC-signed tokens.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "AdminAman123"
PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16
SESSION_TTL_SECONDS = 8 * 60 * 60


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(
    password: str,
    *,
    salt: bytes | None = None,
    iterations: int = PBKDF2_ITERATIONS,
) -> str:
    """Return a self-describing ``pbkdf2_sha256$iterations$salt$digest`` string."""
    salt = secrets.token_bytes(SALT_BYTES) if salt is None else salt
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join(("pbkdf2_sha256", str(iterations), _b64encode(salt), _b64encode(digest)))


def verify_password(password: str, encoded: str) -> bool:
    """Verify a password against a stored hash without leaking comparison timing."""
    try:
        algorithm, iterations_text, salt_text, digest_text = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        salt = _b64decode(salt_text)
        expected = _b64decode(digest_text)
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations_text)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, expected)


def create_session_token(
    username: str,
    secret: bytes,
    *,
    ttl_seconds: int = SESSION_TTL_SECONDS,
    now: datetime | None = None,
) -> str:
    issued_at = int((now or datetime.now(timezone.utc)).timestamp())
    payload = _b64encode(
        json.dumps(
            {"sub": username, "exp": issued_at + ttl_seconds}, separators=(",", ":")
        ).encode("utf-8")
    )
    signature = _b64encode(hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def verify_session_token(
    token: str, secret: bytes, *, now: datetime | None = None
) -> str | None:
    """Return the token subject when the signature and expiry are valid."""
    try:
        payload, signature = token.split(".", 1)
        expected = hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64decode(signature), expected):
            return None
        claims = json.loads(_b64decode(payload))
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    if not isinstance(claims, dict):
        return None
    username = claims.get("sub")
    expires_at = claims.get("exp")
    if not isinstance(username, str) or not isinstance(expires_at, int):
        return None
    if expires_at <= int((now or datetime.now(timezone.utc)).timestamp()):
        return None
    return username


def load_or_create_secret(path: Path, *, environment_value: str | None = None) -> bytes:
    """Use the configured secret or persist a random one so sessions survive restarts."""
    if environment_value:
        return environment_value.encode("utf-8")
    if path.is_file():
        existing = path.read_bytes()
        if len(existing) >= 32:
            return existing
    secret = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(secret)
    return secret
