"""Public portal: browse published documents by taxonomy, search, read the official PDF.

Only *published* versions are visible here. A document that is being revised
(e.g. 1.1 in Draft) stays visible with its last published version (1.0).
Retired documents disappear from the portal.

Access is public by default; the administrator can require a login (any
account, including the 'viewer' role), and the server can restrict the portal
to intranet addresses (PORTAL_ALLOWED_IPS).

Documents classified "Limited distribution" or "Confidential" are shown only
to signed-in participants of the document and to Document Managers.
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from sqlalchemy.orm import Session, selectinload

from .. import naming, site_settings, storage, workflow
from ..db import get_db
from ..models import (RETIRED, TRANSLATION_LANGUAGES, Acknowledgment, Document, ExternalReference, Publication,
                      Taxonomy, Term, Translation, User)
from .deps import LoginRequired, csrf_protect, current_user, flash, redirect, render

router = APIRouter()


def portal_user(request: Request, db: Session = Depends(get_db),
                user: User | None = Depends(current_user)) -> User | None:
    """Enforces the portal access setting; returns the (optional) user."""
    if user is None and site_settings.get(db, "portal_access") == "login":
        raise LoginRequired(request.url.path)
    return user


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def visible_documents(db: Session, user: User | None) -> list[Document]:
    docs = (db.query(Document)
            .options(selectinload(Document.terms), selectinload(Document.publications), selectinload(Document.owner),
                     selectinload(Document.assignments))
            .filter(Document.current_version != "", Document.status != RETIRED)
            .order_by(Document.ref).all())
    return [d for d in docs if d.readable_by(user)]


def term_tree(taxonomy: Taxonomy) -> tuple[dict[int | None, list[Term]], dict[int, Term]]:
    children: dict[int | None, list[Term]] = defaultdict(list)
    by_id = {t.id: t for t in taxonomy.terms}
    for t in taxonomy.terms:
        parent = t.parent_id if t.parent_id in by_id else None
        children[parent].append(t)
    return children, by_id


def descendants(children: dict[int | None, list[Term]], term_id: int) -> set[int]:
    out, stack = {term_id}, [term_id]
    while stack:
        for child in children.get(stack.pop(), []):
            if child.id not in out:
                out.add(child.id)
                stack.append(child.id)
    return out


def breadcrumb(by_id: dict[int, Term], term: Term) -> list[Term]:
    path, current, seen = [], term, set()
    while current is not None and current.id not in seen:
        seen.add(current.id)
        path.insert(0, current)
        current = by_id.get(current.parent_id) if current.parent_id else None
    return path


def _count_by_term(docs: list[Document], children, term_ids) -> dict[int, int]:
    counts = {}
    for tid in term_ids:
        scope = descendants(children, tid)
        counts[tid] = sum(1 for d in docs if any(t.id in scope for t in d.terms))
    return counts


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@router.get("/")
def home(request: Request, db: Session = Depends(get_db), user: User | None = Depends(portal_user)):
    docs = visible_documents(db, user)
    taxonomies = db.query(Taxonomy).order_by(Taxonomy.sort, Taxonomy.name).all()
    readable = {d.id for d in docs}
    latest = [p for p in (db.query(Publication).join(Document)
                          .filter(Document.status != RETIRED)
                          .order_by(Publication.published_at.desc(), Publication.id.desc()).limit(60).all())
              if p.document_id in readable][:6]
    cards = []
    for tax in taxonomies:
        children, _ = term_tree(tax)
        roots = children.get(None, [])
        counts = _count_by_term(docs, children, [t.id for t in roots])
        cards.append({"taxonomy": tax, "roots": roots, "counts": counts})
    order = [t["name"] for t in site_settings.get(db, "doc_types")]
    counts: dict[str, int] = {}
    for d in docs:
        counts[d.doc_type] = counts.get(d.doc_type, 0) + 1
    types = sorted(counts.items(), key=lambda kv: (order.index(kv[0]) if kv[0] in order else 99, kv[0]))
    return render(request, db, "portal/home.html", user=user, cards=cards, latest=latest,
                  total=len(docs), types=types)


@router.get("/browse/{slug}")
def browse_taxonomy(slug: str, request: Request, db: Session = Depends(get_db),
                    user: User | None = Depends(portal_user)):
    tax = db.query(Taxonomy).filter_by(slug=slug).first()
    if tax is None:
        raise HTTPException(404)
    docs = visible_documents(db, user)
    children, _ = term_tree(tax)
    roots = children.get(None, [])
    untagged = [d for d in docs if not any(t.taxonomy_id == tax.id for t in d.terms)]
    return render(request, db, "portal/browse.html", user=user, taxonomy=tax, term=None, path=[],
                  children=roots, counts=_count_by_term(docs, children, [t.id for t in roots]),
                  documents=[], untagged=untagged)


@router.get("/browse/{slug}/{term_id}")
def browse_term(slug: str, term_id: int, request: Request, db: Session = Depends(get_db),
                user: User | None = Depends(portal_user)):
    tax = db.query(Taxonomy).filter_by(slug=slug).first()
    term = db.get(Term, term_id)
    if tax is None or term is None or term.taxonomy_id != tax.id:
        raise HTTPException(404)
    docs = visible_documents(db, user)
    children, by_id = term_tree(tax)
    scope = descendants(children, term.id)
    sub = children.get(term.id, [])
    in_scope = [d for d in docs if any(t.id in scope for t in d.terms)]
    return render(request, db, "portal/browse.html", user=user, taxonomy=tax, term=term,
                  path=breadcrumb(by_id, term), children=sub,
                  counts=_count_by_term(docs, children, [t.id for t in sub]), documents=in_scope, untagged=[])


@router.get("/search")
def search(request: Request, q: str = "", type: str = "", db: Session = Depends(get_db),
           user: User | None = Depends(portal_user)):
    docs = visible_documents(db, user)
    words = [w for w in q.lower().split() if w]
    results = []
    for d in docs:
        if type and d.doc_type != type:
            continue
        pub = d.latest_publication
        haystack_title = f"{d.ref} {d.title}".lower()
        haystack = " ".join([haystack_title, d.summary.lower(), " ".join(t.name.lower() for t in d.terms),
                             (pub.text.lower() if pub else "")])
        if words and not all(w in haystack for w in words):
            continue
        score = sum(3 for w in words if w in haystack_title) + sum(1 for w in words if w in d.summary.lower())
        results.append((score, d))
    results.sort(key=lambda r: (-r[0], r[1].ref))
    types = sorted({d.doc_type for d in docs})
    return render(request, db, "portal/search.html", user=user, q=q, type=type, types=types,
                  documents=[d for _, d in results])


@router.get("/d/{ref}")
def document_page(ref: str, request: Request, db: Session = Depends(get_db),
                  user: User | None = Depends(portal_user)):
    doc = db.query(Document).filter_by(ref=ref).first()
    if doc is None or not doc.current_version:
        raise HTTPException(404)
    if not doc.readable_by(user):
        if user is None:
            raise LoginRequired(f"/d/{ref}")
        raise HTTPException(404)
    acknowledged = None
    if doc.ack_required and user and doc.latest_publication:
        acknowledged = db.query(Acknowledgment).filter_by(publication_id=doc.latest_publication.id,
                                                          user_id=user.id).first()
    def on_portal(d: Document | None) -> bool:
        return d is not None and bool(d.current_version) and d.status != RETIRED and d.readable_by(user)

    translations = [t for t in (doc.translation(lang) for lang in TRANSLATION_LANGUAGES) if t]
    return render(request, db, "portal/document.html", user=user, doc=doc, retired=doc.status == RETIRED,
                  acknowledged=acknowledged, parent=doc.parent if on_portal(doc.parent) else None,
                  children=[c for c in doc.children if on_portal(c)], translations=translations)


def _portal_doc(db: Session, ref: str, user: User | None, path: str) -> Document:
    doc = db.query(Document).filter_by(ref=ref).first()
    if doc is None or doc.status == RETIRED or not doc.current_version:
        raise HTTPException(404)
    if not doc.readable_by(user):
        if user is None:
            raise LoginRequired(path)
        raise HTTPException(404)
    return doc


@router.get("/d/{ref}/translations/{tr_id}")
def document_translation(ref: str, tr_id: int, db: Session = Depends(get_db),
                         user: User | None = Depends(portal_user)):
    doc = _portal_doc(db, ref, user, f"/d/{ref}")
    t = db.get(Translation, tr_id)
    if t is None or t.document_id != doc.id:
        raise HTTPException(404)
    return FileResponse(storage.absolute(t.storage_path), media_type=t.content_type,
                        filename=f"{doc.ref}-v{t.version}-{t.language.upper()}{storage.extension(t.filename)}",
                        content_disposition_type="inline")


@router.get("/d/{ref}/references/{ref_id}")
def document_reference(ref: str, ref_id: int, db: Session = Depends(get_db),
                       user: User | None = Depends(portal_user)):
    doc = _portal_doc(db, ref, user, f"/d/{ref}")
    r = db.get(ExternalReference, ref_id)
    if r is None or r not in doc.references or not r.storage_path:
        raise HTTPException(404)
    return FileResponse(storage.absolute(r.storage_path), filename=r.filename, content_disposition_type="inline")


@router.post("/d/{ref}/acknowledge", dependencies=[Depends(csrf_protect)])
def acknowledge(ref: str, request: Request, db: Session = Depends(get_db),
                user: User | None = Depends(current_user)):
    if user is None:
        raise LoginRequired(f"/d/{ref}")
    doc = db.query(Document).filter_by(ref=ref).first()
    if doc is None or not doc.readable_by(user):
        raise HTTPException(404)
    try:
        workflow.acknowledge(db, user, doc)
        db.commit()
        flash(request, "Thank you: your acknowledgment is recorded.", "success")
    except workflow.WorkflowError as exc:
        db.rollback()
        flash(request, str(exc), "error")
    return redirect(f"/d/{ref}")


@router.get("/d/{ref}/v{version}.pdf")
def document_pdf(ref: str, version: str, download: bool = False, db: Session = Depends(get_db),
                 user: User | None = Depends(portal_user)):
    doc = db.query(Document).filter_by(ref=ref).first()
    if doc is None or doc.status == RETIRED:
        raise HTTPException(404)
    if not doc.readable_by(user):
        if user is None:
            raise LoginRequired(f"/d/{ref}/v{version}.pdf")
        raise HTTPException(404)
    pub = next((p for p in doc.publications if p.version == version), None)
    if pub is None:
        raise HTTPException(404)
    name = naming.file_name(doc, ".pdf", site_settings.get_all(db), version=pub.version, draft=False,
                            published=pub.published_at)
    return FileResponse(storage.absolute(pub.pdf_path), media_type="application/pdf", filename=name,
                        content_disposition_type="attachment" if download else "inline")


# ---------------------------------------------------------------------------
# Theme and assets
# ---------------------------------------------------------------------------

@router.get("/theme.css")
def theme_css(db: Session = Depends(get_db)):
    site = site_settings.get_all(db)
    css = ":root {\n" + "".join(
        f"  --{key.replace('color_', 'c-').replace('_', '-')}: {site[key]};\n" for key in site_settings.THEME_KEYS
    ) + "}\n"
    if site.get("banner_asset"):
        css += f".banner {{ background-image: linear-gradient(160deg, rgba(15,23,42,.80), rgba(15,23,42,.92)), " \
               f"url('/assets/{site['banner_asset']}'); background-size: cover; background-position: center; }}\n"
    return Response(css, media_type="text/css", headers={"Cache-Control": "no-cache"})


@router.get("/assets/{name}")
def asset(name: str):
    path = storage.asset_path(name)
    if path is None or storage.extension(name) not in storage.IMAGE_EXTENSIONS:
        raise HTTPException(404)
    return FileResponse(path, headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
                                       "Cache-Control": "public, max-age=86400"})


@router.get("/robots.txt")
def robots(db: Session = Depends(get_db)):
    if site_settings.get(db, "portal_access") == "login":
        return PlainTextResponse("User-agent: *\nDisallow: /\n")
    return PlainTextResponse("User-agent: *\nDisallow: /admin\nDisallow: /auth\n")
