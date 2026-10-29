"""Read-only REST API for other tools (data catalogues such as OpenMetadata, intranets, scripts).

Authentication: ``Authorization: Bearer <token>``, with a token created in
Admin > API tokens (only its hash is stored). Working files and comments are
never exposed; published PDFs are linked through the portal.

    GET /api/v1/documents                 all documents (metadata, stage, versions)
    GET /api/v1/documents/{ref}           one document, with its publications
    GET /api/v1/taxonomies                taxonomies and tags (with codes)
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session, selectinload

from .. import naming, site_settings
from ..config import get_config
from ..db import get_db
from ..models import ApiToken, Document, Taxonomy, utcnow

router = APIRouter(prefix="/api/v1")


def api_client(authorization: str = Header(""), db: Session = Depends(get_db)) -> ApiToken:
    token = authorization.removeprefix("Bearer ").strip()
    row = db.query(ApiToken).filter_by(token_hash=hashlib.sha256(token.encode()).hexdigest(), revoked=False).first() \
        if token else None
    if row is None:
        raise HTTPException(401, "Missing or invalid API token.")
    row.last_used_at = utcnow()
    db.commit()
    return row


def _doc(d: Document, site: dict, full: bool = False) -> dict:
    base = get_config().base_url
    out = {
        "ref": d.ref, "id": d.id_label, "title": d.title, "description": d.summary, "type": d.doc_type,
        "status": d.status, "version_in_force": d.current_version or None, "version_in_preparation":
            d.next_version if d.status not in ("Published", "Retired") else None,
        "published": d.published_at.isoformat() if d.published_at else None,
        "stage_due": d.stage_due.isoformat() if d.stage_due else None,
        "next_review": d.next_review.isoformat() if d.next_review else None,
        "owner": d.owner.email, "reviewers": [a.user.email for a in d.reviewers],
        "approvers": [a.user.email for a in d.approvers],
        "tags": [{"taxonomy": t.taxonomy.slug, "name": t.name, "code": t.code} for t in d.terms],
        "portal_url": f"{base}/d/{d.ref}" if d.current_version else None,
    }
    if full:
        out["publications"] = [{
            "version": p.version, "published": p.published_at.isoformat(), "approved_by": p.approved_by,
            "change_note": p.change_note, "sha256": p.sha256, "signed": p.signed,
            "file_name": naming.file_name(d, ".pdf", site, version=p.version, draft=False, published=p.published_at),
            "pdf_url": f"{base}/d/{d.ref}/v{p.version}.pdf" if d.status != "Retired" else None,
        } for p in d.publications]
    return out


@router.get("/documents")
def list_documents(status: str = "", type: str = "", tag: str = "", db: Session = Depends(get_db),
                   _client: ApiToken = Depends(api_client)):
    site = site_settings.get_all(db)
    q = db.query(Document).options(selectinload(Document.terms), selectinload(Document.assignments))
    if status:
        q = q.filter(Document.status == status)
    if type:
        q = q.filter(Document.doc_type == type)
    docs = q.order_by(Document.ref).all()
    if tag:
        docs = [d for d in docs if any(t.slug == tag.lower() or t.name.lower() == tag.lower() for t in d.terms)]
    return {"count": len(docs), "documents": [_doc(d, site) for d in docs]}


@router.get("/documents/{ref}")
def get_document(ref: str, db: Session = Depends(get_db), _client: ApiToken = Depends(api_client)):
    d = db.query(Document).filter_by(ref=ref).first()
    if d is None:
        raise HTTPException(404, "Unknown document.")
    return _doc(d, site_settings.get_all(db), full=True)


@router.get("/taxonomies")
def taxonomies(db: Session = Depends(get_db), _client: ApiToken = Depends(api_client)):
    return [{"slug": t.slug, "name": t.name, "description": t.description,
             "tags": [{"id": term.id, "name": term.name, "code": term.code, "parent_id": term.parent_id}
                      for term in t.terms]}
            for t in db.query(Taxonomy).order_by(Taxonomy.sort, Taxonomy.name).all()]
