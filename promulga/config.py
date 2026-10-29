"""Deployment configuration, read once from environment variables.

Only what belongs to the *server* lives here (database, secrets, SMTP, SSO,
host names). Everything an administrator changes from the web interface
(colours, banner, deadlines, ...) is stored in the database instead, see
:mod:`promulga.site_settings`.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _list(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


@dataclass(frozen=True)
class Config:
    data_dir: Path
    database_url: str
    secret_key: str
    base_url: str                 # public URL of the site, used in e-mail links
    public_host: str              # optional: host name of the portal
    admin_host: str               # optional: host name of the admin site
    portal_allowed_ips: list[str] = field(default_factory=list)
    secure_cookies: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    gotenberg_url: str = ""
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_label: str = "Single sign-on"
    oidc_auto_create_role: str = ""   # '' = only existing users may sign in with SSO
    admin_email: str = ""
    admin_password: str = ""
    run_scheduler: bool = True
    max_upload_mb: int = 25

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def assets_dir(self) -> Path:
        return self.data_dir / "assets"

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id)


def _secret_key(data_dir: Path) -> str:
    """SECRET_KEY from the environment, else a random key persisted in the data dir."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = data_dir / "secret_key"
    if path.exists():
        return path.read_text().strip()
    key = secrets.token_urlsafe(48)
    path.write_text(key)
    return key


def load_config() -> Config:
    data_dir = Path(os.environ.get("PROMULGA_DATA_DIR", "./data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "files").mkdir(exist_ok=True)
    (data_dir / "assets").mkdir(exist_ok=True)
    env = os.environ.get
    return Config(
        data_dir=data_dir,
        database_url=env("DATABASE_URL") or f"sqlite:///{(data_dir / 'promulga.sqlite3').as_posix()}",
        secret_key=_secret_key(data_dir),
        base_url=(env("BASE_URL") or "http://localhost:8000").rstrip("/"),
        public_host=env("PUBLIC_HOST", ""),
        admin_host=env("ADMIN_HOST", ""),
        portal_allowed_ips=_list(env("PORTAL_ALLOWED_IPS")),
        secure_cookies=_bool(env("SECURE_COOKIES")),
        smtp_host=env("SMTP_HOST", ""),
        smtp_port=int(env("SMTP_PORT") or 587),
        smtp_user=env("SMTP_USER", ""),
        smtp_password=env("SMTP_PASSWORD", ""),
        smtp_from=env("SMTP_FROM", ""),
        smtp_starttls=_bool(env("SMTP_STARTTLS"), True),
        smtp_ssl=_bool(env("SMTP_SSL")),
        gotenberg_url=(env("GOTENBERG_URL") or "").rstrip("/"),
        oidc_issuer=(env("OIDC_ISSUER") or "").rstrip("/"),
        oidc_client_id=env("OIDC_CLIENT_ID", ""),
        oidc_client_secret=env("OIDC_CLIENT_SECRET", ""),
        oidc_label=env("OIDC_LABEL") or "Single sign-on",
        oidc_auto_create_role=env("OIDC_AUTO_CREATE_ROLE", ""),
        admin_email=env("ADMIN_EMAIL", ""),
        admin_password=env("ADMIN_PASSWORD", ""),
        run_scheduler=_bool(env("RUN_SCHEDULER"), True),
        max_upload_mb=int(env("MAX_UPLOAD_MB") or 25),
    )


@lru_cache
def get_config() -> Config:
    return load_config()
