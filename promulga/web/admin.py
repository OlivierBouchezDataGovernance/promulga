"""Admin site: tasks, register, document pages and workflow actions, users,
taxonomies, site settings, outbox and audit log.

Every POST route is CSRF-protected and every workflow rule is enforced in
:mod:`promulga.workflow`; the routes only translate HTTP into function calls,
commit, and redirect back with a message.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from .. import accounts, icons, mailer, reminders, site_settings, storage, workflow
from ..config import get_config
from ..db import get_db
from ..models import (
    ACTIVE_STATUSES, APPROVAL, REVIEW, ROLES, STATUSES, Assignment, Comment, Document, Event, OutboxMail,
    Publication, Revision, Taxonomy, Term, User,
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


def _get_doc(db: Session, doc_id: int, user: User) -> Document:
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404)
    if not workflow.can_see(doc, user):
        raise HTTPException(403, "You do not take part in this document.")
    return doc


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
    following = [d for d in active if workflow.is_participant(d, user)
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
    docs = _register_query(db, user, status, type, q, mine).all()
    return render(request, db, "admin/register.html", user=user, docs=docs, statuses=STATUSES,
                  status=status, type=type, q=q, mine=mine, pending=workflow.pending_users)


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
    users = db.query(User).filter(User.active.is_(True), User.role != "viewer").order_by(User.email).all()
    return render(request, db, "admin/document_form.html", user=user, doc=None, taxonomies=_taxonomies(db),
                  users=users, selected_terms=set(), form={})


@router.post("/documents/new", dependencies=[Depends(csrf_protect)])
async def new_document(request: Request, title: str = Form(""), doc_type: str = Form(""), summary: str = Form(""),
                       owner: str = Form(""), reviewers: str = Form(""), approvers: str = Form(""),
                       terms: list[str] = Form([]), file: UploadFile | None = File(None),
                       db: Session = Depends(get_db), user: User = Depends(require_manager)):
    try:
        doc = workflow.create_document(
            db, user, title=title, doc_type=doc_type, summary=summary, owner_email=owner,
            reviewer_emails=accounts.parse_emails(reviewers), approver_emails=accounts.parse_emails(approvers),
            term_ids=_int_list(terms), file=await _read_upload(file))
        db.commit()
    except (workflow.WorkflowError, accounts.AccountError, ValueError) as exc:
        db.rollback()
        flash(request, str(exc), "error")
        users = db.query(User).filter(User.active.is_(True), User.role != "viewer").order_by(User.email).all()
        return render(request, db, "admin/document_form.html", user=user, doc=None, taxonomies=_taxonomies(db),
                      users=users, selected_terms=set(_int_list(terms)), status_code=400,
                      form={"title": title, "doc_type": doc_type, "summary": summary, "owner": owner,
                            "reviewers": reviewers, "approvers": approvers},
                      messages=request.session.pop("flash", []))
    flash(request, f"{doc.ref} created. The owner received an e-mail.", "success")
    return redirect(f"/admin/documents/{doc.id}")


@router.get("/documents/{doc_id}/edit")
def edit_document_form(doc_id: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require_manager)):
    doc = _get_doc(db, doc_id, user)
    users = db.query(User).filter(User.active.is_(True), User.role != "viewer").order_by(User.email).all()
    form = {"title": doc.title, "doc_type": doc.doc_type, "summary": doc.summary, "owner": doc.owner.email,
            "reviewers": ", ".join(a.user.email for a in doc.reviewers),
            "approvers": ", ".join(a.user.email for a in doc.approvers)}
    return render(request, db, "admin/document_form.html", user=user, doc=doc, taxonomies=_taxonomies(db),
                  users=users, selected_terms={t.id for t in doc.terms}, form=form)


@router.post("/documents/{doc_id}/edit", dependencies=[Depends(csrf_protect)])
def edit_document(doc_id: int, request: Request, title: str = Form(""), summary: str = Form(""),
                  owner: str = Form(""), reviewers: str = Form(""), approvers: str = Form(""),
                  terms: list[str] = Form([]), db: Session = Depends(get_db), user: User = Depends(require_manager)):
    doc = _get_doc(db, doc_id, user)
    try:
        workflow.update_metadata(db, user, doc, title=title, summary=summary, term_ids=_int_list(terms))
        new_people = (owner.strip().lower(), accounts.parse_emails(reviewers), accounts.parse_emails(approvers))
        old_people = (doc.owner.email, [a.user.email for a in doc.reviewers], [a.user.email for a in doc.approvers])
        if new_people != old_people:
            workflow.update_participants(db, user, doc, owner_email=owner, reviewer_emails=new_people[1],
                                         approver_emails=new_people[2])
        db.commit()
        flash(request, "Document updated.", "success")
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
    return render(request, db, "admin/document.html", user=user, doc=doc, events=events, actions=actions,
                  has_task=bool(actions - {"comment", "edit"}), max_mb=get_config().max_upload_mb,
                  pending=workflow.pending_users(doc),
                  comments=top, replies=replies, open_comments=sum(1 for c in top if not c.resolved))


@router.get("/documents/{doc_id}/revisions/{rev_id}")
def download_revision(doc_id: int, rev_id: int, db: Session = Depends(get_db),
                      user: User = Depends(require_contributor)):
    doc = _get_doc(db, doc_id, user)
    rev = db.get(Revision, rev_id)
    if rev is None or rev.document_id != doc.id:
        raise HTTPException(404)
    name = f"{doc.ref} - r{rev.number} - {rev.filename}"
    return FileResponse(storage.absolute(rev.storage_path), media_type=rev.content_type, filename=name)


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
    return FileResponse(storage.absolute(pub.pdf_path), media_type="application/pdf",
                        filename=f"{doc.ref} - v{pub.version}.pdf", content_disposition_type="inline")


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
    return _run(request, db, doc_id, lambda: workflow.check_in(db, user, doc, upload[0], upload[1], note),
                "New revision checked in.")


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
    return render(request, db, "admin/taxonomies.html", user=user, taxonomies=_taxonomies(db))


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
                description: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_manager)):
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
                parent_id=parent.id if parent and parent.taxonomy_id == tax.id else None))
    db.commit()
    return redirect(f"/admin/taxonomies#tax-{tax.id}")


@router.post("/terms/{term_id}", dependencies=[Depends(csrf_protect)])
def update_term(term_id: int, request: Request, name: str = Form(""), parent_id: str = Form(""),
                sort: int = Form(0), delete: str = Form(""), db: Session = Depends(get_db),
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
        term.name, term.sort = name.strip()[:150] or term.name, sort
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
                        template: UploadFile | None = File(None), remove_template: str = Form(""),
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
    }
    if remove_template == "1":
        values["template_asset"] = ""
    elif template is not None and template.filename:
        if storage.extension(template.filename) not in {".docx", ".odt", ".doc"}:
            flash(request, "The template must be a .docx, .doc or .odt file.", "error")
            return redirect("/admin/settings#workflow")
        values["template_asset"] = storage.save_asset(template.filename, await template.read(10 * 1024 * 1024))
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
def download_template(db: Session = Depends(get_db), user: User = Depends(require_manager)):
    path = storage.asset_path(site_settings.get(db, "template_asset"))
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
    return render(request, db, "admin/account.html", user=user)


@router.post("/account", dependencies=[Depends(csrf_protect)])
def update_account(request: Request, name: str = Form(""), current: str = Form(""), password: str = Form(""),
                   password2: str = Form(""), db: Session = Depends(get_db),
                   user: User = Depends(require_contributor)):
    user.name = name.strip()[:200]
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
        workflow.log_admin(db, user, "Password changed")
    db.commit()
    login_session(request, user)
    flash(request, "Account saved.", "success")
    return redirect("/admin/account")
