"""Passwords, single-use tokens, CSRF protection and login throttling."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy.orm import Session

from .models import Token, User, utcnow

_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(user: User, password: str) -> bool:
    if not user.password_hash:
        return False
    try:
        return _hasher.verify(user.password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def check_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"The password must have at least {MIN_PASSWORD_LENGTH} characters.")


# ---------------------------------------------------------------------------
# Single-use tokens (invitation, password reset)
# ---------------------------------------------------------------------------

def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_token(db: Session, user: User, purpose: str, valid_hours: int) -> str:
    """Returns the clear token (to put in an e-mail link); only its hash is stored."""
    token = secrets.token_urlsafe(32)
    db.add(Token(token_hash=_digest(token), user_id=user.id, purpose=purpose,
                 expires_at=utcnow() + timedelta(hours=valid_hours)))
    return token


def use_token(db: Session, token: str, purpose: str) -> User | None:
    """Validates a token and marks it used. Returns its user, or None."""
    row = db.query(Token).filter_by(token_hash=_digest(token or ""), purpose=purpose).first()
    if row is None or row.used_at is not None or row.expires_at < utcnow() or not row.user.active:
        return None
    row.used_at = utcnow()
    return row.user


def peek_token(db: Session, token: str, purpose: str) -> User | None:
    """Like use_token but without consuming it (to display the form)."""
    row = db.query(Token).filter_by(token_hash=_digest(token or ""), purpose=purpose).first()
    if row is None or row.used_at is not None or row.expires_at < utcnow() or not row.user.active:
        return None
    return row.user


# ---------------------------------------------------------------------------
# CSRF: a random token per session, required in every POST form
# ---------------------------------------------------------------------------

def csrf_token(session: dict) -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def csrf_valid(session: dict, submitted: str | None) -> bool:
    expected = session.get("csrf")
    return bool(expected and submitted and hmac.compare_digest(expected, submitted))


# ---------------------------------------------------------------------------
# Login throttling (in memory, per e-mail and per client address)
# ---------------------------------------------------------------------------

class LoginThrottle:
    """Allows `limit` failed attempts per key in a sliding window of `window` seconds."""

    def __init__(self, limit: int = 8, window: int = 900):
        self.limit, self.window = limit, window
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, *keys: str) -> bool:
        now = time.monotonic()
        with self._lock:
            for key in keys:
                recent = [t for t in self._fails.get(key, []) if now - t < self.window]
                self._fails[key] = recent
                if len(recent) >= self.limit:
                    return True
        return False

    def fail(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._fails.setdefault(key, []).append(time.monotonic())

    def reset(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._fails.pop(key, None)


throttle = LoginThrottle()
