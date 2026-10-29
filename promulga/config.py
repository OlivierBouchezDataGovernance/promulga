"""Deployment configuration, read once from environment variables.

Only what belongs to the *server* lives here (database, secrets, SMTP, SSO,
host names, integrations). Everything an administrator changes from the web
interface (colours, banner, deadlines, ...) is stored in the database instead,
see :mod:`promulga.site_settings`.

Secrets
-------
Every secret can be given either directly (``SMTP_PASSWORD=...``) or, better,
as a file (``SMTP_PASSWORD_FILE=/run/secrets/smtp_password``), which is how
Docker secrets work: the value then never appears in ``docker inspect`` nor in
the ``.env`` file. A secret file readable by other users is reported on the
*Security checks* page.
"""

from __future__ import annotations

import os
import secrets
import stat
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _list(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


SECRET_NAMES = ["SECRET_KEY", "ADMIN_PASSWORD", "SMTP_PASSWORD", "OIDC_CLIENT_SECRET", "GITHUB_TOKEN",
                "ONLYOFFICE_JWT_SECRET", "SIGNING_CERT_PASSWORD"]


def _read_secret(name: str, warnings: list[str], used_env: list[str]) -> str:
    """Value of secret `name`: from NAME_FILE if set, else from NAME."""
    path = os.environ.get(f"{name}_FILE")
    if path:
        p = Path(path)
        private_copy = Path("/dev/shm/promulga-secrets") / p.name   # made by deploy/entrypoint.sh
        if private_copy.is_file() and os.access(private_copy, os.R_OK):
            p = private_copy
        if not p.is_file():
            warnings.append(f"{name}_FILE points to a missing file ({path}).")
            return ""
        if os.name == "posix" and p.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            warnings.append(f"The secret file {path} is readable by other users: run 'chmod 600' on it.")
        return p.read_text(encoding="utf-8").strip()
    value = os.environ.get(name, "")
    if value:
        used_env.append(name)
    return value


@dataclass(frozen=True)
class Config:
    data_dir: Path
    database_url: str
    secret_key: str
    base_url: str                 # public URL of the site, used in e-mail links
    public_host: str = ""         # optional: host name of the portal
    admin_host: str = ""          # optional: host name of the admin site
    portal_allowed_ips: list[str] = field(default_factory=list)
    secure_cookies: bool = False
    # Identifiers and file names (tags: see promulga.naming)
    ref_pattern: str = "{TYPE_CODE}-{ID}"
    filename_pattern: str = "{REF}-v{VERSION}.{SUBVERSION}-{TITLE}"
    # E-mail
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    # PDF
    gotenberg_url: str = ""
    sign_pdfs: bool = False
    signing_cert_file: str = ""   # PKCS#12 (.p12/.pfx); empty = self-signed certificate in the data dir
    signing_cert_password: str = ""
    # Single sign-on
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_label: str = "Single sign-on"
    oidc_auto_create_role: str = ""   # '' = only existing users may sign in with SSO
    oidc_role_claim: str = "groups"   # claim holding the user's groups
    oidc_role_map: dict = field(default_factory=dict)  # {group: role}; the role is refreshed at each SSO sign-in
    # First administrator
    admin_email: str = ""
    admin_password: str = ""
    # Public export (GitHub)
    export_tag: str = "public"
    export_site_url: str = ""     # where the exported package will be served, for absolute links
    github_repo: str = ""         # owner/name
    github_branch: str = "main"
    github_folder: str = "policies"
    github_token: str = ""
    # OnlyOffice online editor
    onlyoffice_url: str = ""          # as seen by the browser, e.g. /onlyoffice
    onlyoffice_internal_url: str = ""  # as seen by the app container, e.g. http://onlyoffice
    onlyoffice_jwt_secret: str = ""
    internal_url: str = ""            # the app as seen by OnlyOffice, e.g. http://app:8000
    # Misc
    run_scheduler: bool = True
    max_upload_mb: int = 25
    security_warnings: list[str] = field(default_factory=list)
    secrets_in_env: list[str] = field(default_factory=list)

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

    @property
    def github_enabled(self) -> bool:
        return bool(self.github_repo and self.github_token)

    @property
    def onlyoffice_enabled(self) -> bool:
        return bool(self.onlyoffice_url and self.onlyoffice_jwt_secret)


def _secret_key(data_dir: Path, configured: str) -> str:
    """The configured SECRET_KEY, else a random key persisted in the data dir."""
    if configured:
        return configured
    path = data_dir / "secret_key"
    if path.exists():
        return path.read_text().strip()
    key = secrets.token_urlsafe(48)
    path.write_text(key)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return key


def load_config() -> Config:
    data_dir = Path(os.environ.get("PROMULGA_DATA_DIR", "./data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "files").mkdir(exist_ok=True)
    (data_dir / "assets").mkdir(exist_ok=True)
    env = os.environ.get
    warnings: list[str] = []
    in_env: list[str] = []
    s = {name: _read_secret(name, warnings, in_env) for name in SECRET_NAMES}
    return Config(
        data_dir=data_dir,
        database_url=env("DATABASE_URL") or f"sqlite:///{(data_dir / 'promulga.sqlite3').as_posix()}",
        secret_key=_secret_key(data_dir, s["SECRET_KEY"]),
        base_url=(env("BASE_URL") or "http://localhost:8000").rstrip("/"),
        public_host=env("PUBLIC_HOST", ""),
        admin_host=env("ADMIN_HOST", ""),
        portal_allowed_ips=_list(env("PORTAL_ALLOWED_IPS")),
        secure_cookies=_bool(env("SECURE_COOKIES")),
        ref_pattern=env("REF_PATTERN") or "{TYPE_CODE}-{ID}",
        filename_pattern=env("FILENAME_PATTERN") or "{REF}-v{VERSION}.{SUBVERSION}-{TITLE}",
        smtp_host=env("SMTP_HOST", ""),
        smtp_port=int(env("SMTP_PORT") or 587),
        smtp_user=env("SMTP_USER", ""),
        smtp_password=s["SMTP_PASSWORD"],
        smtp_from=env("SMTP_FROM", ""),
        smtp_starttls=_bool(env("SMTP_STARTTLS"), True),
        smtp_ssl=_bool(env("SMTP_SSL")),
        gotenberg_url=(env("GOTENBERG_URL") or "").rstrip("/"),
        sign_pdfs=_bool(env("SIGN_PDFS")),
        signing_cert_file=env("SIGNING_CERT_FILE", ""),
        signing_cert_password=s["SIGNING_CERT_PASSWORD"],
        oidc_issuer=(env("OIDC_ISSUER") or "").rstrip("/"),
        oidc_client_id=env("OIDC_CLIENT_ID", ""),
        oidc_client_secret=s["OIDC_CLIENT_SECRET"],
        oidc_label=env("OIDC_LABEL") or "Single sign-on",
        oidc_auto_create_role=env("OIDC_AUTO_CREATE_ROLE", ""),
        oidc_role_claim=env("OIDC_ROLE_CLAIM") or "groups",
        oidc_role_map=parse_role_map(env("OIDC_ROLE_MAP", "")),
        admin_email=env("ADMIN_EMAIL", ""),
        admin_password=s["ADMIN_PASSWORD"],
        export_tag=(env("EXPORT_TAG") or "public").strip().lower(),
        export_site_url=(env("EXPORT_SITE_URL") or "").rstrip("/"),
        github_repo=env("GITHUB_REPO", ""),
        github_branch=env("GITHUB_BRANCH") or "main",
        github_folder=(env("GITHUB_FOLDER") or "policies").strip("/"),
        github_token=s["GITHUB_TOKEN"],
        onlyoffice_url=(env("ONLYOFFICE_URL") or "").rstrip("/"),
        onlyoffice_internal_url=(env("ONLYOFFICE_INTERNAL_URL") or "").rstrip("/"),
        onlyoffice_jwt_secret=s["ONLYOFFICE_JWT_SECRET"],
        internal_url=(env("PROMULGA_INTERNAL_URL") or "").rstrip("/"),
        run_scheduler=_bool(env("RUN_SCHEDULER"), True),
        max_upload_mb=int(env("MAX_UPLOAD_MB") or 25),
        security_warnings=warnings,
        secrets_in_env=in_env,
    )


@lru_cache
def get_config() -> Config:
    return load_config()


def parse_role_map(text: str) -> dict:
    """OIDC_ROLE_MAP="platform-admin=admin, dg-lead=manager, data-consumer=viewer" -> {group: role}."""
    from .models import ROLES
    out = {}
    for item in (text or "").split(","):
        group, _, role = item.partition("=")
        if group.strip() and role.strip().lower() in ROLES:
            out[group.strip()] = role.strip().lower()
    return out
