"""Indicators, version comparison, security checks and webhook delivery."""

from __future__ import annotations

import difflib
import io
import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from . import docx_meta, site_settings, storage
from .config import get_config
from .models import (
    ACTIVE_STATUSES, PUBLISHED, ROLE_ADMIN, STATUSES, Acknowledgment, Document, Publication, Revision, StageLog,
    User, WebhookDelivery, utcnow,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def kpis(db: Session, months: int = 12) -> dict:
    """Workflow indicators over the last `months` months."""
    since = utcnow() - timedelta(days=30 * months)
    docs = db.query(Document).all()
    by_status = {s: sum(1 for d in docs if d.status == s) for s in STATUSES}
    stages = db.query(StageLog).filter(StageLog.started_at >= since).all()

    per_stage = {}
    for stage in ACTIVE_STATUSES:
        rows = [s for s in stages if s.stage == stage and s.ended_at]
        durations = [(s.ended_at - s.started_at).total_seconds() / 86400 for s in rows]
        on_time = [s.on_time for s in rows if s.on_time is not None]
        per_stage[stage] = {
            "completed": len(rows),
            "avg_days": round(sum(durations) / len(durations), 1) if durations else None,
            "on_time_pct": round(100 * sum(on_time) / len(on_time)) if on_time else None,
            "open": sum(1 for s in stages if s.stage == stage and s.ended_at is None),
        }

    # Cycle time: first Draft start to publication, per publication of the period.
    cycles = []
    for pub in db.query(Publication).filter(Publication.published_at >= since.date()).all():
        first = (db.query(StageLog).filter(StageLog.document_id == pub.document_id, StageLog.stage == "Draft",
                                           StageLog.started_at <= datetime.combine(pub.published_at, datetime.max.time()))
                 .order_by(StageLog.started_at.desc()).first())
        if first:
            cycles.append((pub.published_at - first.started_at.date()).days)

    late_by_owner: dict[str, int] = defaultdict(int)
    for d in docs:
        if d.is_overdue:
            late_by_owner[d.owner.display] += 1

    ack = []
    for d in docs:
        if d.ack_required and d.latest_publication and d.status == PUBLISHED:
            audience = db.query(User).filter(User.active.is_(True)).count()
            done = db.query(Acknowledgment).filter_by(publication_id=d.latest_publication.id).count()
            ack.append({"doc": d, "done": done, "audience": audience,
                        "pct": round(100 * done / audience) if audience else 0})

    return {
        "months": months, "total": len(docs), "by_status": by_status, "per_stage": per_stage,
        "overdue": [d for d in docs if d.is_overdue],
        "reviews_due": sorted([d for d in docs if d.status == PUBLISHED and d.next_review
                               and d.next_review <= date.today() + timedelta(days=30)], key=lambda d: d.next_review),
        "published_period": len(cycles),
        "avg_cycle_days": round(sum(cycles) / len(cycles)) if cycles else None,
        "late_by_owner": sorted(late_by_owner.items(), key=lambda kv: -kv[1]),
        "acknowledgments": ack,
    }


# ---------------------------------------------------------------------------
# Comparing two versions
# ---------------------------------------------------------------------------

def text_lines(filename: str, data: bytes) -> list[str]:
    name = filename.lower()
    try:
        if name.endswith(".docx"):
            return docx_meta.docx_paragraphs(data)
        if name.endswith(".pdf"):
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            return [line.strip() for page in reader.pages for line in (page.extract_text() or "").splitlines()
                    if line.strip()]
        if name.endswith((".md", ".txt")):
            return [line.rstrip() for line in data.decode("utf-8", "replace").splitlines() if line.strip()]
    except Exception as exc:   # comparison is best effort
        return [f"(text could not be extracted: {exc})"]
    return ["(this file type cannot be compared; download both versions instead)"]


def compare(old: Revision, new: Revision) -> list[tuple[str, str]]:
    """[(op, line)] with op in ' ' (same), '-' (removed), '+' (added)."""
    a = text_lines(old.filename, storage.read_bytes(old.storage_path))
    b = text_lines(new.filename, storage.read_bytes(new.storage_path))
    out = []
    for line in difflib.ndiff(a, b):
        if line.startswith("? "):
            continue
        out.append((line[0], line[2:]))
    return out


# ---------------------------------------------------------------------------
# Security checks (Admin > Security)
# ---------------------------------------------------------------------------

def security_checks(db: Session) -> list[dict]:
    cfg = get_config()
    site = site_settings.get_all(db)
    checks = []

    def add(ok: bool | None, title: str, detail: str):
        checks.append({"ok": ok, "title": title, "detail": detail})

    https = cfg.base_url.startswith("https://")
    add(https, "HTTPS", "BASE_URL uses https." if https else "BASE_URL is not https: fine for a local test only.")
    add(cfg.secure_cookies or not https, "Secure cookies",
        "Session cookies are sent over HTTPS only." if cfg.secure_cookies
        else ("SECURE_COOKIES=false: set it to true with HTTPS." if https else "Not applicable over plain HTTP."))
    admins_temp = db.query(User).filter(User.role == ROLE_ADMIN, User.must_change_password.is_(True)).count()
    add(admins_temp == 0, "Administrator password",
        "No administrator uses a temporary password." if not admins_temp
        else f"{admins_temp} administrator(s) still have the password of the .env file.")
    add(not cfg.admin_password, "ADMIN_PASSWORD removed",
        "No administrator password in the configuration." if not cfg.admin_password
        else "ADMIN_PASSWORD is still set: remove it from .env (or its secret file) once you signed in.")
    in_env = [n for n in cfg.secrets_in_env if n != "ADMIN_PASSWORD"]
    add(not in_env, "Secrets in files",
        "Secrets are read from files (*_FILE)." if not in_env
        else "Given as plain environment variables: " + ", ".join(in_env) + ". Prefer the *_FILE variant "
             "(see deploy/setup.sh): plain variables are visible with 'docker inspect'.")
    for w in cfg.security_warnings:
        add(False, "Secret file", w)
    add(cfg.smtp_enabled or None, "E-mail", "SMTP configured." if cfg.smtp_enabled
        else "No SMTP: e-mails stay in the Outbox.")
    add(bool(cfg.gotenberg_url) or None, "PDF conversion", "Gotenberg configured." if cfg.gotenberg_url
        else "No converter: only PDF files can be published.")
    add(cfg.sign_pdfs or None, "Electronic seal", "Published PDFs are signed (PAdES)." if cfg.sign_pdfs
        else "SIGN_PDFS=false: PDFs are stamped and fingerprinted but not signed.")
    last = site.get("last_backup") or ""
    recent = bool(last) and (date.today() - date.fromisoformat(last[:10])).days <= 7
    add(recent, "Backup", f"Last backup: {last}." if last else "No backup recorded: run 'promulga backup' "
        "(see INSTALL.md) and schedule it.")
    return checks


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

def flush_webhooks(db: Session, limit: int = 50) -> int:
    pending = (db.query(WebhookDelivery).filter(WebhookDelivery.sent_at.is_(None), WebhookDelivery.attempts < 5)
               .order_by(WebhookDelivery.id).limit(limit).all())
    sent = 0
    for d in pending:
        d.attempts += 1
        try:
            r = httpx.post(d.url, content=d.payload, headers={"Content-Type": "application/json"}, timeout=15)
            if r.status_code < 300:
                d.sent_at, d.error = utcnow(), ""
                sent += 1
            else:
                d.error = f"HTTP {r.status_code}: {r.text[:300]}"
        except httpx.HTTPError as exc:
            d.error = str(exc)[:300]
    return sent


def test_webhook(url: str) -> str:
    r = httpx.post(url, content=json.dumps({"text": "Promulga test message: the webhook works."}),
                   headers={"Content-Type": "application/json"}, timeout=15)
    return f"HTTP {r.status_code}"
