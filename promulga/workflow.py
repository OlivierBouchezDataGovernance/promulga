"""The document life cycle. The flow is fixed; only its deadlines are configurable.

    Draft --submit--> Review --all reviewers done--> Revision --submit--> Approval --all approve--> Published
      ^                (or a manager closes it)        ^                      |                        |
      |                                                +------ one reject ----+                        |
      +------------------- a manager starts a new minor/major revision <-------------------------------+
                                                                   (Published --retire--> Retired)

- Without reviewers, "submit" in Draft goes straight to Approval.
- Editing is check-out / check-in: the owner downloads the working file,
  edits it in Word (or any editor) and uploads it again as a new revision.
- Reviewers write their remarks as comments in the application (they may
  also attach an annotated file); the owner replies and resolves them.
- At publication the last revision is converted to PDF, stamped with the
  reference, version and date, and becomes immutable.

Every function here:
  * checks that `actor` may do it in the current stage (WorkflowError otherwise),
  * changes the document, writes the audit Event, queues the e-mails,
  * does NOT commit: the caller (one HTTP request) commits or rolls back,
    so a failure (e.g. PDF conversion) leaves nothing half done.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from sqlalchemy.orm import Session

from . import mailer, pdf, site_settings, storage
from .accounts import participant
from .config import get_config
from .models import (
    ACTIVE_STATUSES, APPROVAL, APPROVER, DRAFT, PUBLISHED, RETIRED, REVIEW, REVIEWER, REVISION,
    ROLE_MANAGER, Assignment, Comment, Document, Event, Publication, Revision, Term, User, utcnow,
)


class WorkflowError(Exception):
    """An action that is not allowed; the message is shown to the user."""


# ---------------------------------------------------------------------------
# Who may do what
# ---------------------------------------------------------------------------

def is_manager(user: User) -> bool:
    return user.has_role(ROLE_MANAGER)


def is_participant(doc: Document, user: User) -> bool:
    return doc.owner_id == user.id or any(a.user_id == user.id for a in doc.assignments)


def can_see(doc: Document, user: User) -> bool:
    """Access to the working area of a document (files, comments, history)."""
    return is_manager(user) or is_participant(doc, user)


def pending_users(doc: Document) -> list[User]:
    """People who still have to act in the current stage."""
    if doc.status in (DRAFT, REVISION):
        return [doc.owner]
    if doc.status == REVIEW:
        return [a.user for a in doc.reviewers if a.acted_at is None]
    if doc.status == APPROVAL:
        return [a.user for a in doc.approvers if a.acted_at is None]
    return []


def allowed_actions(doc: Document, user: User) -> set[str]:
    """Actions the interface should offer to `user` (the functions re-check anyway)."""
    actions: set[str] = set()
    owner_or_manager = doc.owner_id == user.id or is_manager(user)
    if doc.status in (DRAFT, REVISION) and owner_or_manager:
        actions |= {"checkin", "submit"}
        if not doc.checked_out_by_id:
            actions.add("checkout")
    if doc.checked_out_by_id and (doc.checked_out_by_id == user.id or is_manager(user)):
        actions.add("release")
    if doc.status in ACTIVE_STATUSES and can_see(doc, user):
        actions.add("comment")
    mine = [a for a in doc.assignments if a.user_id == user.id and a.acted_at is None]
    if doc.status == REVIEW and any(a.role == REVIEWER for a in mine):
        actions.add("review_done")
    if doc.status == APPROVAL and any(a.role == APPROVER for a in mine):
        actions |= {"approve", "reject"}
    if is_manager(user):
        actions.add("edit")
        if doc.status == REVIEW:
            actions.add("close_review")
        if doc.status in (PUBLISHED, RETIRED):
            actions.add("new_revision")
        if doc.status == PUBLISHED:
            actions.add("retire")
    return actions


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkflowError(message)


def _require_status(doc: Document, *statuses: str) -> None:
    _require(doc.status in statuses,
             f"{doc.ref} is in stage {doc.status}; this action needs {' or '.join(statuses)}. Refresh the page.")


def _require_owner_or_manager(doc: Document, actor: User) -> None:
    _require(doc.owner_id == actor.id or is_manager(actor),
             f"Only the owner of {doc.ref} or a Document Manager can do this.")


def _require_manager(actor: User) -> None:
    _require(is_manager(actor), "Only a Document Manager can do this.")


# ---------------------------------------------------------------------------
# Creation and metadata
# ---------------------------------------------------------------------------

def create_document(db: Session, actor: User, *, title: str, doc_type: str, summary: str, owner_email: str,
                    reviewer_emails: list[str], approver_emails: list[str], term_ids: list[int],
                    file: tuple[str, bytes] | None) -> Document:
    _require_manager(actor)
    site = site_settings.get_all(db)
    title = (title or "").strip()
    _require(bool(title), "The title is required.")
    _require(len(approver_emails) > 0, "At least one approver is required.")
    prefix = site_settings.doc_type_prefix(site, doc_type)

    owner = participant(db, owner_email, actor, site)
    doc = Document(ref=_next_ref(db, prefix), title=title, summary=(summary or "").strip(), doc_type=doc_type,
                   owner=owner, status=DRAFT, next_version="1.0",
                   stage_due=_due(site, DRAFT), stage_started_at=utcnow())
    db.add(doc)
    doc.terms = db.query(Term).filter(Term.id.in_(term_ids or [0])).all()
    _set_assignments(db, doc, reviewer_emails, approver_emails, actor, site)
    db.flush()

    if file is None and site.get("template_asset"):
        path = storage.asset_path(site["template_asset"])
        if path:
            file = (f"{doc.ref} - {title}{path.suffix}", path.read_bytes())
    if file is not None:
        _store_revision(db, actor, doc, file[0], file[1], "Initial file")

    _log(db, doc, actor, "Created", "", DRAFT)
    _notify_stage(db, doc, site)
    return doc


def update_metadata(db: Session, actor: User, doc: Document, *, title: str, summary: str,
                    term_ids: list[int]) -> None:
    _require_manager(actor)
    title = (title or "").strip()
    _require(bool(title), "The title is required.")
    before = f"title '{doc.title}'"
    doc.title = title
    doc.summary = (summary or "").strip()
    doc.terms = db.query(Term).filter(Term.id.in_(term_ids or [0])).all()
    _log(db, doc, actor, "Metadata changed", doc.status, doc.status, f"Before: {before}")


def update_participants(db: Session, actor: User, doc: Document, *, owner_email: str,
                        reviewer_emails: list[str], approver_emails: list[str]) -> None:
    _require_manager(actor)
    _require(len(approver_emails) > 0, "At least one approver is required.")
    site = site_settings.get_all(db)
    before = _participants_text(doc)
    new_owner = participant(db, owner_email, actor, site)
    doc.owner_id = new_owner.id
    _set_assignments(db, doc, reviewer_emails, approver_emails, actor, site)
    db.flush()
    db.refresh(doc)
    _log(db, doc, actor, "Participants changed", doc.status, doc.status, f"Before: {before}")
    if doc.status == REVIEW and not doc.reviewers:
        _move(db, doc, REVISION, actor, "Review skipped (no reviewers left)", site=site)
    else:
        _advance_if_complete(db, doc, actor, site)


def _set_assignments(db: Session, doc: Document, reviewer_emails: list[str], approver_emails: list[str],
                     actor: User, site: dict) -> None:
    """Replaces reviewers/approvers, keeping the state of people who stay."""
    wanted = [(REVIEWER, e) for e in reviewer_emails] + [(APPROVER, e) for e in approver_emails]
    keep: list[Assignment] = []
    for role, email in wanted:
        user = participant(db, email, actor, site)
        existing = next((a for a in doc.assignments if a.role == role and a.user_id == user.id), None)
        keep.append(existing or Assignment(role=role, user=user))
    doc.assignments[:] = keep


def _participants_text(doc: Document) -> str:
    return (f"owner {doc.owner.email}; reviewers {', '.join(a.user.email for a in doc.reviewers) or '-'}; "
            f"approvers {', '.join(a.user.email for a in doc.approvers) or '-'}")


# ---------------------------------------------------------------------------
# Check-out / check-in
# ---------------------------------------------------------------------------

def check_out(db: Session, actor: User, doc: Document) -> None:
    _require_status(doc, DRAFT, REVISION)
    _require_owner_or_manager(doc, actor)
    _require(doc.checked_out_by_id in (None, actor.id), f"{doc.ref} is already checked out by "
             f"{doc.checked_out_by.display if doc.checked_out_by else 'someone'}.")
    doc.checked_out_by_id = actor.id
    doc.checked_out_at = utcnow()
    _log(db, doc, actor, "Checked out", doc.status, doc.status)


def release_check_out(db: Session, actor: User, doc: Document) -> None:
    _require(doc.checked_out_by_id is not None, "The document is not checked out.")
    _require(doc.checked_out_by_id == actor.id or is_manager(actor),
             "Only the person who checked it out or a Document Manager can release it.")
    doc.checked_out_by_id = None
    doc.checked_out_at = None
    _log(db, doc, actor, "Check-out released", doc.status, doc.status)


def check_in(db: Session, actor: User, doc: Document, filename: str, data: bytes, note: str = "") -> Revision:
    _require_status(doc, DRAFT, REVISION)
    _require_owner_or_manager(doc, actor)
    _require(doc.checked_out_by_id in (None, actor.id),
             f"{doc.ref} is checked out by {doc.checked_out_by.display if doc.checked_out_by else 'someone'}.")
    revision = _store_revision(db, actor, doc, filename, data, note)
    doc.checked_out_by_id = None
    doc.checked_out_at = None
    _log(db, doc, actor, f"Checked in revision {revision.number}", doc.status, doc.status, note)
    return revision


def _store_revision(db: Session, actor: User, doc: Document, filename: str, data: bytes, note: str) -> Revision:
    filename = storage.safe_filename(filename)
    _require(storage.extension(filename) in storage.ALLOWED_EXTENSIONS,
             f"File type not accepted. Allowed: {', '.join(sorted(storage.ALLOWED_EXTENSIONS))}.")
    max_bytes = get_config().max_upload_mb * 1024 * 1024
    _require(0 < len(data) <= max_bytes, f"The file is empty or larger than {get_config().max_upload_mb} MB.")
    path, digest = storage.save_bytes(str(doc.id), filename, data)
    number = max((r.number for r in doc.revisions), default=0) + 1
    revision = Revision(document=doc, number=number, filename=filename,
                        content_type=_content_type(filename), storage_path=path, size=len(data), sha256=digest,
                        note=(note or "").strip(), uploaded_by=actor, status_at_upload=doc.status)
    db.add(revision)
    db.flush()
    db.refresh(doc)
    return revision


def _content_type(filename: str) -> str:
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".odt": "application/vnd.oasis.opendocument.text",
        ".rtf": "application/rtf",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".md": "text/markdown",
        ".txt": "text/plain",
    }.get(storage.extension(filename), "application/octet-stream")


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

def add_comment(db: Session, actor: User, doc: Document, body: str, parent_id: int | None = None,
                attachment: tuple[str, bytes] | None = None) -> Comment:
    _require_status(doc, *ACTIVE_STATUSES)
    _require(can_see(doc, actor), "You do not take part in this document.")
    body = (body or "").strip()
    _require(bool(body) or attachment is not None, "The comment is empty.")
    _require(len(body) <= 10_000, "The comment is too long (10,000 characters maximum).")
    parent = None
    if parent_id:
        parent = db.get(Comment, parent_id)
        _require(parent is not None and parent.document_id == doc.id, "Unknown comment.")
    comment = Comment(document=doc, author=actor, body=body, stage=doc.status, version=doc.next_version,
                      revision=doc.latest_revision, parent_id=parent.id if parent else None)
    if attachment is not None:
        name = storage.safe_filename(attachment[0])
        _require(storage.extension(name) in storage.ALLOWED_EXTENSIONS, "File type not accepted.")
        _require(0 < len(attachment[1]) <= get_config().max_upload_mb * 1024 * 1024,
                 f"The file is empty or larger than {get_config().max_upload_mb} MB.")
        comment.attachment_name = name
        comment.attachment_path, _ = storage.save_bytes(f"{doc.id}/comments", name, attachment[1])
    db.add(comment)
    db.flush()
    return comment


def set_comment_resolved(db: Session, actor: User, doc: Document, comment: Comment, resolved: bool) -> None:
    _require(comment.document_id == doc.id, "Unknown comment.")
    _require(doc.owner_id == actor.id or comment.author_id == actor.id or is_manager(actor),
             "Only the owner, the author of the comment or a Document Manager can do this.")
    comment.resolved = resolved


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------

def submit(db: Session, actor: User, doc: Document) -> None:
    """Draft -> Review (or Approval without reviewers); Revision -> Approval."""
    _require_status(doc, DRAFT, REVISION)
    _require_owner_or_manager(doc, actor)
    _require(doc.latest_revision is not None, "Check in a file before submitting.")
    _require(doc.checked_out_by_id is None, "The document is still checked out: check in your file first "
             "(or release the check-out).")
    if doc.status == DRAFT and doc.reviewers:
        _move(db, doc, REVIEW, actor, "Submitted for review")
    elif doc.status == DRAFT:
        _move(db, doc, APPROVAL, actor, "Submitted for approval (no reviewers)")
    else:
        _move(db, doc, APPROVAL, actor, "Submitted for approval")


def review_done(db: Session, actor: User, doc: Document, note: str = "") -> None:
    _require_status(doc, REVIEW)
    mine = next((a for a in doc.reviewers if a.user_id == actor.id), None)
    _require(mine is not None, f"You are not a reviewer of {doc.ref}.")
    _require(mine.acted_at is None, "You already marked your review as done.")
    mine.acted_at = utcnow()
    _log(db, doc, actor, "Review done", REVIEW, REVIEW, note)
    _advance_if_complete(db, doc, actor)


def close_review(db: Session, actor: User, doc: Document, note: str = "") -> None:
    _require_manager(actor)
    _require_status(doc, REVIEW)
    missing = ", ".join(u.email for u in pending_users(doc))
    detail = " ".join(p for p in [note.strip(), f"(no response from {missing})" if missing else ""] if p)
    _move(db, doc, REVISION, actor, "Review closed by manager", detail)


def approve(db: Session, actor: User, doc: Document, note: str = "") -> None:
    _require_status(doc, APPROVAL)
    mine = next((a for a in doc.approvers if a.user_id == actor.id), None)
    _require(mine is not None, f"You are not an approver of {doc.ref}.")
    _require(mine.acted_at is None, "You already approved this version.")
    mine.acted_at = utcnow()
    _log(db, doc, actor, "Approved", APPROVAL, APPROVAL, note)
    _advance_if_complete(db, doc, actor)


def reject(db: Session, actor: User, doc: Document, note: str) -> None:
    _require_status(doc, APPROVAL)
    _require(any(a.user_id == actor.id for a in doc.approvers), f"You are not an approver of {doc.ref}.")
    _require(bool((note or "").strip()), "Please explain why you reject the document.")
    _move(db, doc, REVISION, actor, "Rejected", note.strip())


def start_revision(db: Session, actor: User, doc: Document, bump: str, note: str = "") -> None:
    """Published/Retired -> Draft, targeting the next minor or major version."""
    _require_manager(actor)
    _require_status(doc, PUBLISHED, RETIRED)
    doc.next_version = bump_version(doc.current_version, bump)
    _move(db, doc, DRAFT, actor, f"New {'major' if bump == 'major' else 'minor'} revision started",
          " ".join(p for p in [f"Target version {doc.next_version}.", note.strip()] if p))


def retire(db: Session, actor: User, doc: Document, note: str = "") -> None:
    """Withdraws a published document from the portal (its history is kept)."""
    _require_manager(actor)
    _require_status(doc, PUBLISHED)
    _require(bool((note or "").strip()), "Please give the reason of the withdrawal.")
    _move(db, doc, RETIRED, actor, "Retired", note.strip(), notify=False)


def _move(db: Session, doc: Document, status: str, actor: User | None, action: str, detail: str = "",
          site: dict | None = None, notify: bool = True) -> None:
    site = site or site_settings.get_all(db)
    previous = doc.status
    doc.status = status
    doc.stage_due = _due(site, status)
    doc.stage_started_at = utcnow()
    for a in doc.assignments:
        if (status == REVIEW and a.role == REVIEWER) or (status in (APPROVAL, REVISION) and a.role == APPROVER):
            a.acted_at = None
    if status in (REVIEW, APPROVAL, PUBLISHED, RETIRED):
        doc.checked_out_by_id = None
        doc.checked_out_at = None
    _log(db, doc, actor, action, previous, status, detail)
    if notify:
        _notify_stage(db, doc, site, action=action, actor=actor, detail=detail)


def _advance_if_complete(db: Session, doc: Document, actor: User, site: dict | None = None) -> None:
    if pending_users(doc):
        return
    if doc.status == REVIEW:
        _move(db, doc, REVISION, actor, "All reviews done", site=site)
    elif doc.status == APPROVAL:
        _publish(db, doc, actor, site or site_settings.get_all(db))


def _publish(db: Session, doc: Document, actor: User, site: dict) -> Publication:
    source = doc.latest_revision
    _require(source is not None, "There is no file to publish.")
    version = normalise_version(doc.next_version) or "1.0"
    today = date.today()
    try:
        raw_pdf = pdf.to_pdf(source.filename, storage.read_bytes(source.storage_path))
    except pdf.ConversionError as exc:
        raise WorkflowError(f"Publication failed: {exc} Your approval was not recorded; try again later "
                            "or ask the owner to check in a PDF.") from exc
    label = f"{doc.ref} - {doc.title} - Version {version} - Published {today.isoformat()} - {site['site_name']}"
    final_pdf = pdf.stamp(raw_pdf, label)
    path, digest = storage.save_bytes(f"{doc.id}/published", f"{doc.ref}-v{version}.pdf", final_pdf)
    publication = Publication(
        document=doc, version=version, published_at=today, pdf_path=path, sha256=digest,
        source_revision_id=source.id, title=doc.title, text=pdf.extract_text(final_pdf),
        approved_by=", ".join(a.user.display for a in doc.approvers),
    )
    db.add(publication)
    doc.current_version = version
    doc.published_at = today
    _move(db, doc, PUBLISHED, actor, f"Published v{version}", f"sha256 {digest}", site=site)
    db.flush()
    return publication


# ---------------------------------------------------------------------------
# Versions, references, deadlines
# ---------------------------------------------------------------------------

def normalise_version(v: str | None) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    parts = v.split(".")
    return f"{int(parts[0])}.{int(parts[1]) if len(parts) > 1 and parts[1] else 0}"


def bump_version(current: str, bump: str) -> str:
    major, minor = (int(x) for x in (normalise_version(current) or "0.0").split("."))
    return f"{major + 1}.0" if bump == "major" else f"{major}.{minor + 1}"


def _next_ref(db: Session, prefix: str) -> str:
    highest = 0
    for (ref,) in db.query(Document.ref).filter(Document.ref.like(f"{prefix}-%")).all():
        m = re.match(rf"^{re.escape(prefix)}-(\d+)$", ref)
        if m:
            highest = max(highest, int(m.group(1)))
    return f"{prefix}-{highest + 1:04d}"


def _due(site: dict, status: str) -> date | None:
    days = site["stage_days"].get(status)
    return date.today() + timedelta(days=int(days)) if days else None


# ---------------------------------------------------------------------------
# Audit log and notifications
# ---------------------------------------------------------------------------

def _version_label(doc: Document) -> str:
    return doc.current_version if doc.status in (PUBLISHED, RETIRED) else f"{doc.next_version} (draft)"


def _log(db: Session, doc: Document | None, actor: User | None, action: str, from_status: str, to_status: str,
         detail: str = "") -> None:
    db.add(Event(document_id=doc.id if doc else None, document_ref=doc.ref if doc else "",
                 actor_id=actor.id if actor else None, actor_label=actor.email if actor else "system",
                 action=action, from_status=from_status, to_status=to_status,
                 version=_version_label(doc) if doc else "", detail=(detail or "")[:5000]))


def log_admin(db: Session, actor: User, action: str, detail: str = "") -> None:
    """Audit line for administration actions not linked to a document."""
    _log(db, None, actor, action, "", "", detail)


def doc_link(doc: Document) -> str:
    return f"{get_config().base_url}/admin/documents/{doc.id}"


def _notify_stage(db: Session, doc: Document, site: dict, action: str = "", actor: User | None = None,
                  detail: str = "") -> None:
    due = doc.stage_due.isoformat() if doc.stage_due else ""
    link = ("Open the document", doc_link(doc))
    comment = detail if detail and action in ("Rejected", "Review closed by manager") else ""
    who = actor.display if actor else "The system"

    def send(user: User, subject: str, lines: list[str]):
        mailer.queue(db, user.email, subject, "notice.html", site, heading=f"{doc.ref} - {doc.title}",
                     lines=lines, button=link, comment=comment, comment_author=who)

    if doc.status == DRAFT:
        send(doc.owner, f"Please draft {doc.ref} (due {due})", [
            f"You are the owner of this {doc.doc_type.lower()} (version {doc.next_version} in preparation).",
            "Download the working file (check-out), edit it, and upload it again (check-in). "
            "When it is ready, click Submit.", f"Due date: {due}."])
    elif doc.status == REVIEW:
        for user in pending_users(doc):
            send(user, f"Please review {doc.ref} (due {due})", [
                f"{doc.owner.display} asks you to review this document.",
                "Download the current file, then write your remarks as comments on the document page "
                "(you may also attach an annotated copy). Click 'Review done' when finished.",
                f"Due date: {due}."])
    elif doc.status == REVISION:
        intro = ("The document was rejected during approval and comes back to you."
                 if action == "Rejected" else "The review is finished.")
        send(doc.owner, f"Please revise {doc.ref} (due {due})", [
            intro, "Answer and resolve the comments, check in the corrected file, then submit it for approval.",
            f"Due date: {due}."])
    elif doc.status == APPROVAL:
        for user in pending_users(doc):
            send(user, f"Please approve {doc.ref} (due {due})", [
                f"Version {doc.next_version} is submitted for your approval.",
                "Read the document, then approve it, or reject it with a comment.", f"Due date: {due}."])
    elif doc.status == PUBLISHED and site.get("notify_on_publish", True):
        people = {doc.owner.id: doc.owner}
        for a in doc.assignments:
            people[a.user.id] = a.user
        public = f"{get_config().base_url}/d/{doc.ref}"
        for user in people.values():
            mailer.queue(db, user.email, f"{doc.ref} version {doc.current_version} is published", "notice.html",
                         site, heading=f"{doc.ref} - {doc.title}",
                         lines=[f"Version {doc.current_version} is approved and published on "
                                f"{doc.published_at.isoformat()}. Thank you for your contribution."],
                         button=("View the published version", public))
