"""Passkeys (WebAuthn): sign in with a key pair kept on the user's device.

The device (Windows Hello, Touch ID, Android, a security key such as a
YubiKey, or a password manager) creates a key pair for this site; Promulga
stores only the PUBLIC key. To sign in, the device signs a random challenge
after unlocking (fingerprint, face, PIN). Nothing secret travels or is stored
on the server, and a passkey only works on the site it was made for, so it
cannot be phished.

Endpoints (JSON, called by static/passkeys.js):
    POST /auth/passkey/register/options   signed-in user: start adding a passkey
    POST /auth/passkey/register/verify    store the new public key
    POST /auth/passkey/login/options      anyone: start a sign-in
    POST /auth/passkey/login/verify       check the signature and open the session

The relying party is the host of BASE_URL; browsers allow passkeys on HTTPS
sites and on http://localhost only.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options, generate_registration_options, options_to_json,
    verify_authentication_response, verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor, ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .. import site_settings
from ..config import get_config
from ..db import get_db
from ..models import PasskeyCredential, User, utcnow
from ..security import csrf_valid, throttle
from ..workflow import log_admin
from .auth import _safe_next
from .deps import current_user, login_session

router = APIRouter(prefix="/auth/passkey")


def relying_party() -> tuple[str, str]:
    """(rp_id, origin) from BASE_URL, e.g. ('docs.example.org', 'https://docs.example.org')."""
    parts = urlsplit(get_config().base_url)
    return parts.hostname or "localhost", f"{parts.scheme}://{parts.netloc}"


def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


def _csrf_ok(request: Request) -> bool:
    return csrf_valid(request.session, request.headers.get("x-csrf-token"))


# ---------------------------------------------------------------------------
# Registration (from My account)
# ---------------------------------------------------------------------------

@router.post("/register/options")
def register_options(request: Request, db: Session = Depends(get_db), user: User | None = Depends(current_user)):
    if user is None:
        return _error("Sign in first.", 401)
    if not _csrf_ok(request):
        return _error("The page has expired: reload it.", 400)
    rp_id, _origin = relying_party()
    existing = db.query(PasskeyCredential).filter_by(user_id=user.id).all()
    options = generate_registration_options(
        rp_id=rp_id, rp_name=site_settings.get(db, "site_name") or "Promulga",
        user_id=str(user.id).encode(), user_name=user.email, user_display_name=user.display,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id)) for c in existing],
    )
    request.session["passkey_reg"] = bytes_to_base64url(options.challenge)
    return JSONResponse(json.loads(options_to_json(options)))


@router.post("/register/verify")
async def register_verify(request: Request, db: Session = Depends(get_db), user: User | None = Depends(current_user)):
    if user is None:
        return _error("Sign in first.", 401)
    if not _csrf_ok(request):
        return _error("The page has expired: reload it.", 400)
    challenge = request.session.pop("passkey_reg", None)
    if not challenge:
        return _error("No registration in progress: try again.")
    body = await request.json()
    rp_id, origin = relying_party()
    try:
        verified = verify_registration_response(
            credential=body.get("credential"), expected_challenge=base64url_to_bytes(challenge),
            expected_rp_id=rp_id, expected_origin=origin, require_user_verification=True)
    except (InvalidRegistrationResponse, ValueError, KeyError, TypeError) as exc:
        return _error(f"The passkey could not be verified: {exc}")
    credential_id = bytes_to_base64url(verified.credential_id)
    if db.query(PasskeyCredential).filter_by(credential_id=credential_id).first():
        return _error("This passkey is already registered.")
    transports = (body.get("credential") or {}).get("response", {}).get("transports") or []
    name = (str(body.get("name") or "").strip() or "Passkey")[:100]
    db.add(PasskeyCredential(user_id=user.id, credential_id=credential_id,
                             public_key=bytes_to_base64url(verified.credential_public_key),
                             sign_count=verified.sign_count, transports=",".join(map(str, transports))[:200], name=name))
    log_admin(db, user, "Passkey added", name)
    db.commit()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Sign-in (login page)
# ---------------------------------------------------------------------------

@router.post("/login/options")
def login_options(request: Request):
    client = request.client.host if request.client else "?"
    if throttle.blocked(f"ip:{client}"):
        return _error("Too many failed attempts. Wait 15 minutes and try again.", 429)
    rp_id, _origin = relying_party()
    # No allowCredentials: the device proposes the passkeys it has for this site (no e-mail to type).
    options = generate_authentication_options(rp_id=rp_id, user_verification=UserVerificationRequirement.REQUIRED)
    request.session["passkey_auth"] = bytes_to_base64url(options.challenge)
    return JSONResponse(json.loads(options_to_json(options)))


@router.post("/login/verify")
async def login_verify(request: Request, db: Session = Depends(get_db)):
    client = request.client.host if request.client else "?"
    challenge = request.session.pop("passkey_auth", None)
    if not challenge:
        return _error("No sign-in in progress: try again.")
    body = await request.json()
    credential = body.get("credential") or {}
    row = db.query(PasskeyCredential).filter_by(credential_id=str(credential.get("id", ""))).first()
    if row is None or not row.user.active:
        throttle.fail(f"ip:{client}")
        return _error("This passkey is not known here (it may have been removed).", 401)
    rp_id, origin = relying_party()
    try:
        verified = verify_authentication_response(
            credential=credential, expected_challenge=base64url_to_bytes(challenge), expected_rp_id=rp_id,
            expected_origin=origin, credential_public_key=base64url_to_bytes(row.public_key),
            credential_current_sign_count=row.sign_count, require_user_verification=True)
    except (InvalidAuthenticationResponse, ValueError, KeyError, TypeError) as exc:
        throttle.fail(f"ip:{client}")
        return _error(f"The passkey could not be verified: {exc}", 401)
    row.sign_count = verified.new_sign_count
    row.last_used_at = utcnow()
    user = row.user
    user.last_login_at = utcnow()
    db.commit()
    login_session(request, user)
    target = _safe_next(str(body.get("next") or ""))
    if user.role == "viewer" and target.startswith("/admin"):
        target = "/"
    return JSONResponse({"ok": True, "redirect": target})
