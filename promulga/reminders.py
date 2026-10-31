"""Reminders: "due soon" and "overdue" digests, plus a daily summary for managers.

Run once a day at the configured hour by the scheduler (or `promulga reminders`).
Each person gets ONE e-mail listing all their documents, so a busy reviewer
is not flooded. The ReminderLog remembers what was sent for which stage, so:
  * "due soon" is sent once per stage, `reminder_before_days` before the due date;
  * "overdue" is sent on the first late day and then every `overdue_repeat_days`
    (0 = only once).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime

from sqlalchemy.orm import Session

from . import mailer, site_settings
from .models import ACTIVE_STATUSES, PUBLISHED, ROLE_MANAGER, ROLES, Document, ReminderLog, User
from .workflow import doc_link, pending_users

log = logging.getLogger(__name__)

EXPECTED = {
    "Draft": "finish the draft and submit it",
    "Review": "review it and click 'Review done'",
    "Revision": "process the comments and submit it for approval",
    "Approval": "approve or reject it",
}


def maybe_run(db: Session, now: datetime | None = None) -> bool:
    """Called every few minutes: runs the reminders once a day, at or after the configured hour."""
    now = now or datetime.now()
    site = site_settings.get_all(db)
    today = now.date().isoformat()
    if site.get("last_reminder_run") == today or now.hour < int(site.get("reminder_hour", 8)):
        return False
    run(db, now.date())
    site_settings.set_many(db, {"last_reminder_run": today})
    return True


def run(db: Session, today: date | None = None) -> int:
    """Queues today's reminders. Returns the number of e-mails queued."""
    today = today or date.today()
    site = site_settings.get_all(db)
    before = int(site.get("reminder_before_days") or 0)
    repeat = int(site.get("overdue_repeat_days") or 0)

    per_user: dict[int, list[tuple[Document, str]]] = defaultdict(list)
    users: dict[int, User] = {}
    overdue_docs: list[Document] = []

    docs = db.query(Document).filter(Document.status.in_(ACTIVE_STATUSES), Document.stage_due.isnot(None)).all()
    for doc in docs:
        days_left = (doc.stage_due - today).days
        if days_left < 0:
            overdue_docs.append(doc)
            kind = "overdue"
        elif before and days_left <= before:
            kind = "soon"
        else:
            continue
        for user in pending_users(doc):
            if not user.active or not _should_send(db, doc, user, kind, today, repeat):
                continue
            db.add(ReminderLog(document_id=doc.id, user_id=user.id, kind=kind,
                               stage_started_at=doc.stage_started_at, sent_on=today))
            per_user[user.id].append((doc, kind))
            users[user.id] = user
            if user.is_absent and user.deputy and user.deputy.active:   # the deputy is reminded too
                per_user[user.deputy.id].append((doc, kind))
                users[user.deputy.id] = user.deputy

    # Periodic review of published documents: owner warned before, then while overdue.
    review_days = int(site.get("review_reminder_days") or 0)
    reviews_due: list[Document] = []
    for doc in db.query(Document).filter(Document.status == PUBLISHED, Document.next_review.isnot(None)).all():
        days_left = (doc.next_review - today).days
        if days_left > review_days:
            continue
        reviews_due.append(doc)
        kind = "review"
        if _should_send(db, doc, doc.owner, kind, today, repeat if days_left < 0 else 0) and doc.owner.active:
            db.add(ReminderLog(document_id=doc.id, user_id=doc.owner_id, kind=kind,
                               stage_started_at=doc.stage_started_at, sent_on=today))
            per_user[doc.owner_id].append((doc, kind))
            users[doc.owner_id] = doc.owner

    queued = 0
    for user_id, items in per_user.items():
        late = sum(1 for _, k in items if k == "overdue")
        subject = (f"Reminder: {late} overdue document(s)" if late
                   else f"Reminder: {len(items)} document(s) due soon")
        mailer.queue(db, users[user_id].email, subject, "reminder.html", site, user=users[user_id],
                     items=[_item(doc, kind, today) for doc, kind in items])
        queued += 1

    if site.get("manager_digest") and (overdue_docs or reviews_due):
        managers = [u for u in db.query(User).filter(User.active.is_(True)).all()
                    if ROLES.index(u.role) >= ROLES.index(ROLE_MANAGER)]
        for manager in managers:
            # An Audit document appears only in the digest of the managers who belong to its audience.
            items = [_item(doc, "overdue", today, with_people=True) for doc in overdue_docs
                     if not doc.is_strict or doc.in_audience(manager)]
            items += [_item(doc, "review", today, with_people=True) for doc in reviews_due
                      if not doc.is_strict or doc.in_audience(manager)]
            if not items:
                continue
            mailer.queue(db, manager.email, f"Overdue stages and reviews due: {len(items)}", "reminder.html", site,
                         user=manager, items=items, manager=True)
            queued += 1
    log.info("Reminders: %s e-mail(s) queued", queued)
    return queued


def _should_send(db: Session, doc: Document, user: User, kind: str, today: date, repeat: int) -> bool:
    previous = (db.query(ReminderLog)
                .filter_by(document_id=doc.id, user_id=user.id, kind=kind, stage_started_at=doc.stage_started_at)
                .order_by(ReminderLog.sent_on.desc()).first())
    if previous is None:
        return True
    if kind == "soon" or repeat <= 0:
        return False
    return (today - previous.sent_on).days >= repeat


def _item(doc: Document, kind: str, today: date, with_people: bool = False) -> dict:
    due = doc.next_review if kind == "review" else doc.stage_due
    return {
        "ref": doc.ref, "title": doc.title, "status": doc.status, "link": doc_link(doc),
        "due": due.isoformat(), "kind": "overdue" if kind == "review" and due < today else kind,
        "days": abs((due - today).days), "review": kind == "review",
        "expected": ("review it and ask a Document Manager to start a new revision (or confirm it unchanged)"
                     if kind == "review" else EXPECTED.get(doc.status, "act")),
        "waiting_for": (doc.owner.display if kind == "review" else
                        ", ".join(u.display for u in pending_users(doc))) if with_people else "",
    }
