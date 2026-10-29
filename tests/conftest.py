"""Test fixtures: a fresh data directory and SQLite database per test."""

from __future__ import annotations

import io
import re

import pytest
from reportlab.pdfgen import canvas


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMULGA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BASE_URL", "http://testserver")
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.org")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-password-123")
    monkeypatch.setenv("RUN_SCHEDULER", "false")
    for var in ("DATABASE_URL", "SMTP_HOST", "GOTENBERG_URL", "OIDC_ISSUER", "PORTAL_ALLOWED_IPS", "SECRET_KEY",
                "GITHUB_REPO", "GITHUB_BRANCH", "GITHUB_FOLDER", "GITHUB_TOKEN", "ONLYOFFICE_URL"):
        monkeypatch.delenv(var, raising=False)
    from promulga.config import get_config
    get_config.cache_clear()
    yield get_config()
    get_config.cache_clear()


@pytest.fixture()
def db(app_env):
    from promulga import db as database
    from promulga.web.app import bootstrap_admin
    database.init_engine(app_env.database_url)
    bootstrap_admin(app_env)
    _clear_forced_change()
    with database.session_scope() as session:
        yield session


@pytest.fixture()
def client(app_env):
    from fastapi.testclient import TestClient

    from promulga.web.app import create_app
    with TestClient(create_app(app_env)) as c:
        _clear_forced_change()
        yield c


def _clear_forced_change():
    """ADMIN_PASSWORD makes the first admin change it at first sign-in; most tests skip that step."""
    from promulga import db as database
    from promulga.models import User
    with database.session_scope() as db:
        for u in db.query(User).all():
            u.must_change_password = False


def make_pdf(text: str = "Hello") -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer)
    c.drawString(72, 720, text)
    c.save()
    return buffer.getvalue()


def csrf_of(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "no CSRF token in page"
    return match.group(1)


def login(client, email: str, password: str):
    page = client.get("/auth/login")
    return client.post("/auth/login", data={"email": email, "password": password, "next": "/admin",
                                            "csrf": csrf_of(page.text)}, follow_redirects=False)
