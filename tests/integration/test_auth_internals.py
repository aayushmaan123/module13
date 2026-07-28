"""
Tests for the authentication internals: token creation and decoding
(app/auth/jwt.py), the Redis blacklist helpers (app/auth/redis.py), and the
dependency branches not already covered by test_dependencies.py.

The happy paths are covered by the API tests; this module concentrates on the
error branches, which are hard to reach through HTTP.
"""
import asyncio
import concurrent.futures
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from jose import jwt as jose_jwt

from app.auth import jwt as jwt_module
from app.auth import redis as redis_module
from app.auth.dependencies import get_current_user
from app.auth.jwt import create_token, decode_token, get_password_hash, verify_password
from app.core.config import get_settings
from app.models.user import User
from app.schemas.token import TokenType

settings = get_settings()


def run(coro):
    """
    Run a coroutine from a synchronous test.

    The coroutine is executed on a worker thread with its own event loop:
    Playwright's session-scoped sync API keeps a loop running on the main
    thread, and asyncio.run() refuses to start a second one there.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


@pytest.fixture(autouse=True)
def allow_every_token(monkeypatch):
    """
    decode_token consults Redis for revoked tokens. No Redis server runs during
    the tests, so the default here is 'not blacklisted'. Individual tests
    override this when they need the revoked branch.
    """
    async def not_blacklisted(jti):
        return False

    monkeypatch.setattr(jwt_module, "is_blacklisted", not_blacklisted)


@pytest.fixture
def stored_user(db_session):
    """A persisted, active user."""
    suffix = uuid.uuid4().hex[:10]
    user = User(
        first_name="Jwt",
        last_name="Tester",
        email=f"jwt_{suffix}@example.com",
        username=f"jwt_{suffix}",
        password=get_password_hash("SecurePass123!"),
        is_active=True,
        is_verified=False,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
def test_password_hash_round_trip():
    hashed = get_password_hash("SecurePass123!")

    assert hashed != "SecurePass123!"
    assert verify_password("SecurePass123!", hashed) is True
    assert verify_password("WrongPass123!", hashed) is False


# ---------------------------------------------------------------------------
# create_token
# ---------------------------------------------------------------------------
def test_create_token_accepts_a_uuid_subject():
    user_id = uuid.uuid4()

    token = create_token(user_id, TokenType.ACCESS)

    payload = jose_jwt.decode(
        token, settings.JWT_SECRET_KEY, algorithms=[settings.ALGORITHM]
    )
    assert payload["sub"] == str(user_id)
    assert payload["type"] == "access"


def test_create_token_honours_an_explicit_expiry():
    token = create_token(
        str(uuid.uuid4()), TokenType.ACCESS, expires_delta=timedelta(minutes=5)
    )

    payload = jose_jwt.decode(
        token, settings.JWT_SECRET_KEY, algorithms=[settings.ALGORITHM]
    )
    expires_at = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    assert expires_at - datetime.now(timezone.utc) < timedelta(minutes=6)


def test_refresh_token_is_signed_with_the_refresh_secret():
    token = create_token(str(uuid.uuid4()), TokenType.REFRESH)

    payload = jose_jwt.decode(
        token, settings.JWT_REFRESH_SECRET_KEY, algorithms=[settings.ALGORITHM]
    )
    assert payload["type"] == "refresh"


def test_create_token_wraps_encoding_failures_in_a_500(monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("signing backend unavailable")

    monkeypatch.setattr(jwt_module.jwt, "encode", explode)

    with pytest.raises(HTTPException) as exc_info:
        create_token(str(uuid.uuid4()), TokenType.ACCESS)

    assert exc_info.value.status_code == 500
    assert "Could not create token" in exc_info.value.detail


# ---------------------------------------------------------------------------
# decode_token
# ---------------------------------------------------------------------------
def test_decode_token_returns_the_payload():
    user_id = str(uuid.uuid4())
    token = create_token(user_id, TokenType.ACCESS)

    payload = run(decode_token(token, TokenType.ACCESS))

    assert payload["sub"] == user_id
    assert payload["type"] == "access"
    assert "jti" in payload


def test_decode_token_rejects_the_wrong_token_type():
    """Claims signed with the refresh secret but still carrying type=access."""
    token = create_token(str(uuid.uuid4()), TokenType.ACCESS)
    claims = jose_jwt.decode(
        token, settings.JWT_SECRET_KEY, algorithms=[settings.ALGORITHM]
    )
    refresh_signed = jose_jwt.encode(
        claims, settings.JWT_REFRESH_SECRET_KEY, algorithm=settings.ALGORITHM
    )

    with pytest.raises(HTTPException) as exc_info:
        run(decode_token(refresh_signed, TokenType.REFRESH))

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid token type"


def test_decode_token_rejects_a_revoked_token(monkeypatch):
    async def always_blacklisted(jti):
        return True

    monkeypatch.setattr(jwt_module, "is_blacklisted", always_blacklisted)
    token = create_token(str(uuid.uuid4()), TokenType.ACCESS)

    with pytest.raises(HTTPException) as exc_info:
        run(decode_token(token, TokenType.ACCESS))

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Token has been revoked"


def test_decode_token_rejects_an_expired_token():
    token = create_token(
        str(uuid.uuid4()), TokenType.ACCESS, expires_delta=timedelta(minutes=-5)
    )

    with pytest.raises(HTTPException) as exc_info:
        run(decode_token(token, TokenType.ACCESS))

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Token has expired"


def test_decode_token_can_skip_expiry_verification():
    user_id = str(uuid.uuid4())
    token = create_token(
        user_id, TokenType.ACCESS, expires_delta=timedelta(minutes=-5)
    )

    payload = run(decode_token(token, TokenType.ACCESS, verify_exp=False))

    assert payload["sub"] == user_id


def test_decode_token_rejects_a_malformed_token():
    with pytest.raises(HTTPException) as exc_info:
        run(decode_token("not-a-jwt", TokenType.ACCESS))

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Could not validate credentials"


# ---------------------------------------------------------------------------
# get_current_user (app/auth/jwt.py - the database-backed variant)
# ---------------------------------------------------------------------------
def test_get_current_user_returns_the_stored_user(db_session, stored_user):
    token = create_token(str(stored_user.id), TokenType.ACCESS)

    user = run(jwt_module.get_current_user(token=token, db=db_session))

    assert user.id == stored_user.id
    assert user.username == stored_user.username


def test_get_current_user_rejects_an_unknown_subject(db_session):
    token = create_token(str(uuid.uuid4()), TokenType.ACCESS)

    with pytest.raises(HTTPException) as exc_info:
        run(jwt_module.get_current_user(token=token, db=db_session))

    assert exc_info.value.status_code == 401
    assert "User not found" in exc_info.value.detail


def test_get_current_user_rejects_an_inactive_user(db_session, stored_user):
    stored_user.is_active = False
    db_session.commit()
    token = create_token(str(stored_user.id), TokenType.ACCESS)

    with pytest.raises(HTTPException) as exc_info:
        run(jwt_module.get_current_user(token=token, db=db_session))

    assert exc_info.value.status_code == 401
    assert "Inactive user" in exc_info.value.detail


def test_get_current_user_rejects_a_malformed_token(db_session):
    with pytest.raises(HTTPException) as exc_info:
        run(jwt_module.get_current_user(token="not-a-jwt", db=db_session))

    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Redis blacklist helpers (app/auth/redis.py)
# ---------------------------------------------------------------------------
class FakeRedis:
    """Minimal async stand-in for the redis client."""

    def __init__(self):
        self.store = {}
        self.url = None

    async def set(self, key, value, ex=None):
        self.store[key] = (value, ex)

    async def exists(self, key):
        return 1 if key in self.store else 0


@pytest.fixture
def fake_redis(monkeypatch):
    """Replace redis.asyncio.from_url and clear the cached connection."""
    client = FakeRedis()

    async def from_url(url):
        client.url = url
        return client

    monkeypatch.setattr(redis_module.aioredis, "from_url", from_url)
    if hasattr(redis_module.get_redis, "redis"):
        del redis_module.get_redis.redis
    yield client
    if hasattr(redis_module.get_redis, "redis"):
        del redis_module.get_redis.redis


def test_get_redis_reuses_one_connection(fake_redis):
    first = run(redis_module.get_redis())
    second = run(redis_module.get_redis())

    assert first is second is fake_redis
    assert fake_redis.url == settings.REDIS_URL


def test_blacklist_round_trip(fake_redis):
    assert run(redis_module.is_blacklisted("token-jti")) == 0

    run(redis_module.add_to_blacklist("token-jti", 900))

    assert run(redis_module.is_blacklisted("token-jti")) == 1
    assert fake_redis.store["blacklist:token-jti"] == ("1", 900)


# ---------------------------------------------------------------------------
# Dependency payload shapes not covered by test_dependencies.py
# ---------------------------------------------------------------------------
def test_dependency_accepts_a_minimal_dict_payload(monkeypatch):
    user_id = str(uuid.uuid4())
    monkeypatch.setattr(
        User, "verify_token", classmethod(lambda cls, token: {"sub": user_id})
    )

    user = get_current_user(token="any-token")

    assert str(user.id) == user_id
    assert user.username == "unknown"
    assert user.is_active is True


def test_dependency_accepts_a_uuid_payload(monkeypatch):
    user_id = uuid.uuid4()
    monkeypatch.setattr(User, "verify_token", classmethod(lambda cls, token: user_id))

    user = get_current_user(token="any-token")

    assert user.id == user_id
    assert user.username == "unknown"
    assert user.email == "unknown@example.com"


def test_dependency_rejects_a_dict_without_a_subject(monkeypatch):
    monkeypatch.setattr(
        User, "verify_token", classmethod(lambda cls, token: {"unexpected": "shape"})
    )

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(token="any-token")

    assert exc_info.value.status_code == 401


def test_dependency_rejects_an_unrecognised_payload_type(monkeypatch):
    monkeypatch.setattr(
        User, "verify_token", classmethod(lambda cls, token: ["not", "supported"])
    )

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(token="any-token")

    assert exc_info.value.status_code == 401
