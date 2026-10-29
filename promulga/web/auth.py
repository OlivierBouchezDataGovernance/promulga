"""Login (e-mail + password, or OIDC single sign-on), logout, invitations and password resets."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from .. import accounts, site_settings
from ..config import get_config
from ..db import get_db
from ..models import ROLES, User, utcnow
from ..security import check_password_strength, hash_password, peek_token, throttle, use_token, verify_password
from ..workflow import log_admin
from .deps import csrf_protect, current_user, flash, login_session, redirect, render

router = APIRouter(prefix="/auth")


def _safe_next(next_url: str | None) -> str:
    """Only local redirects, to avoid open redirects."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/admin"


@router.get("/login")
def login_form(request: Request, next: str = "", db: Session = Depends(get_db),
               user: User | None = Depends(current_user)):
    if user:
        return redirect(_safe_next(next))
    return render(request, db, "auth/login.html", next=_safe_next(next))


@router.post("/login", dependencies=[Depends(csrf_protect)])
def login(request: Request, email: str = Form(""), password: str = Form(""), next: str = Form(""),
          db: Session = Depends(get_db)):
    email = (email or "").strip().lower()
    client = request.client.host if request.client else "?"
    if throttle.blocked(f"e:{email}", f"ip:{client}"):
        flash(request, "Too many failed attempts. Wait 15 minutes and try again.", "error")
        return redirect(f"/auth/login?next={quote(_safe_next(next))}")
    user = db.query(User).filter_by(email=email).first()
    if user is None or not user.active or not verify_password(user, password):
        throttle.fail(f"e:{email}", f"ip:{client}")
        flash(request, "Wrong e-mail address or password.", "error")
        return redirect(f"/auth/login?next={quote(_safe_next(next))}")
    throttle.reset(f"e:{email}")
    user.last_login_at = utcnow()
    db.commit()
    login_session(request, user)
    target = _safe_next(next)
    if user.role == "viewer" and target.startswith("/admin"):
        target = "/"
    return redirect(target)


@router.post("/logout", dependencies=[Depends(csrf_protect)])
def logout(request: Request):
    request.session.clear()
    return redirect("/")


@router.get("/forgot")
def forgot_form(request: Request, db: Session = Depends(get_db)):
    return render(request, db, "auth/forgot.html")


@router.post("/forgot", dependencies=[Depends(csrf_protect)])
def forgot(request: Request, email: str = Form(""), db: Session = Depends(get_db)):
    client = request.client.host if request.client else "?"
    if not throttle.blocked(f"reset:{client}"):
        throttle.fail(f"reset:{client}")   # counts requests, not failures: max 8 per 15 min
        try:
            user = accounts.find(db, email)
        except accounts.AccountError:
            user = None
        if user and user.active:
            accounts.send_password_reset(db, user, site_settings.get_all(db))
            db.commit()
    flash(request, "If this address has an account, an e-mail with a link is on its way.")
    return redirect("/auth/login")


@router.get("/set-password")
def set_password_form(request: Request, token: str = "", purpose: str = "invite", db: Session = Depends(get_db)):
    purpose = "reset" if purpose == "reset" else "invite"
    user = peek_token(db, token, purpose)
    if user is None:
        flash(request, "This link is invalid or has expired. Ask for a new one.", "error")
        return redirect("/auth/forgot")
    return render(request, db, "auth/set_password.html", token=token, purpose=purpose, target=user)


@router.post("/set-password", dependencies=[Depends(csrf_protect)])
def set_password(request: Request, token: str = Form(""), purpose: str = Form("invite"),
                 password: str = Form(""), password2: str = Form(""), name: str = Form(""),
                 db: Session = Depends(get_db)):
    purpose = "reset" if purpose == "reset" else "invite"
    try:
        if password != password2:
            raise ValueError("The two passwords are different.")
        check_password_strength(password)
    except ValueError as exc:
        flash(request, str(exc), "error")
        return redirect(f"/auth/set-password?token={token}&purpose={purpose}")
    user = use_token(db, token, purpose)
    if user is None:
        flash(request, "This link is invalid or has expired. Ask for a new one.", "error")
        return redirect("/auth/forgot")
    user.password_hash = hash_password(password)
    user.must_change_password = False
    if name.strip():
        user.name = name.strip()[:200]
    user.last_login_at = utcnow()
    log_admin(db, user, "Password set" if purpose == "invite" else "Password reset")
    db.commit()
    login_session(request, user)
    flash(request, "Your password is saved. Welcome!", "success")
    return redirect("/" if user.role == "viewer" else "/admin")


# ---------------------------------------------------------------------------
# OIDC single sign-on (Entra ID, Google, Keycloak, Authentik, ...)
# ---------------------------------------------------------------------------

_oauth = None


def _oidc_client():
    global _oauth
    from authlib.integrations.starlette_client import OAuth
    cfg = get_config()
    if _oauth is None:
        _oauth = OAuth()
        _oauth.register(
            name="sso",
            server_metadata_url=f"{cfg.oidc_issuer}/.well-known/openid-configuration",
            client_id=cfg.oidc_client_id,
            client_secret=cfg.oidc_client_secret,
            client_kwargs={"scope": "openid email profile"},
        )
    return _oauth.sso


@router.get("/oidc/login")
async def oidc_login(request: Request, next: str = ""):
    cfg = get_config()
    if not cfg.oidc_enabled:
        return redirect("/auth/login")
    request.session["oidc_next"] = _safe_next(next)
    return await _oidc_client().authorize_redirect(request, f"{cfg.base_url}/auth/oidc/callback")


@router.get("/oidc/callback")
async def oidc_callback(request: Request, db: Session = Depends(get_db)):
    cfg = get_config()
    if not cfg.oidc_enabled:
        return redirect("/auth/login")
    try:
        token = await _oidc_client().authorize_access_token(request)
    except Exception as exc:  # the provider refused, or the user cancelled
        flash(request, f"Single sign-on failed: {exc}", "error")
        return redirect("/auth/login")
    info = token.get("userinfo") or {}
    email = (info.get("email") or "").strip().lower()
    if not email or info.get("email_verified") is False:
        flash(request, "Your identity provider did not give a verified e-mail address.", "error")
        return redirect("/auth/login")
    user = db.query(User).filter_by(email=email).first()
    if user is None and cfg.oidc_auto_create_role in ROLES:
        user = User(email=email, name=info.get("name", "")[:200], role=cfg.oidc_auto_create_role)
        db.add(user)
    if user is None or not user.active:
        flash(request, f"No active account for {email}. Ask an administrator to invite you.", "error")
        return redirect("/auth/login")
    user.oidc_subject = str(info.get("sub", ""))[:255]
    user.last_login_at = utcnow()
    db.commit()
    next_url = request.session.pop("oidc_next", "/admin")
    login_session(request, user)
    return redirect("/" if user.role == "viewer" and next_url.startswith("/admin") else next_url)
