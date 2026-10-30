"""OnlyOffice Docs integration: one shared version, edited and commented in the browser.

How it works (all calls are signed with ONLYOFFICE_JWT_SECRET):

1. The document page opens the editor with a configuration that says who the
   user is and what they may do in the current stage:
     - Draft / Revision: the owner edits (and replies to comments);
     - Review: reviewers comment (no edits, no tracked changes), the owner
       may only comment; nobody rewrites the text;
     - Approval / Published: everybody reads, comments included.
2. OnlyOffice downloads the latest revision from Promulga (signed URL).
3. Everybody works on the SAME file at the same time; comments of every
   reviewer are in that one file.
4. When the last person closes the editor (or every few minutes, "force
   save"), OnlyOffice calls Promulga back, which stores the result as a new
   revision. The audit log records it like a check-in.

Check-out / check-in remains available for people who prefer Word on their PC.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx
import jwt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import get_config
from .models import APPROVER, DRAFT, REVIEW, REVIEWER, REVISION, Document, Revision, User
from .workflow import acting_assignment, is_manager

EDITABLE = {".docx", ".xlsx", ".pptx", ".odt"}
FILE_TYPES = {".docx": ("docx", "word"), ".odt": ("odt", "word"), ".xlsx": ("xlsx", "cell"),
              ".pptx": ("pptx", "slide"), ".pdf": ("pdf", "pdf")}
FILE_TOKEN_MAX_AGE = 6 * 3600


def available(revision: Revision | None) -> bool:
    cfg = get_config()
    return bool(cfg.onlyoffice_enabled and revision is not None
                and any(revision.filename.lower().endswith(e) for e in FILE_TYPES))


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_config().secret_key, salt="promulga-onlyoffice")


def file_token(revision: Revision) -> str:
    return _serializer().dumps({"rev": revision.id})


def read_file_token(token: str) -> int | None:
    try:
        return int(_serializer().loads(token, max_age=FILE_TOKEN_MAX_AGE)["rev"])
    except (BadSignature, SignatureExpired, KeyError, ValueError, TypeError):
        return None


def callback_token(doc: Document) -> str:
    return _serializer().dumps({"doc": doc.id})


def read_callback_token(token: str) -> int | None:
    try:
        return int(_serializer().loads(token, max_age=7 * 24 * 3600)["doc"])
    except (BadSignature, SignatureExpired, KeyError, ValueError, TypeError):
        return None


def document_key(revision: Revision) -> str:
    """Same key = same editing session. It changes with every new revision."""
    return f"{revision.document_id}-{revision.id}-{revision.sha256[:12]}"


def permissions(doc: Document, user: User) -> dict:
    owner = doc.owner_id == user.id
    editor = doc.status in (DRAFT, REVISION) and (owner or is_manager(user))
    reviewer = doc.status == REVIEW and acting_assignment(doc, user, REVIEWER) is not None
    commenter = editor or reviewer or (doc.status == REVIEW and owner)
    # Reviewers comment only: no editing, no tracked-change suggestions. The Word file is handed out
    # in Draft and Revision only, to the people who edit it.
    return {"edit": editor, "review": editor, "comment": commenter, "download": editor, "print": True,
            "copy": True, "modifyFilter": False, "fillForms": editor}


def editor_config(doc: Document, revision: Revision, user: User) -> dict:
    cfg = get_config()
    ext = "." + revision.filename.rsplit(".", 1)[-1].lower()
    file_type, document_type = FILE_TYPES[ext]
    perms = permissions(doc, user)
    if ext not in EDITABLE:
        perms.update(edit=False, review=False, comment=False)
    mode = "edit" if (perms["edit"] or perms["review"] or perms["comment"]) else "view"
    internal = cfg.internal_url or cfg.base_url
    config = {
        "document": {
            "fileType": file_type, "key": document_key(revision), "title": f"{doc.ref} - {doc.title}{ext}",
            "url": f"{internal}/oo/file/{file_token(revision)}", "permissions": perms,
        },
        "documentType": document_type,
        "editorConfig": {
            "mode": mode, "lang": "en",
            "user": {"id": str(user.id), "name": user.display},
            "customization": {"forcesave": True, "comments": True, "chat": False, "compactHeader": False,
                              "goback": {"url": f"{cfg.base_url}/admin/documents/{doc.id}", "text": "Back to Promulga"}},
        },
        "width": "100%", "height": "100%",
    }
    if mode == "edit":
        config["editorConfig"]["callbackUrl"] = f"{internal}/oo/callback/{callback_token(doc)}"
    config["token"] = jwt.encode(config, cfg.onlyoffice_jwt_secret, algorithm="HS256")
    return config


def verify_callback(body: dict, authorization: str) -> dict:
    """Returns the verified callback payload (OnlyOffice signs it with the shared secret)."""
    secret = get_config().onlyoffice_jwt_secret
    token = body.get("token") or authorization.removeprefix("Bearer ").strip()
    if not token:
        raise ValueError("unsigned callback")
    payload = jwt.decode(token, secret, algorithms=["HS256"])
    return payload.get("payload", payload)


def download_saved(url: str) -> bytes:
    """Downloads the file OnlyOffice saved, through the internal network address."""
    cfg = get_config()
    parts = urlsplit(url)
    path = parts.path
    public_prefix = urlsplit(cfg.onlyoffice_url).path.rstrip("/")
    if public_prefix and path.startswith(public_prefix + "/"):
        path = path[len(public_prefix):]
    target = (cfg.onlyoffice_internal_url or f"{parts.scheme}://{parts.netloc}") + path
    if parts.query:
        target += "?" + parts.query
    r = httpx.get(target, timeout=120)
    r.raise_for_status()
    return r.content


__all__ = ["APPROVER", "available", "editor_config", "permissions", "verify_callback", "download_saved",
           "read_file_token", "read_callback_token", "document_key"]
