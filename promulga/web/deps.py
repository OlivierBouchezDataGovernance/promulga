"""Shared helpers of the HTTP layer: templates, current user, CSRF, flash messages."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

import nh3
from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy.orm import Session

from .. import __version__, icons, site_settings
from ..config import get_config
from ..db import get_db
from ..models import ROLE_ADMIN, ROLE_MANAGER, User
from ..security import csrf_token, csrf_valid

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# HTML the administrator may use in the banner and footer.
ALLOWED_TAGS = {"a", "b", "strong", "i", "em", "u", "p", "br", "span", "div", "h1", "h2", "h3", "h4", "ul", "ol",
                "li", "img", "small", "hr", "blockquote", "section", "nav", "address"}
ALLOWED_ATTRIBUTES = {"*": {"class", "style", "id", "title"}, "a": {"href", "target"},
                      "img": {"src", "alt", "width", "height"}}


def sanitize_html(html: str) -> str:
    """Cleans admin-provided HTML: no scripts, no event handlers, safe URLs only."""
    return nh3.clean(html or "", tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES,
                     url_schemes={"http", "https", "mailto"}, link_rel="noopener noreferrer")


def _fmt_date(value) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _filesize(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


templates.env.filters["fdate"] = _fmt_date
templates.env.filters["filesize"] = _filesize
templates.env.filters["safe_html"] = lambda html: Markup(sanitize_html(html))
templates.env.globals["app_version"] = __version__
templates.env.globals["icon"] = icons.svg
templates.env.globals["type_icon"] = lambda site, doc_type, size=20: icons.svg(
    site_settings.doc_type_icon(site, doc_type), size)


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------

class LoginRequired(Exception):
    def __init__(self, next_url: str):
        self.next_url = next_url


class Forbidden(Exception):
    def __init__(self, message: str = "You do not have access to this page."):
        self.message = message


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(User, user_id)
    if user is None or not user.active or request.session.get("auth_version") != _auth_version(user):
        request.session.clear()
        return None
    return user


def _auth_version(user: User) -> str:
    """Changes when the password changes, so other sessions are logged out."""
    return (user.password_hash or "")[-16:] + str(user.id)


def login_session(request: Request, user: User) -> None:
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["auth_version"] = _auth_version(user)


def require_user(request: Request, user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise LoginRequired(request.url.path + (f"?{request.url.query}" if request.url.query else ""))
    return user


def require_contributor(user: User = Depends(require_user)) -> User:
    """The admin site is for people who have work in the workflow (not mere viewers)."""
    if user.role == "viewer":
        raise Forbidden("Your account can read the portal only.")
    return user


def require_manager(user: User = Depends(require_user)) -> User:
    if not user.has_role(ROLE_MANAGER):
        raise Forbidden("This page is for Document Managers.")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.has_role(ROLE_ADMIN):
        raise Forbidden("This page is for administrators.")
    return user


# ---------------------------------------------------------------------------
# CSRF and flash messages
# ---------------------------------------------------------------------------

class CsrfError(Exception):
    pass


async def csrf_protect(request: Request) -> None:
    """Dependency for every POST route: the form must carry the session's CSRF token."""
    form = await request.form()
    if not csrf_valid(request.session, form.get("csrf")):
        raise CsrfError()


def flash(request: Request, message: str, kind: str = "info") -> None:
    request.session.setdefault("flash", []).append({"message": message, "kind": kind})


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def login_url(next_url: str) -> str:
    return f"/auth/login?next={quote(next_url)}"


def render(request: Request, db: Session, template: str, user: User | None = None, status_code: int = 200,
           **context):
    cfg = get_config()
    site = site_settings.get_all(db)
    messages = request.session.pop("flash", [])
    return templates.TemplateResponse(request, template, {
        "site": site, "user": user, "csrf": csrf_token(request.session), "messages": messages,
        "oidc_enabled": cfg.oidc_enabled, "oidc_label": cfg.oidc_label, "smtp_enabled": cfg.smtp_enabled,
        **context,
    }, status_code=status_code)
