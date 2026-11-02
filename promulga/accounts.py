"""User accounts: lookup by e-mail, invitations, password resets."""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from . import mailer
from .config import get_config
from .models import ROLE_CONTRIBUTOR, ROLE_VIEWER, ROLES, User
from .security import create_token

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
INVITE_HOURS = 24 * 7
RESET_HOURS = 2


class AccountError(ValueError):
    pass


def normalise_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise AccountError(f"Not an e-mail address: {email or '(empty)'}")
    return email


GROUP_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{1,49}")


def parse_people(text: str) -> list[str]:
    """Like parse_emails, but also keeps group tokens ('@dpo-team')."""
    out: list[str] = []
    for part in re.split(r"[,;\s]+", text or ""):
        part = part.strip()
        if not part:
            continue
        if part.startswith("@"):
            token = part.lower()
            if not GROUP_NAME.fullmatch(token[1:]):
                raise AccountError(f"Invalid group name: {part}")
        else:
            token = normalise_email(part)
        if token not in out:
            out.append(token)
    return out


def sync_sso_groups(db: Session, user, groups) -> None:
    """At an SSO sign-in: the user belongs to the SSO groups listed in the claim, and to no other SSO group.
    SSO groups are created when first seen; local groups are never touched."""
    from .models import GROUP_SSO, Group
    if isinstance(groups, str):
        groups = [g.strip() for g in groups.replace(";", ",").split(",")]
    names = {str(g).strip().lower() for g in groups or [] if GROUP_NAME.fullmatch(str(g).strip().lower())}
    for name in names:
        group = db.query(Group).filter_by(name=name).first()
        if group is None:
            group = Group(name=name, source=GROUP_SSO, description="From the single sign-on")
            db.add(group)
        if group.source == GROUP_SSO and user not in group.members:
            group.members.append(user)
    for group in db.query(Group).filter_by(source=GROUP_SSO).all():
        if group.name not in names and user in group.members:
            group.members.remove(user)


def parse_emails(text: str) -> list[str]:
    """Splits a comma/semicolon/newline separated list, validates, de-duplicates."""
    out: list[str] = []
    for part in re.split(r"[,;\s]+", text or ""):
        if part.strip():
            email = normalise_email(part)
            if email not in out:
                out.append(email)
    return out


def find(db: Session, email: str) -> User | None:
    return db.query(User).filter_by(email=normalise_email(email)).first()


def create_user(db: Session, email: str, name: str, role: str, inviter: User | None, site: dict,
                send_invite: bool = True) -> User:
    email = normalise_email(email)
    if role not in ROLES:
        raise AccountError(f"Unknown role {role}")
    if find(db, email):
        raise AccountError(f"{email} already has an account.")
    user = User(email=email, name=(name or "").strip(), role=role)
    db.add(user)
    db.flush()
    if send_invite:
        send_invitation(db, user, inviter, site)
    return user


def send_invitation(db: Session, user: User, inviter: User | None, site: dict) -> None:
    token = create_token(db, user, "invite", INVITE_HOURS)
    link = f"{get_config().base_url}/auth/set-password?token={token}"
    mailer.queue(db, user.email, "You are invited", "notice.html", site,
                 heading=f"Welcome to {site['site_name']}",
                 lines=[f"{inviter.display if inviter else 'An administrator'} created an account for you "
                        f"in the governance document workflow ({user.email}).",
                        "Choose your password with the button below. The link is valid for 7 days."],
                 button=("Choose my password", link))


def send_password_reset(db: Session, user: User, site: dict) -> None:
    token = create_token(db, user, "reset", RESET_HOURS)
    link = f"{get_config().base_url}/auth/set-password?token={token}&purpose=reset"
    mailer.queue(db, user.email, "Reset your password", "notice.html", site,
                 heading="Password reset",
                 lines=["Someone (hopefully you) asked to reset your password.",
                        "The link below is valid for 2 hours. If you did not ask for it, ignore this e-mail."],
                 button=("Choose a new password", link))


def participant(db: Session, email: str, inviter: User, site: dict) -> User:
    """Returns the user for a document participant, inviting them if unknown.

    Viewers are promoted to contributors, since they now have work to do.
    """
    user = find(db, email)
    if user is None:
        return create_user(db, email, "", ROLE_CONTRIBUTOR, inviter, site)
    if not user.active:
        raise AccountError(f"The account {user.email} is deactivated.")
    if user.role == ROLE_VIEWER:
        user.role = ROLE_CONTRIBUTOR
    return user
