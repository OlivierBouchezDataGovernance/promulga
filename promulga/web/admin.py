"""Admin site: tasks, register, document pages and workflow actions, users,
taxonomies, site settings, outbox and audit log.

Every POST route is CSRF-protected and every workflow rule is enforced in
:mod:`promulga.workflow`; the routes only translate HTTP into function calls,
commit, and redirect back with a message.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import secrets
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from .. import (accounts, docx_meta, export, icons, insights, mailer, naming, onlyoffice, pdf, reminders,
               site_settings, storage, workflow)
from ..config import get_config
from ..db import get_db
from ..models import (
    ACTIVE_STATUSES, APPROVAL, REVIEW, ROLES, STATUSES, TRANSLATION_LANGUAGES, ApiToken, Assignment, Comment,
    Document, Event, ExternalReference, OutboxMail, PasskeyCredential, Publication, Revision, Taxonomy, Term,
    Translation, User, WebhookDelivery,
)
from ..security import check_password_strength, hash_password, verify_password
from .deps import (
    csrf_protect, flash, login_session, redirect, render, require_admin, require_contributor, require_manager,
)

router = APIRouter(prefix="/admin")
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:100] or "item"


def _int_list(values: list[str] | None) -> list[int]:
    return [int(v) for v in values or [] if str(v).isdigit()]


async def _read_upload(upload: UploadFile | None) -> tuple[str, bytes] | None:
    if upload is None or not upload.filename:
        return None
    limit = get_config().max_upload_mb * 1024 * 1024
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise workflow.WorkflowError(f"The file is larger than {get_config().max_upload_mb} MB.")
    return upload.filename, data


def _months(value: str) -> int | None:
    value = (value or "").strip()
    return max(0, min(120, int(value))) if value.isdigit() else None


def _warn(request: Request, doc: Document, filename: str | None = None) -> None:
    for w in workflow.segregation_warnings(doc):
        flash(request, w, "error")
    if filename and docx_meta.capability_warning(filename):
        flash(request, docx_meta.capability_warning(filename), "error")


def _get_doc(db: Session, doc_id: int, user: User) -> Document:
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404)
    if not workflow.can_see(doc, user):
        raise HTTPException(403, "You do not take part in this document.")
    return doc


def _people(text: str, picks: list[str] | None) -> list[str]:
    """E-mails of a participant field: the users checked in the list, then the addresses typed."""
    return list(dict.fromkeys([p.strip().lower() for p in picks or [] if p.strip()] + accounts.parse_emails(text)))


def _owner(choice: str, other: str) -> str:
    return (other or "").strip() if (choice or "") in ("", "__other") else choice


def _form_users(db: Session) -> dict:
    active = db.query(User).filter(User.active.is_(True)).order_by(User.name, User.email).all()
    return {"users": [u for u in active if u.role != "viewer"], "all_users": active}


PENDING = "pending"   # files received with a creation form that was refused, kept for the next attempt


def _keep_upload(upload: tuple[str, bytes]) -> str:
    """Keeps the file of a refused creation form (browsers empty file fields when a page is shown again)."""
    token = secrets.token_hex(12)
    folder = get_config().files_dir / PENDING
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{token}.bin").write_bytes(upload[1])
    (folder / f"{token}.name").write_text(storage.safe_filename(upload[0]), encoding="utf-8")
    return token


def _pending_upload(token: str) -> tuple[str, bytes] | None:
    if not re.fullmatch(r"[0-9a-f]{24}", token or ""):
        return None
    folder = get_config().files_dir / PENDING
    data, name = folder / f"{token}.bin", folder / f"{token}.name"
    if not data.is_file() or not name.is_file():
        return None
    return name.read_text(encoding="utf-8"), data.read_bytes()


def _drop_pending(token: str) -> None:
    if re.fullmatch(r"[0-9a-f]{24}", token or ""):
        for ext in (".bin", ".name"):
            (get_config().files_dir / PENDING / f"{token}{ext}").unlink(missing_ok=True)


def _parent_id(value: str) -> int | None:
    value = (value or "").strip()
    return int(value) if value.isdigit() else None


def _parent_options(db: Session, doc: Document | None = None) -> dict:
    """Candidates for the 'Parent document' list, and the parent types allowed per document type."""
    site = site_settings.get_all(db)
    if doc is not None:
        return {"parents": workflow.parent_candidates(db, site, doc.doc_type, doc),
                "parent_rules": {doc.doc_type: workflow.parent_types(site, doc.doc_type)}}
    parents = [d for d in db.query(Document).filter(Document.status != "Retired").order_by(Document.ref).all()
               if not d.is_strict]
    return {"parents": parents,
            "parent_rules": {t["name"]: workflow.parent_types(site, t["name"]) for t in site["doc_types"]}}


def _taxonomies(db: Session) -> list[Taxonomy]:
    return db.query(Taxonomy).options(selectinload(Taxonomy.terms)).order_by(Taxonomy.sort, Taxonomy.name).all()


# ---------------------------------------------------------------------------
# Dashboard and register
# ---------------------------------------------------------------------------

@router.get("")
def dashboard(request: Request, db: Session = Depends(get_db), user: User = Depends(require_contributor)):
    active = (db.query(Document).filter(Document.status.in_(ACTIVE_STATUSES))
              .options(selectinload(Document.assignments)).order_by(Document.stage_due).all())
    to_write = [d for d in active if d.owner_id == user.id and d.status in ("Draft", "Revision")]
    to_review = [d for d in active if d.status == REVIEW and any(
        a.user_id == user.id and a.role == "reviewer" and a.acted_at is None for a in d.assignments)]
    to_approve = [d for d in active if d.status == APPROVAL and any(
        a.user_id == user.id and a.role == "approver" and a.acted_at is None for a in d.assignments)]
    following = [d for d in active if workflow.is_participant(d, user) and workflow.can_see(d, user)
                 and d not in to_write + to_review + to_approve]
    overdue = [d for d in active if d.is_overdue] if workflow.is_manager(user) else []
    return render(request, db, "admin/dashboard.html", user=user, to_write=to_write, to_review=to_review,
                  to_approve=to_approve, following=following, overdue=overdue, active_count=len(active),
                  pending=workflow.pending_users)


def _register_query(db: Session, user: User, status: str, doc_type: str, q: str, mine: bool):
    query = db.query(Document).options(selectinload(Document.owner), selectinload(Document.terms))
    if not workflow.is_manager(user) or mine:
        query = query.outerjoin(Assignment).filter(
            or_(Document.owner_id == user.id, Assignment.user_id == user.id)).distinct()
    if status:
        query = query.filter(Document.status == status)
    if doc_type:
        query = query.filter(Document.doc_type == doc_type)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(Document.ref.ilike(like), Document.title.ilike(like), Document.summary.ilike(like)))
    return query.order_by(Document.ref)


@router.get("/documents")
def register(request: Request, status: str = "", type: str = "", q: str = "", mine: bool = False,
             db: Session = Depends(get_db), user: User = Depends(require_contributor)):
    docs = [d for d in _register_query(db, user, status, type, q, mine).all() if workflow.can_see(d, user)]
    return render(request, db, "admin/register.html", user=user, docs=docs, statuses=STATUSES,
                  status=status, type=type, q=q, mine=mine, pending=workflow.pending_users)


@router.get("/documents/tree")
def register_tree(request: Request, db: Session = Depends(get_db), user: User = Depends(require_contributor)):
    docs = [d for d in db.query(Document).all() if workflow.can_see(d, user)]
    order = [t["name"] for t in site_settings.get(db, "doc_types")]
    return render(request, db, "admin/tree.html", user=user, nodes=workflow.forest(docs, order), total=len(docs))


@router.get("/documents.csv")
def register_csv(status: str = "", type: str = "", q: str = "", mine: bool = False,
                 db: Session = Depends(get_db), user: User = Depends(require_contributor)):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["ID", "Title", "Type", "Owner", "Reviewers", "Approvers", "Status", "Stage due date",
                "Version", "Next version", "Publication date", "Tags", "Last update"])
    for d in _register_query(db, user, status, type, q, mine).all():
        w.writerow([d.ref, d.title, d.doc_type, d.owner.email, ", ".join(a.user.email for a in d.reviewers),
                    ", ".join(a.user.email for a in d.approvers), d.status, d.stage_due or "", d.current_version,
                    d.next_version, d.published_at or "", ", ".join(t.name for t in d.terms),
                    d.updated_at.strftime("%Y-%m-%d %H:%M")])
    return StreamingResponse(iter([out.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=register.csv"})


# ---------------------------------------------------------------------------
# Creation and editing (Document Managers)
# ---------------------------------------------------------------------------

@router.get("/documents/new")
def new_document_form(request: Request, db: Session = Depends(get_db), user: User = Depends(require_manager)):
    defaults = workflow.default_approvers(db, site_settings.get_all(db))
    return render(request, db, "admin/document_form.html", user=user, doc=None, taxonomies=_taxonomies(db),
                  selected_terms=set(), default_approvers=defaults,
                  form={"parent_id": request.query_params.get("parent", ""), "approvers": ", ".join(defaults)},
                  **_form_users(db), **_parent_options(db))


@router.post("/documents/new", dependencies=[Depends(csrf_protect)])
async def new_document(request: Request, title: str = Form(""), doc_type: str = Form(""), summary: str = Form(""),
                       owner: str = Form(""), reviewers: str = Form(""), approvers: str = Form(""),
                       terms: list[str] = Form([]), file: UploadFile | None = File(None),
                       review_months: str = Form(""), ack_required: str = Form(""),
                       classification: str = Form("internal"), keywords: str = Form(""),
                       readers: str = Form(""), parent_id: str = Form(""), owner_other: str = Form(""),
                       reviewers_pick: list[str] = Form([]), approvers_pick: list[str] = Form([]),
                       readers_pick: list[str] = Form([]), pending_file: str = Form(""),
                       db: Session = Depends(get_db), user: User = Depends(require_manager)):
    owner = _owner(owner, owner_other)
    upload = None
    try:
        upload = await _read_upload(file) or _pending_upload(pending_file)
        doc = workflow.create_document(
            db, user, title=title, doc_type=doc_type, summary=summary, owner_email=owner,
            reviewer_emails=_people(reviewers, reviewers_pick), approver_emails=_people(approvers, approvers_pick),
            term_ids=_int_list(terms), file=upload, review_months=_months(review_months),
            ack_required=ack_required == "1", classification=classification, keywords=keywords,
            readers=_people(readers, readers_pick), parent_id=_parent_id(parent_id))
        db.commit()
    except (workflow.WorkflowError, accounts.AccountError, ValueError) as exc:
        db.rollback()
        flash(request, str(exc), "error")
        if upload is not None and not _pending_upload(pending_file):
            _drop_pending(pending_file)
            pending_file = _keep_upload(upload)
        return render(request, db, "admin/document_form.html", user=user, doc=None, taxonomies=_taxonomies(db),
                      selected_terms=set(_int_list(terms)), status_code=400,
                      pending=(pending_file, upload[0]) if upload is not None else None,
                      default_approvers=workflow.default_approvers(db, site_settings.get_all(db)),
                      form={"title": title, "doc_type": doc_type, "summary": summary, "owner": owner,
                            "reviewers": ", ".join(_people(reviewers, reviewers_pick)),
                            "approvers": ", ".join(_people(approvers, approvers_pick)),
                            "classification": classification, "keywords": keywords,
                            "readers": ", ".join(_people(readers, readers_pick)), "parent_id": parent_id},
                      messages=request.session.pop("flash", []), **_form_users(db), **_parent_options(db))
    _drop_pending(pending_file)
    flash(request, f"{doc.ref} created with {'the file ' + upload[0] if upload else 'the template of the type'}. "
                   "The owner received an e-mail.", "success")
    _warn(request, doc, upload[0] if upload else None)
    return redirect(f"/admin/documents/{doc.id}")


@router.get("/documents/{doc_id}/edit")
def edit_document_form(doc_id: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require_manager)):
    doc = _get_doc(db, doc_id, user)
    form = {"title": doc.title, "doc_type": doc.doc_type, "summary": doc.summary, "owner": doc.owner.email,
            "reviewers": ", ".join(a.user.email for a in doc.reviewers),
            "approvers": ", ".join(a.user.email for a in doc.approvers),
            "review_months": "" if doc.review_months is None else doc.review_months, "ack_required": doc.ack_required,
            "classification": doc.classification, "keywords": doc.keywords, "readers": doc.readers,
            "parent_id": str(doc.parent_id or "")}
    return render(request, db, "admin/document_form.html", user=user, doc=doc, taxonomies=_taxonomies(db),
                  selected_terms={t.id for t in doc.terms}, form=form, default_approvers=[],
                  **_form_users(db), **_parent_options(db, doc))


@router.post("/documents/{doc_id}/edit", dependencies=[Depends(csrf_protect)])
def edit_document(doc_id: int, request: Request, title: str = Form(""), summary: str = Form(""),
                  owner: str = Form(""), reviewers: str = Form(""), approvers: str = Form(""),
                  terms: list[str] = Form([]), review_months: str = Form(""), ack_required: str = Form(""),
                  classification: str = Form("internal"), keywords: str = Form(""), readers: str = Form(""),
                  parent_id: str = Form(""), owner_other: str = Form(""),
                  reviewers_pick: list[str] = Form([]), approvers_pick: list[str] = Form([]),
                  readers_pick: list[str] = Form([]),
                  db: Session = Depends(get_db), user: User = Depends(require_manager)):
    doc = _get_doc(db, doc_id, user)
    owner = _owner(owner, owner_other)
    try:
        workflow.update_metadata(db, user, doc, title=title, summary=summary, term_ids=_int_list(terms),
                                 review_months=_months(review_months), ack_required=ack_required == "1",
                                 classification=classification, keywords=keywords,
                                 readers=_people(readers, readers_pick), parent_id=_parent_id(parent_id))
        new_people = (owner.strip().lower(), _people(reviewers, reviewers_pick), _people(approvers, approvers_pick))
        old_people = (doc.owner.email, [a.user.email for a in doc.reviewers], [a.user.email for a in doc.approvers])
        if new_people != old_people:
            workflow.update_participants(db, user, doc, owner_email=owner, reviewer_emails=new_people[1],
                                         approver_emails=new_people[2])
        db.commit()
        flash(request, "Document updated.", "success")
        _warn(request, doc)
    except (workflow.WorkflowError, accounts.AccountError, ValueError) as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return redirect(f"/admin/documents/{doc_id}/edit")
    return redirect(f"/admin/documents/{doc_id}")


# ---------------------------------------------------------------------------
# Document page and actions
# ---------------------------------------------------------------------------

@router.get("/documents/{doc_id}")
def document_page(doc_id: int, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_contributor)):
    doc = _get_doc(db, doc_id, user)
    events = db.query(Event).filter_by(document_id=doc.id).order_by(Event.id.desc()).all()
    top = [c for c in doc.comments if c.parent_id is None]
    replies: dict[int, list[Comment]] = {}
    for c in doc.comments:
        if c.parent_id:
            replies.setdefault(c.parent_id, []).append(c)
    actions = workflow.allowed_actions(doc, user)
    latest = doc.latest_revision
    dist = workflow.distribution(db, doc) if workflow.follows_distribution(doc, user) else None
    linked = {r.id for r in doc.references}
    return render(request, db, "admin/document.html", user=user, doc=doc, events=events, actions=actions,
                  has_task=bool(actions - {"comment", "reply", "edit", "annex"}), max_mb=get_config().max_upload_mb,
                  handles_file=workflow.handles_file(doc, user), remarks=workflow.remarks(doc),
                  undecided=workflow.undecided(doc), languages=TRANSLATION_LANGUAGES,
                  other_references=[r for r in db.query(ExternalReference).order_by(ExternalReference.title).all()
                                    if r.id not in linked],
                  pending=workflow.pending_users(doc), sod=workflow.segregation_warnings(doc),
                  children=[c for c in doc.children if workflow.can_see(c, user)],
                  capability=docx_meta.capability_warning(latest.filename) if latest else "",
                  editor=onlyoffice.available(latest), dist=dist,
                  comments=top, replies=replies, open_comments=sum(1 for c in top if not c.resolved))


@router.get("/documents/{doc_id}/revisions/{rev_id}")
def download_revision(doc_id: int, rev_id: int, db: Session = Depends(get_db),
                      user: User = Depends(require_contributor)):
    doc = _get_doc(db, doc_id, user)
    rev = db.get(Revision, rev_id)
    if rev is None or rev.document_id != doc.id:
        raise HTTPException(404)
    ext = "." + rev.filename.rsplit(".", 1)[-1].lower() if "." in rev.filename else ""
    if doc.status in ACTIVE_STATUSES and ext != ".pdf" and not workflow.handles_file(doc, user):
        return redirect(f"/admin/documents/{doc.id}/revisions/{rev.id}/copy.pdf")
    name = naming.file_name(doc, ext, site_settings.get_all(db))
    data = storage.read_bytes(rev.storage_path)
    if docx_meta.supports_metadata(rev.filename) and rev.id == doc.latest_revision.id:
        try:
            data = workflow.refresh_docx(db, doc, data)   # current status and dates in the downloaded copy
        except Exception:
            pass
    from urllib.parse import quote
    return Response(data, media_type=rev.content_type,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@router.get("/documents/{doc_id}/revisions/{rev_id}/copy.pdf")
def review_copy(doc_id: int, rev_id: int, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_contributor)):
    """PDF 'review copy' of a revision: what reviewers and approvers read instead of the Word file."""
    doc = _get_doc(db, doc_id, user)
    rev = db.get(Revision, rev_id)
    if rev is None or rev.document_id != doc.id:
        raise HTTPException(404)
    try:
        data = workflow.review_copy(db, doc, rev)
    except pdf.ConversionError as exc:
        flash(request, f"The PDF copy cannot be made: {exc}", "error")
        return redirect(f"/admin/documents/{doc_id}")
    name = naming.file_name(doc, ".pdf", site_settings.get_all(db)).replace(".pdf", "-review-copy.pdf")
    from urllib.parse import quote
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}"})


@router.get("/documents/{doc_id}/approval.pdf")
def approval_pdf(doc_id: int, db: Session = Depends(get_db), user: User = Depends(require_contributor)):
    """The PDF submitted for approval (frozen while the document is in Approval)."""
    doc = _get_doc(db, doc_id, user)
    if not doc.approval_pdf_path:
        raise HTTPException(404)
    return FileResponse(storage.absolute(doc.approval_pdf_path), media_type="application/pdf",
                        filename=f"{doc.ref}-v{doc.next_version}-for-approval.pdf", content_disposition_type="inline")


@router.get("/documents/{doc_id}/comments/{comment_id}/attachment")
def download_attachment(doc_id: int, comment_id: int, db: Session = Depends(get_db),
                        user: User = Depends(require_contributor)):
    doc = _get_doc(db, doc_id, user)
    c = db.get(Comment, comment_id)
    if c is None or c.document_id != doc.id or not c.attachment_path:
        raise HTTPException(404)
    return FileResponse(storage.absolute(c.attachment_path), filename=c.attachment_name)


@router.get("/documents/{doc_id}/publications/{pub_id}.pdf")
def download_publication(doc_id: int, pub_id: int, db: Session = Depends(get_db),
                         user: User = Depends(require_contributor)):
    doc = _get_doc(db, doc_id, user)
    pub = db.get(Publication, pub_id)
    if pub is None or pub.document_id != doc.id:
        raise HTTPException(404)
    name = naming.file_name(doc, ".pdf", site_settings.get_all(db), version=pub.version, draft=False,
                            published=pub.published_at)
    return FileResponse(storage.absolute(pub.pdf_path), media_type="application/pdf", filename=name,
                        content_disposition_type="inline")


@router.get("/documents/{doc_id}/compare")
def compare(doc_id: int, request: Request, a: int = 0, b: int = 0, db: Session = Depends(get_db),
            user: User = Depends(require_contributor)):
    """Text comparison of two revisions (default: last published source vs latest revision)."""
    doc = _get_doc(db, doc_id, user)
    revs = {r.id: r for r in doc.revisions}
    if not revs:
        raise HTTPException(404)
    new = revs.get(b) or doc.latest_revision
    default_old = None
    if doc.latest_publication and doc.latest_publication.source_revision_id in revs:
        default_old = revs[doc.latest_publication.source_revision_id]
    if default_old is None or default_old.id == new.id:
        default_old = next((r for r in doc.revisions if r.number < new.number), new)
    old = revs.get(a) or default_old
    lines = insights.compare(old, new)
    return render(request, db, "admin/compare.html", user=user, doc=doc, old=old, new=new, lines=lines,
                  added=sum(1 for op, _ in lines if op == "+"), removed=sum(1 for op, _ in lines if op == "-"))


def _run(request: Request, db: Session, doc_id: int, fn, ok: str):
    try:
        fn()
        db.commit()
        flash(request, ok, "success")
    except (workflow.WorkflowError, accounts.AccountError, ValueError) as exc:
        db.rollback()
        flash(request, str(exc), "error")
    return redirect(f"/admin/documents/{doc_id}")


@router.post("/documents/{doc_id}/checkout", dependencies=[Depends(csrf_protect)])
def checkout(doc_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_contributor)):
    doc = _get_doc(db, doc_id, user)
    try:
        workflow.check_out(db, user, doc)
        db.commit()
    except workflow.WorkflowError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return redirect(f"/admin/documents/{doc_id}")
    flash(request, "Checked out: the download starts. Edit the file, then check it in here.", "success")
    rev = doc.latest_revision
    return redirect(f"/admin/documents/{doc_id}?download={rev.id}" if rev else f"/admin/documents/{doc_id}")


@router.post("/documents/{doc_id}/release", dependencies=[Depends(csrf_protect)])
def release(doc_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_contributor)):
    doc = _get_doc(db, doc_id, user)
    return _run(request, db, doc_id, lambda: workflow.release_check_out(db, user, doc), "Check-out released.")


@router.post("/documents/{doc_id}/checkin", dependencies=[Depends(csrf_protect)])
async def checkin(doc_id: int, request: Request, note: str = Form(""), file: UploadFile | None = File(None),
                  db: Session = Depends(get_db), user: User = Depends(require_contributor)):
    doc = _get_doc(db, doc_id, user)
    try:
        upload = await _read_upload(file)
        if upload is None:
            raise workflow.WorkflowError("Choose a file to check in.")
    except workflow.WorkflowError as exc:
        flash(request, str(exc), "error")
        return redirect(f"/admin/documents/{doc_id}")
    response = _run(request, db, doc_id, lambda: workflow.check_in(db, user, doc, upload[0], upload[1], note),
                    "New revision checked in.")
    if docx_meta.capability_warning(upload[0]):
        flash(request, docx_meta.capability_warning(upload[0]), "error")
    return response


@router.post("/documents/{doc_id}/comments", dependencies=[Depends(csrf_protect)])
async def comment(doc_id: int, request: Request, body: str = Form(""), parent_id: str = Form(""),
                  file: UploadFile | None = File(None), db: Session = Depends(get_db),
                  user: User = Depends(require_contributor)):
    doc = _get_doc(db, doc_id, user)
    try:
        upload = await _read_upload(file)
    except workflow.WorkflowError as exc:
        flash(request, str(exc), "error")
        return redirect(f"/admin/documents/{doc_id}#comments")
    response = _run(request, db, doc_id, lambda: workflow.add_comment(
        db, user, doc, body, int(parent_id) if parent_id.isdigit() else None, upload), "Comment added.")
    response.headers["location"] += "#comments"
    return response


@router.post("/documents/{doc_id}/comments/{comment_id}/resolve", dependencies=[Depends(csrf_protect)])
def resolve(doc_id: int, comment_id: int, request: Request, resolved: str = Form("1"),
            db: Session = Depends(get_db), user: User = Depends(require_contributor)):
    doc = _get_doc(db, doc_id, user)
    c = db.get(Comment, comment_id)
    if c is None:
        raise HTTPException(404)
    response = _run(request, db, doc_id, lambda: workflow.set_comment_resolved(db, user, doc, c, resolved == "1"),
                    "Comment updated.")
    response.headers["location"] += "#comments"
    return response


@router.post("/documents/{doc_id}/comments/{comment_id}/decide", dependencies=[Depends(csrf_protect)])
def decide(doc_id: int, comment_id: int, request: Request, decision: str = Form(""), note: str = Form(""),
           db: Session = Depends(get_db), user: User = Depends(require_contributor)):
    doc = _get_doc(db, doc_id, user)
    c = db.get(Comment, comment_id)
    if c is None:
        raise HTTPException(404)
    response = _run(request, db, doc_id, lambda: workflow.decide_comment(db, user, doc, c, decision, note),
                    "Decision recorded.")
    response.headers["location"] += f"#c{comment_id}"
    return response


@router.get("/documents/{doc_id}/comments.csv")
def comments_csv(doc_id: int, db: Session = Depends(get_db), user: User = Depends(require_contributor)):
    doc = _get_doc(db, doc_id, user)
    out = io.StringIO()
    csv.writer(out).writerows(workflow.comments_csv_rows(doc))
    return StreamingResponse(iter([out.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={doc.ref}-remarks.csv"})


# ---------------------------------------------------------------------------
# Annexes: translations and external references (owner and Document Managers, any stage)
# ---------------------------------------------------------------------------

@router.post("/documents/{doc_id}/translations", dependencies=[Depends(csrf_protect)])
async def add_translation(doc_id: int, request: Request, language: str = Form(""), method: str = Form("machine"),
                          file: UploadFile | None = File(None), db: Session = Depends(get_db),
                          user: User = Depends(require_contributor)):
    doc = _get_doc(db, doc_id, user)
    try:
        upload = await _read_upload(file)
        if upload is None:
            raise workflow.WorkflowError("Choose the translated file.")
    except workflow.WorkflowError as exc:
        flash(request, str(exc), "error")
        return redirect(f"/admin/documents/{doc_id}#annexes")
    response = _run(request, db, doc_id, lambda: workflow.add_translation(db, user, doc, language, upload, method),
                    "Translation attached.")
    response.headers["location"] += "#annexes"
    return response


@router.get("/documents/{doc_id}/translations/{tr_id}")
def download_translation(doc_id: int, tr_id: int, db: Session = Depends(get_db),
                         user: User = Depends(require_contributor)):
    doc = _get_doc(db, doc_id, user)
    t = db.get(Translation, tr_id)
    if t is None or t.document_id != doc.id:
        raise HTTPException(404)
    return FileResponse(storage.absolute(t.storage_path), media_type=t.content_type,
                        filename=f"{doc.ref}-v{t.version}-{t.language.upper()}{storage.extension(t.filename)}")


@router.post("/documents/{doc_id}/translations/{tr_id}/delete", dependencies=[Depends(csrf_protect)])
def delete_translation(doc_id: int, tr_id: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require_contributor)):
    doc = _get_doc(db, doc_id, user)
    t = db.get(Translation, tr_id)
    if t is None:
        raise HTTPException(404)
    response = _run(request, db, doc_id, lambda: workflow.remove_translation(db, user, doc, t), "Translation removed.")
    response.headers["location"] += "#annexes"
    return response


@router.post("/documents/{doc_id}/references", dependencies=[Depends(csrf_protect)])
async def add_reference(doc_id: int, request: Request, reference_id: str = Form(""), title: str = Form(""),
                        issuer: str = Form(""), edition: str = Form(""), url: str = Form(""),
                        file: UploadFile | None = File(None), db: Session = Depends(get_db),
                        user: User = Depends(require_contributor)):
    doc = _get_doc(db, doc_id, user)
    if reference_id.isdigit():
        ref = db.get(ExternalReference, int(reference_id))
        if ref is None:
            raise HTTPException(404)

        def fn():
            workflow.link_reference(db, user, doc, ref)
    else:
        try:
            upload = await _read_upload(file)
        except workflow.WorkflowError as exc:
            flash(request, str(exc), "error")
            return redirect(f"/admin/documents/{doc_id}#annexes")

        def fn():
            workflow.add_reference(db, user, doc, title=title, issuer=issuer, edition=edition, url=url, file=upload)
    response = _run(request, db, doc_id, fn, "External reference linked.")
    response.headers["location"] += "#annexes"
    return response


@router.post("/documents/{doc_id}/references/{ref_id}/unlink", dependencies=[Depends(csrf_protect)])
def unlink_reference(doc_id: int, ref_id: int, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require_contributor)):
    doc = _get_doc(db, doc_id, user)
    ref = db.get(ExternalReference, ref_id)
    if ref is None:
        raise HTTPException(404)
    response = _run(request, db, doc_id, lambda: workflow.unlink_reference(db, user, doc, ref),
                    "External reference unlinked.")
    response.headers["location"] += "#annexes"
    return response


@router.get("/references/{ref_id}/file")
def reference_file(ref_id: int, db: Session = Depends(get_db), user: User = Depends(require_contributor)):
    ref = db.get(ExternalReference, ref_id)
    if ref is None or not ref.storage_path:
        raise HTTPException(404)
    return FileResponse(storage.absolute(ref.storage_path), filename=ref.filename)


ACTIONS = {
    "submit": (lambda db, u, d, note, bump: workflow.submit(db, u, d), "Submitted. The next people were notified."),
    "review_done": (lambda db, u, d, note, bump: workflow.review_done(db, u, d, note), "Review recorded. Thank you!"),
    "close_review": (lambda db, u, d, note, bump: workflow.close_review(db, u, d, note), "Review closed."),
    "approve": (lambda db, u, d, note, bump: workflow.approve(db, u, d, note), "Approval recorded."),
    "reject": (lambda db, u, d, note, bump: workflow.reject(db, u, d, note), "Sent back to the owner."),
    "new_revision": (lambda db, u, d, note, bump: workflow.start_revision(db, u, d, bump, note),
                     "New revision started; the owner was notified."),
    "retire": (lambda db, u, d, note, bump: workflow.retire(db, u, d, note), "Document withdrawn from the portal."),
}


@router.post("/documents/{doc_id}/action/{name}", dependencies=[Depends(csrf_protect)])
def action(doc_id: int, name: str, request: Request, note: str = Form(""), bump: str = Form("minor"),
           db: Session = Depends(get_db), user: User = Depends(require_contributor)):
    if name not in ACTIONS:
        raise HTTPException(404)
    doc = _get_doc(db, doc_id, user)
    fn, ok = ACTIONS[name]
    return _run(request, db, doc_id, lambda: fn(db, user, doc, note, "major" if bump == "major" else "minor"), ok)


# ---------------------------------------------------------------------------
# Users (administrators)
# ---------------------------------------------------------------------------

@router.get("/users")
def users_page(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    users = db.query(User).order_by(User.active.desc(), User.email).all()
    return render(request, db, "admin/users.html", user=user, users=users, roles=ROLES)


@router.post("/users/new", dependencies=[Depends(csrf_protect)])
def create_user(request: Request, email: str = Form(""), name: str = Form(""), role: str = Form("contributor"),
                db: Session = Depends(get_db), user: User = Depends(require_admin)):
    try:
        new = accounts.create_user(db, email, name, role, user, site_settings.get_all(db))
        workflow.log_admin(db, user, "User invited", f"{new.email} as {new.role}")
        db.commit()
        flash(request, f"{new.email} invited by e-mail.", "success")
    except accounts.AccountError as exc:
        db.rollback()
        flash(request, str(exc), "error")
    return redirect("/admin/users")


@router.post("/users/{user_id}", dependencies=[Depends(csrf_protect)])
def update_user(user_id: int, request: Request, name: str = Form(""), role: str = Form(""),
                active: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_admin)):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404)
    if role not in ROLES:
        flash(request, "Unknown role.", "error")
        return redirect("/admin/users")
    if target.id == user.id and (role != "admin" or active != "1"):
        flash(request, "You cannot remove your own administrator rights or deactivate yourself.", "error")
        return redirect("/admin/users")
    before = f"{target.role}, {'active' if target.active else 'inactive'}"
    target.name, target.role, target.active = name.strip()[:200], role, active == "1"
    workflow.log_admin(db, user, "User changed",
                       f"{target.email}: {before} -> {target.role}, {'active' if target.active else 'inactive'}")
    db.commit()
    flash(request, f"{target.email} updated.", "success")
    return redirect("/admin/users")


@router.post("/users/{user_id}/invite", dependencies=[Depends(csrf_protect)])
def reinvite(user_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404)
    accounts.send_invitation(db, target, user, site_settings.get_all(db))
    db.commit()
    flash(request, f"A new invitation was sent to {target.email}.", "success")
    return redirect("/admin/users")


# ---------------------------------------------------------------------------
# Taxonomies and terms (Document Managers)
# ---------------------------------------------------------------------------

@router.get("/taxonomies")
def taxonomies_page(request: Request, db: Session = Depends(get_db), user: User = Depends(require_manager)):
    return render(request, db, "admin/taxonomies.html", user=user, taxonomies=_taxonomies(db),
                  cfg_export_tag=get_config().export_tag)


@router.post("/taxonomies/new", dependencies=[Depends(csrf_protect)])
def create_taxonomy(request: Request, name: str = Form(""), description: str = Form(""),
                    db: Session = Depends(get_db), user: User = Depends(require_manager)):
    if not name.strip():
        flash(request, "The name is required.", "error")
        return redirect("/admin/taxonomies")
    slug, n = _slug(name), 1
    while db.query(Taxonomy).filter_by(slug=slug).first():
        n += 1
        slug = f"{_slug(name)}-{n}"
    db.add(Taxonomy(name=name.strip()[:100], slug=slug, description=description.strip(),
                    sort=db.query(Taxonomy).count()))
    workflow.log_admin(db, user, "Taxonomy created", name.strip())
    db.commit()
    return redirect("/admin/taxonomies")


@router.post("/taxonomies/{tax_id}", dependencies=[Depends(csrf_protect)])
def update_taxonomy(tax_id: int, request: Request, name: str = Form(""), description: str = Form(""),
                    sort: int = Form(0), delete: str = Form(""), db: Session = Depends(get_db),
                    user: User = Depends(require_manager)):
    tax = db.get(Taxonomy, tax_id)
    if tax is None:
        raise HTTPException(404)
    if delete == "1":
        workflow.log_admin(db, user, "Taxonomy deleted", tax.name)
        db.delete(tax)
    else:
        tax.name, tax.description, tax.sort = name.strip()[:100] or tax.name, description.strip(), sort
    db.commit()
    return redirect("/admin/taxonomies")


@router.post("/taxonomies/{tax_id}/terms", dependencies=[Depends(csrf_protect)])
def create_term(tax_id: int, request: Request, name: str = Form(""), parent_id: str = Form(""),
                description: str = Form(""), code: str = Form(""), db: Session = Depends(get_db),
                user: User = Depends(require_manager)):
    tax = db.get(Taxonomy, tax_id)
    if tax is None or not name.strip():
        flash(request, "The term name is required.", "error")
        return redirect("/admin/taxonomies")
    parent = db.get(Term, int(parent_id)) if parent_id.isdigit() else None
    slug, n = _slug(name), 1
    while db.query(Term).filter_by(taxonomy_id=tax.id, slug=slug).first():
        n += 1
        slug = f"{_slug(name)}-{n}"
    db.add(Term(taxonomy=tax, name=name.strip()[:150], slug=slug, description=description.strip(),
                code=_code(code),
                parent_id=parent.id if parent and parent.taxonomy_id == tax.id else None))
    db.commit()
    return redirect(f"/admin/taxonomies#tax-{tax.id}")


def _code(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", value or "").upper()[:20]


@router.post("/terms/{term_id}", dependencies=[Depends(csrf_protect)])
def update_term(term_id: int, request: Request, name: str = Form(""), parent_id: str = Form(""),
                sort: int = Form(0), delete: str = Form(""), code: str = Form(""), db: Session = Depends(get_db),
                user: User = Depends(require_manager)):
    term = db.get(Term, term_id)
    if term is None:
        raise HTTPException(404)
    tax_id = term.taxonomy_id
    if delete == "1":
        for child in db.query(Term).filter_by(parent_id=term.id).all():
            child.parent_id = term.parent_id
        db.delete(term)
    else:
        term.name, term.sort, term.code = name.strip()[:150] or term.name, sort, _code(code)
        new_parent = int(parent_id) if parent_id.isdigit() else None
        # Refuse cycles: the new parent may not be the term itself or one of its descendants.
        ancestor = db.get(Term, new_parent) if new_parent else None
        while ancestor is not None and ancestor.id != term.id:
            ancestor = db.get(Term, ancestor.parent_id) if ancestor.parent_id else None
        if ancestor is not None:
            flash(request, "A term cannot be placed under itself.", "error")
        else:
            term.parent_id = new_parent
    db.commit()
    return redirect(f"/admin/taxonomies#tax-{tax_id}")


# ---------------------------------------------------------------------------
# Site settings (administrators)
# ---------------------------------------------------------------------------

@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return render(request, db, "admin/settings.html", user=user, cfg=get_config(), icon_names=sorted(icons.ICONS))


@router.post("/settings/integrations", dependencies=[Depends(csrf_protect)])
def save_integrations(request: Request, webhook_urls: str = Form(""), export_auto: str = Form(""),
                      test: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_admin)):
    urls = [u.strip() for u in re.split(r"[\s,]+", webhook_urls) if u.strip()]
    bad = [u for u in urls if not u.startswith("https://")]
    if bad:
        flash(request, "Webhook URLs must start with https://: " + ", ".join(bad), "error")
        return redirect("/admin/settings#integrations")
    site_settings.set_many(db, {"webhook_urls": urls, "export_auto": export_auto == "1"})
    workflow.log_admin(db, user, "Integration settings changed", f"{len(urls)} webhook(s)")
    db.commit()
    if test == "1":
        for url in urls:
            try:
                flash(request, f"Test message to {url[:40]}...: {insights.test_webhook(url)}")
            except Exception as exc:
                flash(request, f"Test message to {url[:40]}... failed: {exc}", "error")
    flash(request, "Integrations saved.", "success")
    return redirect("/admin/settings#integrations")


@router.post("/settings/look", dependencies=[Depends(csrf_protect)])
async def save_look(request: Request, site_name: str = Form(""), tagline: str = Form(""),
                    banner_html: str = Form(""), footer_html: str = Form(""),
                    logo: UploadFile | None = File(None), banner: UploadFile | None = File(None),
                    remove_logo: str = Form(""), remove_banner: str = Form(""),
                    db: Session = Depends(get_db), user: User = Depends(require_admin)):
    form = await request.form()
    values = {"site_name": site_name.strip()[:100] or "Promulga", "tagline": tagline.strip()[:300],
              "banner_html": banner_html, "footer_html": footer_html}
    for key in site_settings.COLOR_KEYS:
        color = str(form.get(key, "")).strip()
        if HEX_COLOR.match(color):
            values[key] = color
    for field, upload, remove in (("logo_asset", logo, remove_logo), ("banner_asset", banner, remove_banner)):
        if remove == "1":
            values[field] = ""
        elif upload is not None and upload.filename:
            if storage.extension(upload.filename) not in storage.IMAGE_EXTENSIONS:
                flash(request, "Images must be PNG, JPG, GIF, SVG or WEBP.", "error")
                return redirect("/admin/settings")
            values[field] = storage.save_asset(upload.filename, await upload.read(5 * 1024 * 1024))
    site_settings.set_many(db, values)
    workflow.log_admin(db, user, "Site appearance changed")
    db.commit()
    flash(request, "Appearance saved.", "success")
    return redirect("/admin/settings")


@router.post("/settings/workflow", dependencies=[Depends(csrf_protect)])
async def save_workflow(request: Request, draft_days: int = Form(28), review_days: int = Form(14),
                        revision_days: int = Form(14), approval_days: int = Form(14),
                        reminder_before_days: int = Form(3), overdue_repeat_days: int = Form(1),
                        reminder_hour: int = Form(8), manager_digest: str = Form(""),
                        notify_on_publish: str = Form(""), doc_types: str = Form(""),
                        review_months: int = Form(12), review_reminder_days: int = Form(30),
                        template: UploadFile | None = File(None), remove_template: str = Form(""),
                        type_template: UploadFile | None = File(None), type_template_for: str = Form(""),
                        remove_type_templates: list[str] = Form([]), cover_copyright: str = Form(""),
                        default_approvers: str = Form(""),
                        db: Session = Depends(get_db), user: User = Depends(require_admin)):
    types = []
    for line in doc_types.splitlines():
        parts = [p.strip() for p in line.split("=")]
        if len(parts) >= 2 and parts[0] and re.fullmatch(r"[A-Z0-9]{2,8}", parts[1].upper()):
            icon = parts[2] if len(parts) > 2 and parts[2] in icons.ICONS else icons.DEFAULT_ICON
            types.append({"name": parts[0][:50], "prefix": parts[1].upper(), "icon": icon})
    if not types:
        flash(request, "Give at least one document type, one per line, as 'Name=PREFIX=icon'.", "error")
        return redirect("/admin/settings#workflow")
    days = [draft_days, review_days, revision_days, approval_days]
    if any(d < 1 or d > 365 for d in days):
        flash(request, "Stage durations must be between 1 and 365 days.", "error")
        return redirect("/admin/settings#workflow")
    values = {
        "stage_days": {"Draft": draft_days, "Review": review_days, "Revision": revision_days,
                       "Approval": approval_days},
        "reminder_before_days": max(0, min(60, reminder_before_days)),
        "overdue_repeat_days": max(0, min(60, overdue_repeat_days)),
        "reminder_hour": max(0, min(23, reminder_hour)),
        "manager_digest": manager_digest == "1",
        "notify_on_publish": notify_on_publish == "1",
        "doc_types": types,
        "review_months": max(0, min(120, review_months)),
        "review_reminder_days": max(0, min(365, review_reminder_days)),
        "default_approvers": accounts.parse_emails(default_approvers),
    }
    if remove_template == "1":
        values["template_asset"] = ""
    elif template is not None and template.filename:
        if storage.extension(template.filename) not in {".docx", ".odt", ".doc"}:
            flash(request, "The template must be a .docx, .doc or .odt file.", "error")
            return redirect("/admin/settings#workflow")
        values["template_asset"] = storage.save_asset(template.filename, await template.read(10 * 1024 * 1024))
    type_names = {t["name"] for t in types}
    per_type = {k: v for k, v in (site_settings.get(db, "type_templates") or {}).items()
                if k in type_names and k not in remove_type_templates}
    if type_template is not None and type_template.filename:
        if type_template_for not in type_names:
            flash(request, "Choose the document type of the template.", "error")
            return redirect("/admin/settings#workflow")
        if storage.extension(type_template.filename) not in {".docx", ".odt", ".doc"}:
            flash(request, "The template must be a .docx, .doc or .odt file.", "error")
            return redirect("/admin/settings#workflow")
        per_type[type_template_for] = storage.save_asset(type_template.filename,
                                                         await type_template.read(10 * 1024 * 1024))
    values["type_templates"] = per_type
    values["cover_copyright"] = cover_copyright.strip()[:300]
    site_settings.set_many(db, values)
    workflow.log_admin(db, user, "Workflow settings changed",
                       f"days {days}, before {values['reminder_before_days']}, repeat {values['overdue_repeat_days']}")
    db.commit()
    flash(request, "Deadlines and alerts saved. They apply to stages started from now on.", "success")
    return redirect("/admin/settings#workflow")


@router.post("/settings/portal", dependencies=[Depends(csrf_protect)])
def save_portal(request: Request, portal_access: str = Form("public"), portal_show_owner: str = Form(""),
                db: Session = Depends(get_db), user: User = Depends(require_admin)):
    site_settings.set_many(db, {"portal_access": "login" if portal_access == "login" else "public",
                                "portal_show_owner": portal_show_owner == "1"})
    workflow.log_admin(db, user, "Portal settings changed", portal_access)
    db.commit()
    flash(request, "Portal settings saved.", "success")
    return redirect("/admin/settings#portal")


@router.get("/settings/template")
def download_template(type: str = "", db: Session = Depends(get_db), user: User = Depends(require_manager)):
    name = ((site_settings.get(db, "type_templates") or {}).get(type) if type
            else site_settings.get(db, "template_asset"))
    path = storage.asset_path(name or "")
    if path is None:
        raise HTTPException(404)
    return FileResponse(path, filename=f"template{path.suffix}")


# ---------------------------------------------------------------------------
# Outbox, reminders, audit log
# ---------------------------------------------------------------------------

@router.get("/outbox")
def outbox(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    mails = db.query(OutboxMail).order_by(OutboxMail.id.desc()).limit(200).all()
    return render(request, db, "admin/outbox.html", user=user, mails=mails)


@router.get("/outbox/{mail_id}", response_class=HTMLResponse)
def outbox_mail(mail_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    mail = db.get(OutboxMail, mail_id)
    if mail is None:
        raise HTTPException(404)
    # Shown inside a sandboxed iframe; the CSP forbids scripts anyway.
    return HTMLResponse(mail.html, headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src *"})


@router.post("/outbox/flush", dependencies=[Depends(csrf_protect)])
def outbox_flush(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    if not get_config().smtp_enabled:
        flash(request, "SMTP is not configured: e-mails stay in the outbox (see INSTALL.md).", "error")
    else:
        sent = mailer.flush_outbox(db)
        db.commit()
        flash(request, f"{sent} e-mail(s) sent.", "success")
    return redirect("/admin/outbox")


@router.post("/reminders/run", dependencies=[Depends(csrf_protect)])
def run_reminders(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    queued = reminders.run(db)
    workflow.log_admin(db, user, "Reminders run manually", f"{queued} e-mail(s)")
    db.commit()
    flash(request, f"{queued} reminder e-mail(s) queued.", "success")
    return redirect("/admin/outbox")


def _audit_query(db: Session, q: str, ref: str):
    query = db.query(Event)
    if ref:
        query = query.filter(Event.document_ref == ref.strip().upper())
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(Event.action.ilike(like), Event.actor_label.ilike(like), Event.detail.ilike(like)))
    return query.order_by(Event.id.desc())


@router.get("/audit")
def audit(request: Request, q: str = "", ref: str = "", db: Session = Depends(get_db),
          user: User = Depends(require_manager)):
    return render(request, db, "admin/audit.html", user=user, events=_audit_query(db, q, ref).limit(500).all(),
                  q=q, ref=ref)


@router.get("/audit.csv")
def audit_csv(q: str = "", ref: str = "", db: Session = Depends(get_db), user: User = Depends(require_manager)):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Timestamp (UTC)", "Document", "Version", "From", "To", "Action", "Actor", "Detail"])
    for e in _audit_query(db, q, ref).all():
        w.writerow([e.created_at.strftime("%Y-%m-%d %H:%M:%S"), e.document_ref, e.version, e.from_status,
                    e.to_status, e.action, e.actor_label, e.detail])
    return StreamingResponse(iter([out.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=audit-{date.today()}.csv"})


# ---------------------------------------------------------------------------
# My account
# ---------------------------------------------------------------------------

@router.get("/account")
def account(request: Request, db: Session = Depends(get_db), user: User = Depends(require_contributor)):
    people = db.query(User).filter(User.active.is_(True), User.role != "viewer", User.id != user.id) \
        .order_by(User.email).all()
    passkeys = db.query(PasskeyCredential).filter_by(user_id=user.id).order_by(PasskeyCredential.id).all()
    return render(request, db, "admin/account.html", user=user, people=people, passkeys=passkeys)


@router.post("/account/passkeys/{key_id}/delete", dependencies=[Depends(csrf_protect)])
def delete_passkey(key_id: int, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_contributor)):
    key = db.get(PasskeyCredential, key_id)
    if key is None or key.user_id != user.id:
        raise HTTPException(404)
    workflow.log_admin(db, user, "Passkey removed", key.name)
    db.delete(key)
    db.commit()
    flash(request, "Passkey removed.", "success")
    return redirect("/admin/account#passkeys")


@router.post("/account/absence", dependencies=[Depends(csrf_protect)])
def update_absence(request: Request, deputy_id: str = Form(""), absent_until: str = Form(""),
                   db: Session = Depends(get_db), user: User = Depends(require_contributor)):
    deputy = db.get(User, int(deputy_id)) if deputy_id.isdigit() else None
    if deputy is not None and (deputy.id == user.id or not deputy.active):
        deputy = None
    try:
        until = date.fromisoformat(absent_until) if absent_until else None
    except ValueError:
        until = None
    if until and not deputy:
        flash(request, "Choose a deputy who will act for you during your absence.", "error")
        return redirect("/admin/account")
    user.deputy_id = deputy.id if deputy else None
    user.absent_until = until
    workflow.log_admin(db, user, "Absence and deputy changed",
                       f"absent until {until or '-'}, deputy {deputy.email if deputy else '-'}")
    db.commit()
    flash(request, "Saved." + (f" {deputy.display} can act for you until {until}." if until else ""), "success")
    return redirect("/admin/account")


@router.post("/account", dependencies=[Depends(csrf_protect)])
def update_account(request: Request, name: str = Form(""), current: str = Form(""), password: str = Form(""),
                   password2: str = Form(""), db: Session = Depends(get_db),
                   user: User = Depends(require_contributor)):
    user.name = name.strip()[:200]
    if user.must_change_password and not password:
        db.rollback()
        flash(request, "Please choose a new password.", "error")
        return redirect("/admin/account")
    if password:
        try:
            if user.password_hash and not verify_password(user, current):
                raise ValueError("The current password is wrong.")
            if password != password2:
                raise ValueError("The two new passwords are different.")
            check_password_strength(password)
        except ValueError as exc:
            db.rollback()
            flash(request, str(exc), "error")
            return redirect("/admin/account")
        user.password_hash = hash_password(password)
        user.must_change_password = False
        workflow.log_admin(db, user, "Password changed")
    db.commit()
    login_session(request, user)
    flash(request, "Account saved.", "success")
    return redirect("/admin/account")


# ---------------------------------------------------------------------------
# Online editor (OnlyOffice)
# ---------------------------------------------------------------------------

@router.get("/documents/{doc_id}/editor")
def online_editor(doc_id: int, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_contributor)):
    doc = _get_doc(db, doc_id, user)
    rev = doc.latest_revision
    if not onlyoffice.available(rev):
        flash(request, "The online editor is not available for this document.", "error")
        return redirect(f"/admin/documents/{doc_id}")
    config = onlyoffice.editor_config(doc, rev, user)
    return render(request, db, "admin/editor.html", user=user, doc=doc, config=config,
                  api_js=f"{get_config().onlyoffice_url}/web-apps/apps/api/documents/api.js")


# ---------------------------------------------------------------------------
# Distribution and read & acknowledge (owner of the document, Document Managers)
# ---------------------------------------------------------------------------

@router.post("/documents/{doc_id}/acknowledgments/remind", dependencies=[Depends(csrf_protect)])
def remind_acknowledgments(doc_id: int, request: Request, db: Session = Depends(get_db),
                           user: User = Depends(require_contributor)):
    doc = _get_doc(db, doc_id, user)
    return _run(request, db, doc_id, lambda: workflow.remind_acknowledgments(db, user, doc),
                "Reminder sent to the people who have not acknowledged yet.")


@router.get("/documents/{doc_id}/acknowledgments.csv")
def acknowledgments_csv(doc_id: int, db: Session = Depends(get_db), user: User = Depends(require_contributor)):
    doc = _get_doc(db, doc_id, user)
    if not workflow.follows_distribution(doc, user):
        raise HTTPException(403, "Only the owner of the document or a Document Manager sees its distribution list.")
    dist = workflow.distribution(db, doc)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["E-mail", "Name", "Version", "Read", "Acknowledged at (UTC)"])
    version = dist["publication"].version if dist["publication"] else ""
    for r in dist["rows"]:
        a = r["ack"]
        read = ("yes" if a else "no") if dist["tracked"] else "not tracked"
        w.writerow([r["user"].email, r["user"].name, version, read,
                    a.acknowledged_at.strftime("%Y-%m-%d %H:%M") if a else ""])
    return StreamingResponse(iter([out.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={doc.ref}-acknowledgments.csv"})


# ---------------------------------------------------------------------------
# Indicators, security checks
# ---------------------------------------------------------------------------

@router.get("/indicators")
def indicators(request: Request, months: int = 12, db: Session = Depends(get_db),
               user: User = Depends(require_manager)):
    months = max(1, min(60, months))
    return render(request, db, "admin/indicators.html", user=user, k=insights.kpis(db, months, viewer=user))


@router.get("/security")
def security(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return render(request, db, "admin/security.html", user=user, checks=insights.security_checks(db))


# ---------------------------------------------------------------------------
# Public export
# ---------------------------------------------------------------------------

@router.get("/export")
def export_page(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return render(request, db, "admin/export.html", user=user, cfg=get_config(),
                  docs=export.exported_documents(db))


@router.post("/export/scope", dependencies=[Depends(csrf_protect)])
def export_scope(request: Request, scope: str = Form("public"), db: Session = Depends(get_db),
                 user: User = Depends(require_admin)):
    scope = "all" if scope == "all" else "public"
    site_settings.set_many(db, {"export_scope": scope})
    workflow.log_admin(db, user, "Export scope changed", scope)
    db.commit()
    flash(request, "Every published document will be exported." if scope == "all"
          else "Only the public documents will be exported.", "success")
    return redirect("/admin/export")


@router.get("/export/package.zip")
def export_zip(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    data = export.zip_bytes(export.build(db))
    workflow.log_admin(db, user, "Public package downloaded", f"{len(data)} bytes")
    db.commit()
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f"attachment; filename=public-documents-{date.today()}.zip"})


@router.post("/export/github", dependencies=[Depends(csrf_protect)])
def export_github(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    try:
        files = export.build(db)
        sha = export.push_github(files, f"Promulga: {len(export.exported_documents(db))} public document(s)")
    except export.ExportError as exc:
        flash(request, str(exc), "error")
        return redirect("/admin/export")
    site_settings.set_many(db, {"last_export": f"{date.today()} {sha[:10] or 'no change'}", "export_pending": False})
    workflow.log_admin(db, user, "Public package pushed to GitHub", sha or "no change")
    db.commit()
    flash(request, f"Pushed to GitHub: commit {sha[:10]}." if sha else "GitHub is already up to date.", "success")
    return redirect("/admin/export")


# ---------------------------------------------------------------------------
# API tokens (administrators)
# ---------------------------------------------------------------------------

@router.get("/api-tokens")
def api_tokens(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    tokens = db.query(ApiToken).order_by(ApiToken.revoked, ApiToken.id.desc()).all()
    new_token = request.session.pop("new_api_token", None)
    return render(request, db, "admin/api_tokens.html", user=user, tokens=tokens, new_token=new_token)


@router.post("/api-tokens", dependencies=[Depends(csrf_protect)])
def create_api_token(request: Request, name: str = Form(""), db: Session = Depends(get_db),
                     user: User = Depends(require_admin)):
    token = "prm_" + secrets.token_urlsafe(32)
    db.add(ApiToken(name=(name.strip() or "API client")[:100], created_by_id=user.id,
                    token_hash=hashlib.sha256(token.encode()).hexdigest()))
    workflow.log_admin(db, user, "API token created", name.strip())
    db.commit()
    request.session["new_api_token"] = token     # shown once, on the next page
    return redirect("/admin/api-tokens")


@router.post("/api-tokens/{token_id}/revoke", dependencies=[Depends(csrf_protect)])
def revoke_api_token(token_id: int, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require_admin)):
    row = db.get(ApiToken, token_id)
    if row:
        row.revoked = True
        workflow.log_admin(db, user, "API token revoked", row.name)
        db.commit()
    return redirect("/admin/api-tokens")
