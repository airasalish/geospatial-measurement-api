"""Authentication and tenancy.

Off by default (``GEO_AUTH_REQUIRED=false``): the demo and the docker-compose
stack stay one-click and everything is anonymous. When on:

* passwords are hashed with scrypt (stdlib; memory-hard, salted per user);
* the API issues HS256 JWTs (``sub`` = user id, ``exp``) via ``POST /api/auth/token``;
* every file and DEM is owned by a user, and reads, analyses and tiles are scoped
  to the owner. Another user's id gives 404, not 403, so ids can't be probed.

Map clients can't add headers to tile and download requests, so GET endpoints
also accept ``?access_token=``.
"""

import base64
import hashlib
import hmac
import os
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import GeoAPIError
from app.models import User

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
_bearer = HTTPBearer(auto_error=False)


class Unauthorized(GeoAPIError):
    status_code = 401
    code = "unauthorized"


class Conflict(GeoAPIError):
    status_code = 409
    code = "conflict"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    b64 = base64.b64encode
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${b64(salt).decode()}${b64(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    expected = base64.b64decode(digest)
    actual = hashlib.scrypt(
        password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected)
    )
    return hmac.compare_digest(actual, expected)


def create_token(settings: Settings, user: User) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": user.id,
        "email": user.email,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_ttl_minutes),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def _decode(settings: Settings, token: str) -> str:
    try:
        claims = jwt.decode(
            token, settings.jwt_secret, algorithms=["HS256"], options={"require": ["exp", "sub"]}
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Token expired; sign in again.") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("Invalid token.") from exc
    return str(claims["sub"])


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _session(request: Request):  # type: ignore[no-untyped-def]
    yield from request.app.state.db.session()


def optional_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[Session, Depends(_session)],
    access_token: Annotated[str | None, Query(include_in_schema=False)] = None,
) -> User | None:
    token = credentials.credentials if credentials else access_token
    if not token:
        return None
    user = session.get(User, _decode(_settings(request), token))
    if user is None:
        raise Unauthorized("Account no longer exists.")
    return user


def current_user(request: Request, user: Annotated[User | None, Depends(optional_user)]) -> User | None:
    """The signed-in user, or None in anonymous mode. 401 when auth is required and missing."""
    if _settings(request).auth_required and user is None:
        raise Unauthorized("Sign in required.")
    return user if _settings(request).auth_required else None


def require_user(user: Annotated[User | None, Depends(optional_user)]) -> User:
    if user is None:
        raise Unauthorized("Sign in required.")
    return user


UserDep = Annotated[User | None, Depends(current_user)]
RequiredUserDep = Annotated[User, Depends(require_user)]


def owner_id(user: User | None) -> str | None:
    return user.id if user else None
