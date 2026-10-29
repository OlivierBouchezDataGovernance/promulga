"""FastAPI application: middleware, error pages, start-up (database, first admin, scheduler)."""

from __future__ import annotations

import ipaddress
from datetime import date
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .. import db as database
from ..config import Config, get_config
from . import admin, api, auth, oo, portal
from .deps import CsrfError, Forbidden, LoginRequired, MustChangePassword, flash, login_url, render

log = logging.getLogger("promulga")
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class SiteRoutingMiddleware(BaseHTTPMiddleware):
    """Two web sites on one application.

    - With PUBLIC_HOST and ADMIN_HOST set, the admin site (/admin, /auth) only
      answers on ADMIN_HOST, and "/" on ADMIN_HOST goes to /admin. The portal
      answers on PUBLIC_HOST.
    - With PORTAL_ALLOWED_IPS set, the portal only answers to those networks
      (intranet). The admin site is not restricted by this setting.
    Without these variables, both sites share one host: portal at "/", admin at "/admin".
    """

    def __init__(self, app, cfg: Config):
        super().__init__(app)
        self.cfg = cfg
        self.networks = [ipaddress.ip_network(n, strict=False) for n in cfg.portal_allowed_ips]

    async def dispatch(self, request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0].lower()
        path = request.url.path
        is_admin_path = path.startswith(("/admin", "/auth", "/api", "/oo"))
        if self.cfg.admin_host and self.cfg.public_host:
            if host == self.cfg.public_host.lower() and path.startswith("/admin"):
                return RedirectResponse(f"https://{self.cfg.admin_host}{path}", status_code=307)
            if host == self.cfg.admin_host.lower() and path == "/":
                return RedirectResponse("/admin", status_code=307)
        if self.networks and not is_admin_path and not path.startswith(("/static", "/theme.css", "/assets")):
            client = request.client.host if request.client else ""
            try:
                allowed = any(ipaddress.ip_address(client) in net for net in self.networks)
            except ValueError:
                allowed = False
            if not allowed:
                return PlainTextResponse("This portal is only available on the internal network.", status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return response


def bootstrap_admin(cfg: Config) -> None:
    """Creates the first administrator (ADMIN_EMAIL) if no administrator exists.

    - Without ADMIN_PASSWORD (recommended): a one-time link to choose the
      password is written to the logs (`docker compose logs app`), valid 24 h.
    - With ADMIN_PASSWORD: the password works once and must be changed at the
      first sign-in, so the value in .env is useless afterwards.
    """
    from ..models import ROLE_ADMIN, User
    from ..security import create_token, hash_password

    if not cfg.admin_email:
        return
    with database.session_scope() as db:
        if db.query(User).filter_by(role=ROLE_ADMIN).first():
            return
        user = db.query(User).filter_by(email=cfg.admin_email.lower()).first()
        if user is None:
            user = User(email=cfg.admin_email.lower(), name="Administrator", role=ROLE_ADMIN)
            db.add(user)
        user.role = ROLE_ADMIN
        db.flush()
        if cfg.admin_password:
            user.password_hash = hash_password(cfg.admin_password)
            user.must_change_password = True
            log.warning("First administrator %s created with ADMIN_PASSWORD: it must be changed at the first "
                        "sign-in. Remove ADMIN_PASSWORD from .env afterwards.", user.email)
        else:
            token = create_token(db, user, "invite", 24)
            log.warning("First administrator %s created. Choose the password here (valid 24 h, single use): "
                        "%s/auth/set-password?token=%s", user.email, cfg.base_url, token)


def start_scheduler():
    from apscheduler.schedulers.background import BackgroundScheduler

    from .. import export, insights, mailer, reminders, site_settings

    def flush():
        with database.session_scope() as db:
            mailer.flush_outbox(db)
        with database.session_scope() as db:
            insights.flush_webhooks(db)

    def pending_export():
        """Automatic GitHub export after a publication (Settings > Integrations)."""
        with database.session_scope() as db:
            if not site_settings.get(db, "export_pending") or not get_config().github_enabled:
                return
            try:
                sha = export.push_github(export.build(db), "Promulga: automatic export after publication")
                site_settings.set_many(db, {"export_pending": False,
                                            "last_export": f"{date.today()} {sha[:10] or 'no change'}"})
            except export.ExportError as exc:
                log.error("Automatic export failed: %s", exc)

    def daily():
        with database.session_scope() as db:
            reminders.maybe_run(db)

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(flush, "interval", seconds=60, id="outbox", max_instances=1, coalesce=True)
    scheduler.add_job(daily, "interval", minutes=10, id="reminders", max_instances=1, coalesce=True)
    scheduler.add_job(pending_export, "interval", minutes=2, id="export", max_instances=1, coalesce=True)
    scheduler.start()
    return scheduler


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or get_config()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        database.init_engine(cfg.database_url)
        bootstrap_admin(cfg)
        scheduler = start_scheduler() if cfg.run_scheduler else None
        yield
        if scheduler:
            scheduler.shutdown(wait=False)

    app = FastAPI(title="Promulga", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SiteRoutingMiddleware, cfg=cfg)
    app.add_middleware(SessionMiddleware, secret_key=cfg.secret_key, session_cookie="promulga_session",
                       max_age=60 * 60 * 12, same_site="lax", https_only=cfg.secure_cookies)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(auth.router)
    app.include_router(api.router)
    app.include_router(oo.router)
    app.include_router(admin.router)
    app.include_router(portal.router)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired):
        return RedirectResponse(login_url(exc.next_url), status_code=303)

    @app.exception_handler(MustChangePassword)
    async def _must_change(request: Request, exc: MustChangePassword):
        flash(request, "Please choose a new password before continuing.", "error")
        return RedirectResponse("/admin/account", status_code=303)

    @app.exception_handler(CsrfError)
    async def _csrf(request: Request, exc: CsrfError):
        return PlainTextResponse("The form has expired. Go back, reload the page and try again.", status_code=400)

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, exc: Forbidden):
        return _error_page(request, 403, exc.message)

    @app.exception_handler(HTTPException)
    async def _http(request: Request, exc: HTTPException):
        messages = {404: "This page does not exist.", 403: "You do not have access to this page."}
        detail = exc.detail if isinstance(exc.detail, str) and exc.status_code == 403 else None
        return _error_page(request, exc.status_code, detail or messages.get(exc.status_code, "Error."))

    return app


def _error_page(request: Request, status: int, message: str):
    from ..db import get_db
    from .deps import current_user

    db_gen = get_db()
    db = next(db_gen)
    try:
        user = current_user(request, db)
        return render(request, db, "portal/error.html", user=user, status=status, message=message,
                      status_code=status)
    finally:
        db_gen.close()
