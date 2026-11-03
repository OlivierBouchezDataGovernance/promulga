"""Read-only REST API for other tools (data catalogues such as OpenMetadata, intranets, scripts).

Authentication: ``Authorization: Bearer <token>``, with a token created in
Admin > API tokens (only its hash is stored). Working files and comments are
never exposed; published PDFs are linked through the portal.

    GET /api/v1/documents                 all documents (metadata, stage, versions);
                                          filters: status, type, tag, chatbot, website (no, internal, public)
    GET /api/v1/documents/{ref}           one document, with its publications
    GET /api/v1/taxonomies                taxonomies and tags (with codes)
    GET /api/v1/corpus                    knowledge feed of a chatbot (PROM-65), see corpus()

Two kinds of tokens (Admin > API tokens): a catalogue token reads the first three; a chatbot token, created
for one audience (internal or public), reads the corpus only, and a public token never sees an internal
document. Documents classified "Audit - Confidential" are never listed nor returned.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session, selectinload

from .. import naming, site_settings
from ..config import get_config
from ..db import get_db
from ..models import DISTRIBUTION_ORDER, ApiToken, ChannelLog, Document, Taxonomy, max_distribution, utcnow

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


def catalogue_client(client: ApiToken = Depends(api_client)) -> ApiToken:
    if client.audience:
        raise HTTPException(403, "A chatbot token reads the corpus only (/api/v1/corpus).")
    return client


def feed_client(client: ApiToken = Depends(api_client)) -> ApiToken:
    if client.audience not in ("internal", "public"):
        raise HTTPException(403, "The corpus needs a chatbot token, created for an audience (internal or public).")
    return client


def _rank(level: str) -> int:
    return DISTRIBUTION_ORDER.index(level) if level in DISTRIBUTION_ORDER else 0


def _since(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(400, "since must be a date or a date and time (ISO 8601), e.g. 2026-11-01.") from None


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
        "classification": d.classification, "chatbot": d.chatbot, "website": d.website, "keywords": [k.strip() for k in d.keywords.split(",") if k.strip()],
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
def list_documents(status: str = "", type: str = "", tag: str = "", chatbot: str = "", website: str = "",
                   db: Session = Depends(get_db), _client: ApiToken = Depends(catalogue_client)):
    site = site_settings.get_all(db)
    q = db.query(Document).options(selectinload(Document.terms), selectinload(Document.assignments))
    if status:
        q = q.filter(Document.status == status)
    if type:
        q = q.filter(Document.doc_type == type)
    if chatbot:
        q = q.filter(Document.chatbot == chatbot)
    if website:
        q = q.filter(Document.website == website)
    docs = [d for d in q.order_by(Document.ref).all() if not d.is_strict]   # Audit documents are not exposed
    if tag:
        docs = [d for d in docs if any(t.slug == tag.lower() or t.name.lower() == tag.lower() for t in d.terms)]
    return {"count": len(docs), "documents": [_doc(d, site) for d in docs]}


@router.get("/documents/{ref}")
def get_document(ref: str, db: Session = Depends(get_db), _client: ApiToken = Depends(catalogue_client)):
    d = db.query(Document).filter_by(ref=ref).first()
    if d is None:
        raise HTTPException(404, "Unknown document.")
    return _doc(d, site_settings.get_all(db), full=True)


@router.get("/corpus")
def corpus(audience: str = "", since: str = "", db: Session = Depends(get_db),
           client: ApiToken = Depends(feed_client)):
    """The knowledge feed of a chatbot: the clean text (Publication.text) of the version in force of every
    document a chatbot of `audience` may use (default: the audience of the token). "public" = Chatbot use
    Public; "internal" = Internal or Public. The classification caps it (limited, confidential and audit
    documents never), drafts and retired documents are never in it.

    `since` (ISO date or date and time): only the documents published or opened to the chatbot since then,
    plus `withdrawals`: the documents the chatbot may no longer use (retired, Chatbot use lowered, more
    restrictive classification) since then, so the client deletes them from its index."""
    audience = audience or client.audience
    if audience not in ("internal", "public"):
        raise HTTPException(400, "audience must be internal or public.")
    if client.audience == "public" and audience != "public":
        raise HTTPException(403, "This token is for the public audience: it never sees internal documents.")
    after = _since(since)
    wanted = DISTRIBUTION_ORDER[_rank(audience):]
    base = get_config().base_url
    docs = (db.query(Document).options(selectinload(Document.terms), selectinload(Document.publications))
            .filter(Document.chatbot_feed.in_(wanted)).order_by(Document.ref).all())
    docs = [d for d in docs if _rank(d.feed_level) >= _rank(audience)]   # second lock, computed now
    logs = (db.query(ChannelLog).filter(ChannelLog.channel == "chatbot").order_by(ChannelLog.at).all()
            if after else [])
    if after:
        changed, previous = set(), {}
        for log in logs:   # the chatbot of this audience gained the document, or a new version of it
            prev_level, prev_version = previous.get(log.document_id, ("no", ""))
            if log.at >= after and _rank(log.level) >= _rank(audience) and (
                    _rank(prev_level) < _rank(audience) or log.version != prev_version):
                changed.add(log.document_id)
            previous[log.document_id] = (log.level, log.version)
        docs = [d for d in docs if d.id in changed]
    out = []
    for d in docs:
        pub = d.latest_publication
        out.append({
            "ref": d.ref, "title": pub.title if pub else d.title, "description": d.summary, "type": d.doc_type,
            "version": d.current_version, "published": d.published_at.isoformat() if d.published_at else None,
            "classification": d.classification, "chatbot": d.feed_level,
            "tags": [{"taxonomy": t.taxonomy.slug, "name": t.name, "code": t.code} for t in d.terms],
            "keywords": [k.strip() for k in d.keywords.split(",") if k.strip()],
            "portal_url": f"{base}/d/{d.ref}",
            "pdf_url": f"{base}/d/{d.ref}/v{d.current_version}.pdf",
            "sha256": pub.sha256 if pub else None, "text": (pub.text or "") if pub else "",
        })
    result = {"audience": audience, "generated": utcnow().replace(microsecond=0).isoformat() + "Z",
              "count": len(out), "documents": out}
    if after:
        # Only documents this audience could once use are named: a public client never learns of the others.
        current = {d.id: d for d in db.query(Document).filter(
            Document.id.in_({log.document_id for log in logs if log.document_id})).all()}
        once, last = set(), {}
        for log in logs:
            key = log.document_id or log.ref
            if _rank(log.level) >= _rank(audience):
                once.add(key)
            last[key] = log
        withdrawals = []
        for key, log in last.items():
            doc = current.get(log.document_id)
            if key in once and log.at >= after and (doc is None or _rank(doc.feed_level) < _rank(audience)):
                reason = ("deleted" if doc is None else "retired" if doc.status == "Retired"
                          else "audit" if doc.is_strict else "classification"
                          if _rank(max_distribution(doc.classification)) < _rank(audience) else "chatbot use")
                withdrawals.append({"ref": doc.ref if doc else log.ref, "withdrawn": log.at.isoformat() + "Z",
                                    "reason": reason})
        result["withdrawals"] = sorted(withdrawals, key=lambda w: w["ref"])
    return result


@router.get("/taxonomies")
def taxonomies(db: Session = Depends(get_db), _client: ApiToken = Depends(catalogue_client)):
    return [{"slug": t.slug, "name": t.name, "description": t.description,
             "tags": [{"id": term.id, "name": term.name, "code": term.code, "parent_id": term.parent_id}
                      for term in t.terms]}
            for t in db.query(Taxonomy).order_by(Taxonomy.sort, Taxonomy.name).all()]
