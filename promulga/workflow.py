"""The document life cycle. The flow is fixed; only its deadlines are configurable.

    Draft --submit--> Review --all reviewers done--> Revision --submit--> Approval --all approve--> Published
      ^                (or a manager closes it)        ^                      |                        |
      |                                                +------ one reject ----+                        |
      +------------------- a manager starts a new minor/major revision <-------------------------------+
                                                                   (Published --retire--> Retired)

- Without reviewers, "submit" in Draft goes straight to Approval.
- Editing is check-out / check-in: the owner downloads the working file,
  edits it in Word (or any editor) and uploads it again as a new revision.
- Who sees and does what, by stage:
    Draft     only the owner (and Document Managers) see the document;
    Review    reviewers comment, nobody edits; the Word file is not handed out,
              only a PDF "review copy";
    Revision  the owner edits and decides on every remark (accepted, or rejected
              with a justification); the others may only reply to existing remarks;
              comments written in the Word file are imported when the review ends;
    Approval  frozen: a PDF is built from the submitted revision and the approvers
              approve that PDF; the same revision is published.
  There is no second review round: Revision always goes to Approval.
- Every document hangs under a parent (Charter > Policy > Standard, Guideline,
  Procedure ...), except the Charter. See PARENT_TYPES.
- Annexes are never reviewed nor approved: French and Dutch translations of a
  published English version, and external references (legal texts, norms).
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

import json

from sqlalchemy import func

from . import docx_meta, mailer, naming, pdf, site_settings, storage
from .accounts import participant
from .config import get_config
from .models import (
    ACCEPTED, ACTIVE_STATUSES, APPROVAL, APPROVER, CLASSIFICATIONS, DRAFT, PUBLISHED, REJECTED, RETIRED, REVIEW,
    REVIEWER, REVISION, ROLE_ADMIN, ROLE_MANAGER, STRICT_CLASSIFICATIONS, TRANSLATION_LANGUAGES, Acknowledgment, Assignment, Comment, Document, Event,
    ExternalReference, Publication, Revision, StageLog, Term, Translation, User, WebhookDelivery, utcnow,
)

# Allowed parent types, by type code (the prefix of the type in the site settings).
# [] = top of the hierarchy (no parent). A type that is not listed may hang anywhere, or nowhere.
# The rule only applies when the site defines at least one of the allowed parent types.
PARENT_TYPES: dict[str, list[str]] = {
    "CHT": [],
    "POL": ["CHT"],
    "STD": ["POL"], "GDL": ["POL"], "PRO": ["POL"], "PRC": ["POL"],
    "CTR": ["STD"],
    "WIN": ["PRO", "PRC"],
    "METH": ["POL"], "TPL": ["POL"],
}


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
    """Access to the working area of a document (files, comments, history).
    A Draft belongs to its owner: reviewers and approvers see it from Review on.
    An Audit document is closed to everybody outside its audience, Document Managers and deputies included."""
    if doc.is_strict and not doc.in_audience(user):
        return False
    if is_manager(user) or doc.owner_id == user.id:
        return True
    if doc.status == DRAFT:
        return doc.owner.is_absent and doc.owner.deputy_id == user.id
    if is_participant(doc, user):
        return True
    return any(a.user.is_absent and a.user.deputy_id == user.id for a in doc.assignments)


def handles_file(doc: Document, user: User) -> bool:
    """Whether `user` may get the editable working file now (Draft and Revision, owner or manager).
    In Review and Approval everybody gets a PDF copy instead."""
    return doc.status in (DRAFT, REVISION) and (doc.owner_id == user.id or is_manager(user))


def pending_users(doc: Document) -> list[User]:
    """People who still have to act in the current stage."""
    if doc.status in (DRAFT, REVISION):
        return [doc.owner]
    if doc.status == REVIEW:
        return [a.user for a in doc.reviewers if a.acted_at is None]
    if doc.status == APPROVAL:
        return [a.user for a in doc.approvers if a.acted_at is None]
    return []


def acting_assignment(doc: Document, actor: User, role: str) -> Assignment | None:
    """The assignment `actor` may act on: their own, or that of an absent person whose deputy they are."""
    own = next((a for a in doc.people(role) if a.user_id == actor.id and a.acted_at is None), None)
    if own:
        return own
    return next((a for a in doc.people(role) if a.acted_at is None and a.user.is_absent
                 and a.user.deputy_id == actor.id), None)


def segregation_warnings(doc: Document) -> list[str]:
    """Segregation of duties: the owner approves their own document (it is theirs), but never alone."""
    out = []
    if doc.approvers and not independent_approvers(doc):
        out.append("The owner is the only approver: add at least one other approver "
                   "(the owner approves the document, but never alone).")
    if any(a.user_id == doc.owner_id for a in doc.reviewers):
        out.append("The owner is also a reviewer of their own document.")
    return out


def independent_approvers(doc: Document) -> list[Assignment]:
    return [a for a in doc.approvers if a.user_id != doc.owner_id]


def default_approvers(db: Session, site: dict) -> list[str]:
    """E-mails proposed as approvers of a new document, besides its owner: the setting, else the administrators."""
    emails = [e for e in site.get("default_approvers") or [] if e]
    if emails:
        return emails
    return [u.email for u in db.query(User).filter(User.active.is_(True), User.role == ROLE_ADMIN)
            .order_by(User.email).all()]


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
        actions.add("reply")
        if doc.status != REVISION or owner_or_manager:
            actions.add("comment")
    if doc.status == REVISION and owner_or_manager:
        actions.add("decide")
    if owner_or_manager:
        actions.add("annex")
    if doc.status == REVIEW and acting_assignment(doc, user, REVIEWER):
        actions.add("review_done")
    if doc.status == APPROVAL and acting_assignment(doc, user, APPROVER):
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
                    file: tuple[str, bytes] | None, review_months: int | None = None,
                    ack_required: bool = False, classification: str = "internal",
                    keywords: str = "", readers: list[str] | None = None,
                    parent_id: int | None = None, number: int | None = None) -> Document:
    """`number` reserves a given {ID} (e.g. to import a set whose references are already written in the
    documents); by default the next free number is used."""
    _require_manager(actor)
    site = site_settings.get_all(db)
    title = (title or "").strip()
    _require(bool(title), "The title is required.")
    _require(len(approver_emails) > 0, "At least one approver is required.")
    prefix = site_settings.doc_type_prefix(site, doc_type)
    _require(classification in CLASSIFICATIONS, "Unknown classification.")
    _check_summary(summary)
    parent = _check_parent(db, site, doc_type, parent_id, None)

    owner = participant(db, owner_email, actor, site)
    if number is not None:
        _require(number > 0 and db.query(Document).filter(Document.number == number).count() == 0,
                 f"The document number {number} is already used.")
    else:
        number = (db.query(func.max(Document.number)).scalar() or 0) + 1
    doc = Document(number=number, ref=f"tmp-{number}", title=title, summary=(summary or "").strip(),
                   doc_type=doc_type, owner=owner, status=DRAFT, next_version="1.0",
                   change_note="Initial version", stage_due=_due(site, DRAFT), stage_started_at=utcnow(),
                   review_months=review_months, ack_required=ack_required, classification=classification,
                   keywords=_clean_keywords(keywords), readers=", ".join(readers or []), parent=parent)
    db.add(doc)
    doc.terms = db.query(Term).filter(Term.id.in_(term_ids or [0])).all()
    doc.ref = naming.build_ref(doc, prefix)
    # References made by Promulga 0.1 were numbered per type (POL-0007 may exist): skip used ones.
    for _ in range(1000):
        if db.query(Document).filter(Document.ref == doc.ref, Document.id != doc.id).count() == 0:
            break
        doc.number += 1
        doc.ref = naming.build_ref(doc, prefix)
    else:
        raise WorkflowError(f"No free reference for {prefix}: check REF_PATTERN, it must contain {{ID}}.")
    _set_assignments(db, doc, reviewer_emails, approver_emails, actor, site)
    db.flush()
    db.refresh(doc)
    _check_audience(doc, actor)

    template = (site.get("type_templates") or {}).get(doc_type) or site.get("template_asset")
    if file is None and template:
        path = storage.asset_path(template)
        if path:
            file = (f"template{path.suffix}", path.read_bytes())
    if file is None:   # no file, no template: a blank Word document with its cover page
        file = ("document.docx", docx_meta.new_template(naming.values(doc, site=site), naming.LABELS,
                                                        logo=cover_logo(site)))
    _store_revision(db, actor, doc, file[0], file[1], "Initial file")

    _log(db, doc, actor, "Created", "", DRAFT)
    _open_stage(db, doc)
    _notify_stage(db, doc, site)
    return doc


def update_metadata(db: Session, actor: User, doc: Document, *, title: str, summary: str,
                    term_ids: list[int], review_months: int | None = None, ack_required: bool | None = None,
                    classification: str | None = None, keywords: str | None = None,
                    readers: list[str] | None = None, parent_id: int | None | bool = False) -> None:
    """`parent_id` False = leave the parent unchanged; None = no parent; an id = that parent."""
    _require_manager(actor)
    title = (title or "").strip()
    _require(bool(title), "The title is required.")
    _check_summary(summary)
    before = f"title '{doc.title}'"
    if parent_id is not False:
        parent = _check_parent(db, site_settings.get_all(db), doc.doc_type, parent_id, doc)
        if (parent.id if parent else None) != doc.parent_id:
            before += f", parent {doc.parent.ref if doc.parent else 'none'}"
            doc.parent = parent
    doc.title = title
    doc.summary = (summary or "").strip()
    doc.terms = db.query(Term).filter(Term.id.in_(term_ids or [0])).all()
    doc.review_months = review_months
    if ack_required is not None:
        doc.ack_required = ack_required
    if classification is not None:
        _require(classification in CLASSIFICATIONS, "Unknown classification.")
        _require(classification not in STRICT_CLASSIFICATIONS or not doc.children,
                 "This document has children: an Audit document cannot be the parent of other documents.")
        if classification != doc.classification:
            before += f", classification {doc.classification_label}"
        doc.classification = classification
    if keywords is not None:
        doc.keywords = _clean_keywords(keywords)
    if readers is not None:
        doc.readers = ", ".join(readers)
    _check_audience(doc, actor)
    _log(db, doc, actor, "Metadata changed", doc.status, doc.status, f"Before: {before}")


def _check_audience(doc: Document, actor: User) -> None:
    """An Audit document is seen by its audience only: whoever sets it up must stay in it."""
    _require(not doc.is_strict or doc.in_audience(actor),
             "An Audit document is visible to its audience only (owner, reviewers, approvers, named readers): "
             "add yourself to it, or you will lose access to the document.")


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
    _check_audience(doc, actor)
    _log(db, doc, actor, "Participants changed", doc.status, doc.status, f"Before: {before}")
    if doc.status == REVIEW and not doc.reviewers:
        _move(db, doc, REVISION, actor, "Review skipped (no reviewers left)", site=site)
    else:
        _advance_if_complete(db, doc, actor, site)


# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------

def _type_codes(site: dict) -> dict[str, str]:
    """Document type name -> type code (upper-case prefix)."""
    return {t["name"]: (t.get("prefix") or "").upper() for t in site["doc_types"]}


def parent_types(site: dict, doc_type: str) -> list[str] | None:
    """Names of the types a `doc_type` document may hang under; [] = top of the hierarchy, None = free."""
    codes = _type_codes(site)
    allowed = PARENT_TYPES.get(codes.get(doc_type, ""))
    if allowed is None:
        return None
    names = [name for name, code in codes.items() if code in allowed]
    if allowed and not names:      # the site does not use the parent type (e.g. no Charter): free
        return None
    return names


def parent_candidates(db: Session, site: dict, doc_type: str, doc: Document | None = None) -> list[Document]:
    """Documents that may be the parent of a `doc_type` document (or of `doc`, without its own subtree)."""
    allowed = parent_types(site, doc_type)
    if allowed == []:
        return []
    query = db.query(Document).filter(Document.status != RETIRED)
    if allowed is not None:
        query = query.filter(Document.doc_type.in_(allowed))
    excluded = {d.id for d in descendants(doc)} | ({doc.id} if doc else set())
    return [d for d in query.order_by(Document.ref).all() if d.id not in excluded and not d.is_strict]


def descendants(doc: Document | None) -> list[Document]:
    out, stack, seen = [], list(doc.children) if doc else [], set()
    while stack:
        d = stack.pop()
        if d.id in seen:
            continue
        seen.add(d.id)
        out.append(d)
        stack.extend(d.children)
    return out


def _check_parent(db: Session, site: dict, doc_type: str, parent_id: int | None, doc: Document | None) -> Document | None:
    allowed = parent_types(site, doc_type)
    if allowed == []:
        _require(not parent_id, f"A {doc_type} is the top of the hierarchy: it has no parent document.")
        return None
    if not parent_id:
        if allowed is not None:
            raise WorkflowError(f"Choose the parent document: a {doc_type} hangs under a {' or '.join(allowed)}. "
                                "Create that document first if it does not exist yet.")
        return None
    parent = db.get(Document, parent_id)
    _require(parent is not None and parent.status != RETIRED, "Unknown or withdrawn parent document.")
    _require(not parent.is_strict, "An Audit document cannot be the parent of another document.")
    if allowed is not None and parent.doc_type not in allowed:
        raise WorkflowError(f"A {doc_type} hangs under a {' or '.join(allowed)}, not under a {parent.doc_type}.")
    if doc is not None:
        _require(parent.id != doc.id and parent.id not in {d.id for d in descendants(doc)},
                 "A document cannot hang under itself or under one of its own children.")
    return parent


def tree(db: Session) -> list[tuple[int, Document]]:
    """The whole register as (depth, document), parents first, children sorted by reference."""
    docs = db.query(Document).order_by(Document.ref).all()
    ids = {d.id for d in docs}
    out: list[tuple[int, Document]] = []

    def walk(d: Document, depth: int, seen: set[int]):
        out.append((depth, d))
        for c in d.children:
            if c.id not in seen:
                walk(c, depth + 1, seen | {c.id})

    for d in docs:
        if d.parent_id is None or d.parent_id not in ids:
            walk(d, 0, {d.id})
    return out


def forest(docs: list[Document], type_order: list[str] | None = None) -> list[dict]:
    """`docs` as nested nodes {"doc", "children"}: each document under its parent when the parent is in
    `docs` too, else at the top. Children are sorted by type (in `type_order`) then by reference."""
    ids = {d.id for d in docs}
    order = type_order or []

    def key(d: Document):
        return (order.index(d.doc_type) if d.doc_type in order else len(order), d.ref)

    def node(d: Document, seen: set[int]) -> dict:
        kids = sorted((c for c in d.children if c.id in ids and c.id not in seen), key=key)
        return {"doc": d, "children": [node(c, seen | {c.id}) for c in kids]}

    roots = sorted((d for d in docs if d.parent_id is None or d.parent_id not in ids), key=key)
    return [node(d, {d.id}) for d in roots]


SUMMARY_MAX = 300   # one sentence (Writing Guideline): it must fit on the title page with the title


def _check_summary(summary: str) -> None:
    n = len((summary or "").strip())
    _require(n <= SUMMARY_MAX, f"The description is one sentence of {SUMMARY_MAX} characters at most, so that it fits "
             f"on the title page ({n} now). The detail belongs in the document.")


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


def save_online(db: Session, user: User | None, doc: Document, data: bytes) -> Revision:
    """Stores the file saved by the online editor as a new revision (Draft, Review and Revision: comments
    of reviewers are part of the file; Approval is frozen). The Word cover page is refreshed at check-in
    and publication only, so open editing sessions are never invalidated."""
    _require_status(doc, DRAFT, REVIEW, REVISION)
    filename = doc.latest_revision.filename if doc.latest_revision else "document.docx"
    actor = user or doc.owner
    revision = _store_revision(db, actor, doc, filename, data, "Saved from the online editor", refresh=False)
    _log(db, doc, actor, f"Online editor saved revision {revision.number}", doc.status, doc.status)
    return revision


def _store_revision(db: Session, actor: User, doc: Document, filename: str, data: bytes, note: str,
                    refresh: bool = True) -> Revision:
    filename = storage.safe_filename(filename)
    _require(storage.extension(filename) in storage.ALLOWED_EXTENSIONS,
             f"File type not accepted. Allowed: {', '.join(sorted(storage.ALLOWED_EXTENSIONS))}.")
    max_bytes = get_config().max_upload_mb * 1024 * 1024
    _require(0 < len(data) <= max_bytes, f"The file is empty or larger than {get_config().max_upload_mb} MB.")
    if refresh and docx_meta.supports_metadata(filename):
        try:
            data = refresh_docx(db, doc, data)
        except Exception as exc:  # a damaged .docx is still stored, as uploaded
            _log(db, doc, actor, "Word metadata not applied", doc.status, doc.status, str(exc)[:300])
    path, digest = storage.save_bytes(str(doc.id), filename, data)
    number = max((r.number for r in doc.revisions), default=0) + 1
    revision = Revision(document=doc, number=number, filename=filename,
                        content_type=_content_type(filename), storage_path=path, size=len(data), sha256=digest,
                        note=(note or "").strip(), uploaded_by=actor, status_at_upload=doc.status)
    db.add(revision)
    db.flush()
    db.refresh(doc)
    return revision


def history_rows(doc: Document, upcoming: dict | None = None) -> list[dict[str, str]]:
    """Version history for the cover page: published versions, oldest first, plus `upcoming`."""
    rows = [{"version": p.version, "date": p.published_at.isoformat(), "author": doc.owner.display,
             "note": p.change_note, "approved_by": p.approved_by} for p in reversed(doc.publications)]
    if upcoming:
        rows.append(upcoming)
    return rows


def refresh_docx(db: Session, doc: Document, data: bytes, version: str | None = None,
                 published: date | None = None, upcoming: dict | None = None) -> bytes:
    """Fills the Word variables and (re)builds the cover page of a .docx."""
    site = site_settings.get_all(db)
    vals = naming.values(doc, version, site, published)
    if published:
        vals["STATUS"] = PUBLISHED
    return docx_meta.update_docx(data, vals, naming.LABELS, history_rows(doc, upcoming),
                                 approvals=approval_rows(doc), logo=cover_logo(site))


def approval_rows(doc: Document) -> list[dict[str, str]]:
    """The approvers of the version in preparation (or just published), for {{APPROVALS}}."""
    return [{"role": "Approver", "name": a.user.display, "decision": "Approved" if a.acted_at else "Pending",
             "date": a.acted_at.date().isoformat() if a.acted_at else ""} for a in doc.approvers]


def cover_logo(site: dict) -> tuple[bytes, str] | None:
    """The site logo for cover pages, when it is a PNG or JPEG (Word cannot show SVG reliably)."""
    path = storage.asset_path(site.get("logo_asset") or "")
    if path is None or path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        return None
    return path.read_bytes(), path.suffix.lower()


def _clean_keywords(text: str) -> str:
    words = [w.strip() for w in (text or "").replace(";", ",").split(",") if w.strip()]
    return ", ".join(dict.fromkeys(words))[:500]


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
    _require(parent is not None or doc.status != REVISION or doc.owner_id == actor.id or is_manager(actor),
             "During the revision you can only reply to the existing remarks: the review is over.")
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


def remarks(doc: Document) -> list[Comment]:
    """The remarks of the version in preparation that the owner must decide on: top-level comments
    of the other participants (written in Promulga or imported from the Word file)."""
    return [c for c in doc.comments if c.parent_id is None and c.version == doc.next_version
            and c.author_id != doc.owner_id]


def undecided(doc: Document) -> list[Comment]:
    return [c for c in remarks(doc) if not c.decision]


def decide_comment(db: Session, actor: User, doc: Document, comment: Comment, decision: str, note: str = "") -> None:
    """Revision: the owner accepts a remark, or rejects it with a justification."""
    _require_status(doc, REVISION)
    _require_owner_or_manager(doc, actor)
    _require(comment.document_id == doc.id and comment.parent_id is None, "Unknown comment.")
    _require(decision in (ACCEPTED, REJECTED), "Choose accept or reject.")
    note = (note or "").strip()
    _require(decision == ACCEPTED or bool(note), "Explain why you reject this remark.")
    comment.decision = decision
    comment.decision_note = note[:5000]
    comment.decided_at = utcnow()
    comment.decided_by_id = actor.id
    comment.resolved = True
    _log(db, doc, actor, f"Remark {decision}", doc.status, doc.status,
         f"{comment.author_name}: {comment.body[:200]}" + (f" -> {note[:300]}" if note else ""))


def import_word_comments(db: Session, doc: Document) -> int:
    """Copies the comments of the latest Word revision into the remarks (once per comment)."""
    rev = doc.latest_revision
    if rev is None or not docx_meta.supports_metadata(rev.filename):
        return 0
    try:
        found = docx_meta.word_comments(storage.read_bytes(rev.storage_path))
    except Exception:
        return 0
    known = {c.external_id for c in doc.comments if c.external_id}
    people = {}
    for u in [doc.owner] + [a.user for a in doc.assignments]:
        people[u.display.lower()] = u
        people[u.email.lower()] = u
    added = 0
    for item in found:
        key = f"word:{rev.sha256[:12]}:{item['id']}"
        if key in known:
            continue
        user = people.get(item["author"].strip().lower())
        db.add(Comment(document=doc, author=user, author_label=item["author"][:200], source="word",
                       location=item["anchor"][:300], external_id=key, body=item["text"][:10_000],
                       stage=REVIEW, version=doc.next_version, revision=rev))
        added += 1
    if added:
        db.flush()
        db.refresh(doc)
    return added


def comments_csv_rows(doc: Document) -> list[list[str]]:
    """Every remark of the version in preparation with the owner's decision, for the record."""
    rows = [["Version", "Source", "Author", "Date (UTC)", "Location", "Remark", "Decision", "Justification",
             "Decided by", "Decided at (UTC)"]]
    for c in [c for c in doc.comments if c.parent_id is None]:
        rows.append([c.version, c.source, c.author_name, c.created_at.strftime("%Y-%m-%d %H:%M"), c.location,
                     c.body, c.decision, c.decision_note, c.decided_by.display if c.decided_by else "",
                     c.decided_at.strftime("%Y-%m-%d %H:%M") if c.decided_at else ""])
    return rows


# ---------------------------------------------------------------------------
# PDF copies for Review and Approval
# ---------------------------------------------------------------------------

def review_copy(db: Session, doc: Document, rev: Revision) -> bytes:
    """A PDF of `rev` marked 'Review copy': what reviewers and approvers read instead of the Word file.
    Kept on disk per revision and stage. Raises pdf.ConversionError when no converter is available."""
    if doc.status == APPROVAL and doc.approval_pdf_path and doc.approval_revision_id == rev.id:
        return storage.read_bytes(doc.approval_pdf_path)
    cache = get_config().files_dir / str(doc.id) / "review" / f"r{rev.id}-{doc.status}.pdf"
    if cache.is_file():
        return cache.read_bytes()
    data = storage.read_bytes(rev.storage_path)
    if docx_meta.supports_metadata(rev.filename):
        data = refresh_docx(db, doc, data)
    out = pdf.watermark(pdf.to_pdf(rev.filename, data), "REVIEW COPY")
    out = pdf.stamp(out, f"{doc.ref} - {doc.title} - version {doc.next_version} (draft, not in force) - "
                         f"r{rev.number} - {doc.status}")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(out)
    return out


def _prepare_approval(db: Session, doc: Document, actor: User, site: dict) -> None:
    """Approval: freezes the submitted revision and builds the PDF the approvers approve."""
    rev = doc.latest_revision
    doc.approval_revision_id = rev.id if rev else None
    doc.approval_pdf_path = doc.approval_pdf_sha = ""
    if rev is None:
        return
    try:
        data = storage.read_bytes(rev.storage_path)
        if docx_meta.supports_metadata(rev.filename):
            data = refresh_docx(db, doc, data)
        out = pdf.stamp(pdf.to_pdf(rev.filename, data),
                        f"{doc.ref} - {doc.title} - version {doc.next_version} submitted for approval "
                        f"{date.today().isoformat()} - {site['site_name']}")
    except pdf.ConversionError as exc:
        _log(db, doc, actor, "Approval PDF not built", doc.status, doc.status, str(exc)[:300])
        return
    doc.approval_pdf_path, doc.approval_pdf_sha = storage.save_bytes(f"{doc.id}/approval",
                                                                     f"{doc.ref}-v{doc.next_version}.pdf", out)
    _log(db, doc, actor, "Approval PDF built", doc.status, doc.status,
         f"r{rev.number}, sha256 {doc.approval_pdf_sha}")


# ---------------------------------------------------------------------------
# Annexes: translations and external references (never reviewed nor approved)
# ---------------------------------------------------------------------------

TRANSLATION_EXTENSIONS = {".pdf", ".docx", ".odt"}


def add_translation(db: Session, actor: User, doc: Document, language: str, file: tuple[str, bytes],
                    method: str = "machine") -> Translation:
    _require_owner_or_manager(doc, actor)
    _require(language in TRANSLATION_LANGUAGES, "Unknown language.")
    _require(bool(doc.current_version), "Translations are attached to a published version: publish the "
             "English version first.")
    name = storage.safe_filename(file[0])
    _require(storage.extension(name) in TRANSLATION_EXTENSIONS,
             f"File type not accepted. Allowed: {', '.join(sorted(TRANSLATION_EXTENSIONS))}.")
    _require(0 < len(file[1]) <= get_config().max_upload_mb * 1024 * 1024,
             f"The file is empty or larger than {get_config().max_upload_mb} MB.")
    path, digest = storage.save_bytes(f"{doc.id}/translations", name, file[1])
    t = Translation(document=doc, language=language, version=doc.current_version,
                    method="human" if method == "human" else "machine", filename=name, storage_path=path,
                    content_type=_content_type(name), sha256=digest, size=len(file[1]), created_by_id=actor.id)
    db.add(t)
    db.flush()
    db.refresh(doc)
    _log(db, doc, actor, f"Translation added ({TRANSLATION_LANGUAGES[language]})", doc.status, doc.status,
         f"{t.notice} {name}, sha256 {digest}")
    return t


def remove_translation(db: Session, actor: User, doc: Document, translation: Translation) -> None:
    _require_owner_or_manager(doc, actor)
    _require(translation.document_id == doc.id, "Unknown translation.")
    _log(db, doc, actor, f"Translation removed ({translation.language_label})", doc.status, doc.status,
         f"version {translation.version}, {translation.filename}")
    db.delete(translation)
    db.flush()
    db.refresh(doc)


def add_reference(db: Session, actor: User, doc: Document, *, title: str, issuer: str = "", edition: str = "",
                  url: str = "", file: tuple[str, bytes] | None = None) -> ExternalReference:
    """Registers an external reference and links it to `doc`."""
    _require_owner_or_manager(doc, actor)
    title, url = (title or "").strip(), (url or "").strip()
    _require(bool(title), "The title of the reference is required.")
    _require(not url or url.startswith(("https://", "http://")), "The link must start with https://.")
    _require(bool(url) or file is not None, "Give a link or a file.")
    ref = ExternalReference(title=title[:300], issuer=(issuer or "").strip()[:200],
                            edition=(edition or "").strip()[:100], url=url[:1000], created_by_id=actor.id)
    if file is not None:
        name = storage.safe_filename(file[0])
        _require(storage.extension(name) in storage.ALLOWED_EXTENSIONS, "File type not accepted.")
        _require(0 < len(file[1]) <= get_config().max_upload_mb * 1024 * 1024,
                 f"The file is empty or larger than {get_config().max_upload_mb} MB.")
        ref.filename = name
        ref.storage_path, _ = storage.save_bytes("references", name, file[1])
    db.add(ref)
    db.flush()
    link_reference(db, actor, doc, ref)
    return ref


def link_reference(db: Session, actor: User, doc: Document, ref: ExternalReference) -> None:
    _require_owner_or_manager(doc, actor)
    if ref not in doc.references:
        doc.references.append(ref)
        _log(db, doc, actor, "External reference linked", doc.status, doc.status, ref.label)


def unlink_reference(db: Session, actor: User, doc: Document, ref: ExternalReference) -> None:
    _require_owner_or_manager(doc, actor)
    if ref in doc.references:
        doc.references.remove(ref)
        _log(db, doc, actor, "External reference unlinked", doc.status, doc.status, ref.label)


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
    if doc.status == REVISION:
        open_ = undecided(doc)
        _require(not open_, f"Decide on every remark before submitting: {len(open_)} remark(s) without a "
                 "decision (accept, or reject with a justification).")
    _require(bool(independent_approvers(doc)), "The owner is the only approver: add at least one other "
             "approver before submitting (the owner approves the document, but never alone).")
    if doc.status == DRAFT and doc.reviewers:
        _move(db, doc, REVIEW, actor, "Submitted for review")
        return
    site = site_settings.get_all(db)
    label = "Submitted for approval (no reviewers)" if doc.status == DRAFT else "Submitted for approval"
    _move(db, doc, APPROVAL, actor, label, site=site, notify=False)
    _prepare_approval(db, doc, actor, site)
    _notify_stage(db, doc, site, action=label, actor=actor)


def review_done(db: Session, actor: User, doc: Document, note: str = "") -> None:
    _require_status(doc, REVIEW)
    own = next((a for a in doc.reviewers if a.user_id == actor.id), None)
    _require(not (own and own.acted_at), "You already marked your review as done.")
    mine = acting_assignment(doc, actor, REVIEWER)
    _require(mine is not None, f"You are not a reviewer of {doc.ref}.")
    mine.acted_at = utcnow()
    _log(db, doc, actor, "Review done" + _on_behalf(mine, actor), REVIEW, REVIEW, note)
    _advance_if_complete(db, doc, actor)


def close_review(db: Session, actor: User, doc: Document, note: str = "") -> None:
    _require_manager(actor)
    _require_status(doc, REVIEW)
    missing = ", ".join(u.email for u in pending_users(doc))
    detail = " ".join(p for p in [note.strip(), f"(no response from {missing})" if missing else ""] if p)
    _move(db, doc, REVISION, actor, "Review closed by manager", detail)


def approve(db: Session, actor: User, doc: Document, note: str = "") -> None:
    _require_status(doc, APPROVAL)
    own = next((a for a in doc.approvers if a.user_id == actor.id), None)
    _require(not (own and own.acted_at), "You already approved this version.")
    mine = acting_assignment(doc, actor, APPROVER)
    _require(mine is not None, f"You are not an approver of {doc.ref}.")
    mine.acted_at = utcnow()
    _log(db, doc, actor, "Approved" + _on_behalf(mine, actor), APPROVAL, APPROVAL, note)
    _advance_if_complete(db, doc, actor)


def reject(db: Session, actor: User, doc: Document, note: str) -> None:
    _require_status(doc, APPROVAL)
    mine = next((a for a in doc.approvers if a.user_id == actor.id), None) or acting_assignment(doc, actor, APPROVER)
    _require(mine is not None, f"You are not an approver of {doc.ref}.")
    _require(bool((note or "").strip()), "Please explain why you reject the document.")
    _move(db, doc, REVISION, actor, "Rejected" + _on_behalf(mine, actor), note.strip())


def _on_behalf(assignment: Assignment, actor: User) -> str:
    return "" if assignment.user_id == actor.id else f" (on behalf of {assignment.user.email})"


def start_revision(db: Session, actor: User, doc: Document, bump: str, note: str = "") -> None:
    """Published/Retired -> Draft, targeting the next minor or major version."""
    _require_manager(actor)
    _require_status(doc, PUBLISHED, RETIRED)
    _require(bool((note or "").strip()), "Describe what the new version will change (change note).")
    doc.next_version = bump_version(doc.current_version, bump)
    doc.change_note = note.strip()[:2000]
    _move(db, doc, DRAFT, actor, f"New {'major' if bump == 'major' else 'minor'} revision started",
          f"Target version {doc.next_version}. {doc.change_note}")


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
    if status in (DRAFT, REVISION):         # unfrozen: a new approval PDF is built at the next submission
        doc.approval_revision_id = None
        doc.approval_pdf_path = doc.approval_pdf_sha = ""
    _log(db, doc, actor, action, previous, status, detail)
    if status == REVISION:
        n = import_word_comments(db, doc)
        if n:
            _log(db, doc, actor, f"{n} comment(s) imported from the Word file", status, status)
    _open_stage(db, doc)
    webhook(db, site, action, doc, actor, detail)
    if notify:
        _notify_stage(db, doc, site, action=action, actor=actor, detail=detail)


def _open_stage(db: Session, doc: Document) -> None:
    """Closes the open StageLog line of the document and opens one for its current stage."""
    now = utcnow()
    for row in db.query(StageLog).filter_by(document_id=doc.id, ended_at=None).all():
        row.ended_at = now
    if doc.status in ACTIVE_STATUSES:
        db.add(StageLog(document_id=doc.id, stage=doc.status, started_at=now, due=doc.stage_due,
                        owner_id=doc.owner_id))


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
    _require(doc.approval_revision_id in (None, source.id),
             "The file changed after it was submitted for approval: send it back to revision.")
    approved_pdf = doc.approval_pdf_sha
    version = normalise_version(doc.next_version) or "1.0"
    today = date.today()
    # Set before the PDF is built, so that its document control page shows the next review date.
    months = doc.review_months if doc.review_months is not None else int(site.get("review_months") or 0)
    doc.next_review = _add_months(today, months) if months > 0 else None
    approved_by = ", ".join(a.user.display for a in doc.approvers)
    note = doc.change_note or ("Initial version" if not doc.publications else "")
    upcoming = {"version": version, "date": today.isoformat(), "author": doc.owner.display, "note": note,
                "approved_by": approved_by}
    data = storage.read_bytes(source.storage_path)
    try:
        if docx_meta.supports_metadata(source.filename):
            data = refresh_docx(db, doc, data, version=version, published=today, upcoming=upcoming)
            raw_pdf = pdf.to_pdf(source.filename, data)
        else:
            vals = naming.values(doc, version, site, today)
            vals["STATUS"] = PUBLISHED
            raw_pdf = pdf.prepend(pdf.cover_pdf(vals, naming.LABELS, history_rows(doc, upcoming),
                                                logo=cover_logo(site), approvals=approval_rows(doc)),
                                  pdf.to_pdf(source.filename, data))
    except pdf.ConversionError as exc:
        raise WorkflowError(f"Publication failed: {exc} Your approval was not recorded; try again later "
                            "or ask the owner to check in a PDF.") from exc
    label = f"{doc.ref} - {doc.title} - Version {version} - Published {today.isoformat()} - {site['site_name']}"
    final_pdf = pdf.stamp(raw_pdf, label)
    signed = False
    if get_config().sign_pdfs:
        final_pdf = pdf.sign(final_pdf, f"{doc.ref} version {version} approved by {approved_by}")
        signed = True
    path, digest = storage.save_bytes(f"{doc.id}/published", f"{doc.ref}-v{version}.pdf", final_pdf)
    publication = Publication(
        document=doc, version=version, published_at=today, pdf_path=path, sha256=digest,
        source_revision_id=source.id, title=doc.title, text=pdf.extract_text(final_pdf),
        approved_by=approved_by, change_note=note, signed=signed,
    )
    db.add(publication)
    doc.current_version = version
    doc.published_at = today
    doc.change_note = ""
    _move(db, doc, PUBLISHED, actor, f"Published v{version}",
          f"sha256 {digest}" + (f"; approved PDF sha256 {approved_pdf}, same revision r{source.number}"
                                if approved_pdf else ""), site=site)
    db.flush()
    db.refresh(doc)          # publications are ordered newest first
    if doc.ack_required:
        _ask_acknowledgments(db, doc, site)
    if site.get("export_auto"):
        site_settings.set_many(db, {"export_pending": True})
    return publication


def _add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year, month = d.year + month // 12, month % 12 + 1
    import calendar
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


# ---------------------------------------------------------------------------
# Read & acknowledge
# ---------------------------------------------------------------------------

def acknowledgment_audience(db: Session, doc: Document | None = None) -> list[User]:
    """The mandatory readers of `doc` chosen by its Data Owner, else everybody with an active account."""
    query = db.query(User).filter(User.active.is_(True))
    emails = [e.strip().lower() for e in (doc.readers if doc is not None else "").split(",") if e.strip()]
    if emails:
        query = query.filter(User.email.in_(emails))
    users = query.order_by(User.email).all()
    if doc is not None and doc.is_strict:   # never everybody: the audience of the document
        users = [u for u in users if doc.in_audience(u)]
    return users


def acknowledge(db: Session, user: User, doc: Document) -> None:
    pub = doc.latest_publication
    _require(doc.ack_required and pub is not None and doc.status != RETIRED,
             "This document does not ask for an acknowledgment.")
    if db.query(Acknowledgment).filter_by(publication_id=pub.id, user_id=user.id).first():
        return
    db.add(Acknowledgment(document_id=doc.id, publication_id=pub.id, user_id=user.id))
    _log(db, doc, user, f"Acknowledged v{pub.version}", doc.status, doc.status)


def acknowledgment_status(db: Session, doc: Document) -> dict:
    pub = doc.latest_publication
    audience = acknowledgment_audience(db, doc)
    if pub is None:
        return {"publication": None, "done": [], "missing": audience, "rate": 0}
    done_ids = {a.user_id: a for a in db.query(Acknowledgment).filter_by(publication_id=pub.id).all()}
    done = [(u, done_ids[u.id]) for u in audience if u.id in done_ids]
    missing = [u for u in audience if u.id not in done_ids]
    return {"publication": pub, "done": done, "missing": missing,
            "rate": round(100 * len(done) / len(audience)) if audience else 0}


def _ask_acknowledgments(db: Session, doc: Document, site: dict, only: list[User] | None = None) -> int:
    link = f"{get_config().base_url}/d/{doc.ref}"
    people = only if only is not None else acknowledgment_audience(db, doc)
    for user in people:
        mailer.queue(db, user.email, f"Please read {doc.ref} version {doc.current_version}", "notice.html", site,
                     heading=f"{doc.ref} - {doc.title}",
                     lines=[f"Version {doc.current_version} of this {doc.doc_type.lower()} is now in force.",
                            "Please read it and confirm with the button 'I have read and understood' on its page."],
                     button=("Read and acknowledge", link))
    return len(people)


def follows_distribution(doc: Document, user: User) -> bool:
    """Who sees, by name, the people the document is distributed to and whether they read it:
    its owner (or the deputy of an absent owner) and the Document Managers."""
    if not can_see(doc, user):
        return False
    return (is_manager(user) or doc.owner_id == user.id
            or (doc.owner.is_absent and doc.owner.deputy_id == user.id))


def distribution(db: Session, doc: Document) -> dict:
    """The distribution list of `doc`: everybody in its audience, with the acknowledgment of the version in
    force when reading is tracked. Before the first publication: the planned list, nobody has read yet.
    rows = [{"user", "ack"}], the people who have not read first."""
    status = acknowledgment_status(db, doc)
    tracked = doc.ack_required and status["publication"] is not None
    rows = [{"user": u, "ack": None} for u in status["missing"]] + [{"user": u, "ack": a} for u, a in status["done"]]
    rows.sort(key=lambda r: (r["ack"] is not None, (r["user"].name or r["user"].email).lower()))
    named = bool(doc.audience_emails)
    return {**status, "rows": rows, "tracked": tracked, "named": named, "total": len(rows),
            "planned": status["publication"] is None}


def remind_acknowledgments(db: Session, actor: User, doc: Document) -> int:
    _require(follows_distribution(doc, actor), f"Only the owner of {doc.ref} or a Document Manager can do this.")
    _require(doc.ack_required and doc.latest_publication is not None and doc.status != RETIRED,
             "This document does not ask for an acknowledgment of a published version.")
    missing = acknowledgment_status(db, doc)["missing"]
    n = _ask_acknowledgments(db, doc, site_settings.get_all(db), missing)
    _log(db, doc, actor, "Acknowledgment reminder", doc.status, doc.status, f"{n} person(s)")
    return n


# ---------------------------------------------------------------------------
# Webhooks (Teams, Slack, any JSON endpoint)
# ---------------------------------------------------------------------------

def webhook(db: Session, site: dict, action: str, doc: Document, actor: User | None, detail: str = "") -> None:
    urls = [u for u in site.get("webhook_urls") or [] if u.startswith("https://")]
    if not urls:
        return
    if doc.is_strict:   # an Audit document is not announced outside its audience
        return
    who = actor.display if actor else "Promulga"
    text = f"{doc.ref} {doc.title}: {action} by {who} (stage {doc.status}). {doc_link(doc)}"
    payload = json.dumps({"text": text, "event": action, "stage": doc.status, "ref": doc.ref, "title": doc.title,
                          "version": _version_label(doc), "actor": actor.email if actor else "", "detail": detail,
                          "url": doc_link(doc)})
    for url in urls:
        db.add(WebhookDelivery(url=url, payload=payload))


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
        if user.is_absent and user.deputy:
            mailer.queue(db, user.deputy.email, f"[deputy for {user.display}] {subject}", "notice.html", site,
                         heading=f"{doc.ref} - {doc.title}",
                         lines=[f"{user.display} is absent until {user.absent_until.isoformat()} and named you "
                                "as deputy. You can act on their behalf."] + lines, button=link)

    if doc.status == DRAFT:
        send(doc.owner, f"Please draft {doc.ref} (due {due})", [
            f"You are the owner of this {doc.doc_type.lower()} (version {doc.next_version} in preparation).",
            "Download the working file (check-out), edit it, and upload it again (check-in). "
            "When it is ready, click Submit.", f"Due date: {due}."])
    elif doc.status == REVIEW:
        for user in pending_users(doc):
            send(user, f"Please review {doc.ref} (due {due})", [
                f"{doc.owner.display} asks you to review this document.",
                "Read the PDF review copy, then write your remarks as comments on the document page "
                "(or comment online, or attach an annotated copy). Click 'Review done' when finished.",
                f"Due date: {due}."])
    elif doc.status == REVISION:
        intro = ("The document was rejected during approval and comes back to you."
                 if action == "Rejected" else "The review is finished.")
        send(doc.owner, f"Please revise {doc.ref} (due {due})", [
            intro, "Decide on every remark (accept, or reject with a justification), check in the corrected "
            "file, then submit it for approval.",
            f"Due date: {due}."])
    elif doc.status == APPROVAL:
        for user in pending_users(doc):
            send(user, f"Please approve {doc.ref} (due {due})", [
                f"Version {doc.next_version} is submitted for your approval.",
                "Read the PDF submitted for approval, then approve it, or reject it with a comment.", f"Due date: {due}."])
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
