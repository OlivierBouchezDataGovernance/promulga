"""Endpoints called by the OnlyOffice server (not by browsers): file download and save callback.

They carry no session: each URL contains a signed, expiring token, and the
callback body is a JWT signed with ONLYOFFICE_JWT_SECRET.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from .. import onlyoffice, storage, workflow
from ..db import get_db
from ..models import Document, Revision, User

router = APIRouter(prefix="/oo")
log = logging.getLogger(__name__)


@router.get("/file/{token}")
def file(token: str, db: Session = Depends(get_db)):
    rev_id = onlyoffice.read_file_token(token)
    rev = db.get(Revision, rev_id) if rev_id else None
    if rev is None:
        return Response(status_code=404)
    return Response(storage.read_bytes(rev.storage_path), media_type=rev.content_type)


@router.post("/callback/{token}")
async def callback(token: str, request: Request, authorization: str = Header(""), db: Session = Depends(get_db)):
    doc_id = onlyoffice.read_callback_token(token)
    doc = db.get(Document, doc_id) if doc_id else None
    if doc is None:
        return JSONResponse({"error": 1})
    try:
        payload = onlyoffice.verify_callback(await request.json(), authorization)
    except Exception as exc:
        log.warning("Rejected OnlyOffice callback for document %s: %s", doc_id, exc)
        return JSONResponse({"error": 1})
    status = payload.get("status")
    # 2 = everybody closed the document with changes; 6 = periodic "force save" while editing.
    if status in (2, 6) and payload.get("url"):
        users = payload.get("users") or []
        user = db.get(User, int(users[0])) if users and str(users[0]).isdigit() else None
        try:
            data = onlyoffice.download_saved(payload["url"])
            workflow.save_online(db, user, doc, data)
            db.commit()
        except workflow.WorkflowError as exc:
            db.rollback()
            log.warning("Online save of %s refused: %s", doc.ref, exc)
        except Exception as exc:
            db.rollback()
            log.error("Online save of %s failed: %s", doc.ref, exc)
            return JSONResponse({"error": 1})
    return JSONResponse({"error": 0})
