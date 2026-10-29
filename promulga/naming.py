"""Document metadata as tags: one dictionary used for references, file names,
Word variables, the cover page and the exported catalogue.

Tags (case-insensitive in patterns, written {TAG}):

    ID           global document number, 4 digits minimum (0001, ..., 10000)
    REF          document reference, built from REF_PATTERN (default {TYPE_CODE}-{ID})
    TITLE        title                      DESCRIPTION  summary
    TYPE         document type              TYPE_CODE    type prefix (POL, STD...)
    DOMAIN       name of the tag in the "domain" taxonomy
    CODE:<slug>  code of the document's tag in taxonomy <slug>, e.g. {CODE:domain} -> DG
    TAG:<slug>   name of that tag
    VERSION      major version (1 in 1.2)    SUBVERSION   minor version (2 in 1.2)
    FULL_VERSION 1.2                         STATUS       workflow stage
    AUTHOR       owner                       REVIEWERS / APPROVERS
    CREATED / UPDATED / PUBLISHED / NEXT_REVIEW   dates (yyyy-mm-dd)
    CLASSIFICATION  e.g. "Internal publication"   KEYWORDS  free keywords
    CHANGE_NOTE  what the version in preparation changes
    LINK         address of the official version on the portal
    COPYRIGHT    copyright line of the cover page (Settings)   YEAR  current year
    DATE         today
"""

from __future__ import annotations

import re
from datetime import date

from .config import get_config
from .models import CLASSIFICATIONS, PUBLISHED, RETIRED, Document

TAG_RE = re.compile(r"\{([A-Za-z_]+)(?::([A-Za-z0-9_-]+))?\}")
UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')

# Human labels of the variables, in the order of the cover page.
LABELS = [
    ("REF", "Reference"), ("TYPE", "Type"), ("DOMAIN", "Domain"), ("FULL_VERSION", "Version"),
    ("STATUS", "Status"), ("CLASSIFICATION", "Classification"), ("AUTHOR", "Owner"),
    ("REVIEWERS", "Reviewers"), ("APPROVERS", "Approvers"), ("CREATED", "Created"),
    ("UPDATED", "Last update"), ("PUBLISHED", "Published"), ("NEXT_REVIEW", "Next review"),
    ("KEYWORDS", "Keywords"), ("READERS", "Mandatory readers"), ("LINK", "Official version"),
]


def id_label(number: int) -> str:
    return f"{number:04d}"


def values(doc: Document, version: str | None = None, site: dict | None = None,
           published: date | None = None) -> dict[str, str]:
    """All tag values of a document. `version` defaults to the version being
    prepared (or in force once published)."""
    if version is None:
        version = doc.current_version if doc.status in (PUBLISHED, RETIRED) else doc.next_version
    major, _, minor = (version or "0.0").partition(".")
    prefix = ""
    if site:
        prefix = next((t["prefix"] for t in site["doc_types"] if t["name"] == doc.doc_type), "")
    domain = next((t for t in doc.terms if t.taxonomy and t.taxonomy.slug == "domain"), None)
    pub = published or doc.published_at
    v = {
        "ID": id_label(doc.number) if doc.number else "",
        "REF": doc.ref or "",
        "TITLE": doc.title or "",
        "DESCRIPTION": doc.summary or "",
        "TYPE": doc.doc_type or "",
        "TYPE_CODE": prefix,
        "DOMAIN": domain.name if domain else "",
        "VERSION": major,
        "SUBVERSION": minor or "0",
        "FULL_VERSION": version or "",
        "STATUS": doc.status or "",
        "AUTHOR": doc.owner.display if doc.owner else "",
        "REVIEWERS": ", ".join(a.user.display for a in doc.reviewers),
        "APPROVERS": ", ".join(a.user.display for a in doc.approvers),
        "CREATED": doc.created_at.date().isoformat() if doc.created_at else "",
        "UPDATED": (doc.updated_at or doc.created_at).date().isoformat() if doc.created_at else "",
        "PUBLISHED": pub.isoformat() if pub else "",
        "NEXT_REVIEW": doc.next_review.isoformat() if doc.next_review else "",
        "DATE": date.today().isoformat(),
        "YEAR": str(date.today().year),
        "CLASSIFICATION": CLASSIFICATIONS.get(doc.classification or "internal", CLASSIFICATIONS["internal"]),
        "CLASSIFICATION_CODE": doc.classification or "internal",
        "KEYWORDS": doc.keywords or "",
        "CHANGE_NOTE": doc.change_note or "",
        "READERS": doc.readers or ("Every account" if doc.ack_required else "None"),
        "LINK": f"{get_config().base_url.rstrip('/')}/d/{doc.ref}" if doc.ref else "",
    }
    copyright_pattern = (site or {}).get("cover_copyright", "")
    v["COPYRIGHT"] = re.sub(r"\{(YEAR|SITE)\}", lambda m: v["YEAR"] if m.group(1) == "YEAR"
                            else (site or {}).get("site_name", ""), copyright_pattern)
    return v


def render(pattern: str, doc: Document, vals: dict[str, str] | None = None) -> str:
    """Replaces {TAG} and {CODE:slug}/{TAG:slug} in a pattern."""
    vals = vals or {}

    def sub(m: re.Match) -> str:
        tag, arg = m.group(1).upper(), (m.group(2) or "").lower()
        if tag in ("CODE", "TAG") and arg:
            term = next((t for t in doc.terms if t.taxonomy and t.taxonomy.slug == arg), None)
            if term is None:
                return ""
            return (term.code or term.slug.upper()[:6]) if tag == "CODE" else term.name
        return vals.get(tag, m.group(0))

    return TAG_RE.sub(sub, pattern)


def build_ref(doc: Document, type_code: str) -> str:
    """Reference of a new document from REF_PATTERN. Must contain {ID} to stay unique."""
    pattern = get_config().ref_pattern
    if "{ID}" not in pattern.upper():
        pattern += "-{ID}"
    vals = {"ID": id_label(doc.number), "TYPE_CODE": type_code, "TYPE": doc.doc_type, "TITLE": doc.title}
    ref = render(pattern, doc, vals)
    ref = re.sub(r"\s+", "-", UNSAFE_FILENAME.sub("-", ref)).strip("-")
    return re.sub(r"-{2,}", "-", ref)[:60]


def file_name(doc: Document, extension: str, site: dict, version: str | None = None, draft: bool | None = None,
              published: date | None = None) -> str:
    """File name from FILENAME_PATTERN, e.g. 'POL-0001-v1.0-Data Quality Policy.pdf'."""
    vals = values(doc, version, site, published)
    name = render(get_config().filename_pattern, doc, vals)
    if draft is None:
        draft = doc.status not in (PUBLISHED, RETIRED)
    if draft:
        name += "-draft"
    name = UNSAFE_FILENAME.sub("-", name)
    name = re.sub(r"-{2,}", "-", re.sub(r"\s{2,}", " ", name)).strip(" .-")[:180] or doc.ref
    return f"{name}{extension}"
