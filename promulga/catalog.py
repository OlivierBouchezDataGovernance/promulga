"""Links between documents and the data catalogue (OpenMetadata, PROM-48).

A document has one data *domain* (Document.domain) and may be linked to glossary terms and data products
(CatalogLink), named by their fully qualified name (FQN) in OpenMetadata.

- Promulga side: the document page, the API (``domain``, ``catalog_links``, filters ``domain`` and ``linked``)
  and the export show them, with a link to the entity in OpenMetadata when OPENMETADATA_URL is set.
- OpenMetadata side: with a bot token (OPENMETADATA_TOKEN or OPENMETADATA_TOKEN_FILE), Promulga checks that
  the entity exists (and reads its display name) and adds the published document to the *References* of the
  glossary terms (name "REF Title", link to the portal page). Domains and data products have no references in
  OpenMetadata: a catalogue reads their documents from the API (``GET /api/v1/documents?domain=`` or
  ``?linked=``). Only documents that are neither limited, confidential nor Audit are written there.

Without a token the links are kept as typed and nothing is sent.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx

from . import __version__
from .config import get_config
from .models import CATALOG_KINDS, RESTRICTED_CLASSIFICATIONS, CatalogLink, Document, utcnow

PAGES = {"domain": "domain", **{k: v["page"] for k, v in CATALOG_KINDS.items()}}
APIS = {"domain": "domains", **{k: v["api"] for k, v in CATALOG_KINDS.items()}}


class CatalogError(RuntimeError):
    pass


def entity_url(kind: str, fqn: str) -> str:
    """The page of the entity in OpenMetadata ('' when OPENMETADATA_URL is not set)."""
    base = get_config().openmetadata_url
    if not base or not fqn or kind not in PAGES:
        return ""
    return f"{base}/{PAGES[kind]}/{quote(fqn, safe='')}"


def _client() -> httpx.Client:
    cfg = get_config()
    return httpx.Client(base_url=f"{cfg.openmetadata_url}/api/v1", timeout=20, headers={
        "Authorization": f"Bearer {cfg.openmetadata_token}", "Accept": "application/json",
        "User-Agent": f"Promulga/{__version__}"})


def _call(client: httpx.Client, method: str, path: str, **kw) -> dict | None:
    try:
        r = client.request(method, path, **kw)
    except httpx.HTTPError as exc:
        raise CatalogError(f"OpenMetadata cannot be reached: {exc}") from exc
    if r.status_code == 404:
        return None
    if r.status_code >= 300:
        raise CatalogError(f"OpenMetadata {method} {path} failed ({r.status_code}): {r.text[:200]}")
    return r.json()


def _get(client: httpx.Client, kind: str, fqn: str, fields: str = "") -> dict | None:
    return _call(client, "GET", f"/{APIS[kind]}/name/{quote(fqn, safe='')}",
                 params={"fields": fields} if fields else None)


def lookup(kind: str, fqn: str, client: httpx.Client | None = None) -> str | None:
    """The display name of the entity; None when no token is configured (nothing checked).
    Raises CatalogError when the entity does not exist or OpenMetadata answers with an error."""
    if not get_config().openmetadata_enabled:
        return None
    own = client is None
    client = client or _client()
    try:
        entity = _get(client, kind, fqn)
    finally:
        if own:
            client.close()
    if entity is None:
        label = CATALOG_KINDS.get(kind, {}).get("label", kind).lower()
        raise CatalogError(f"OpenMetadata has no {label} named {fqn}.")
    return entity.get("displayName") or entity.get("name") or fqn


def writable(doc: Document) -> bool:
    """Whether the document may be named in OpenMetadata: published, and not limited, confidential nor Audit."""
    return bool(doc.current_version) and (doc.classification or "internal") not in RESTRICTED_CLASSIFICATIONS


def _reference(doc: Document) -> dict:
    return {"name": f"{doc.ref} {doc.title}"[:250], "endpoint": f"{get_config().base_url}/d/{doc.ref}"}


def sync_link(link: CatalogLink, remove: bool = False, client: httpx.Client | None = None) -> bool:
    """Adds (or removes) the document in the References of a glossary term. Returns True when OpenMetadata
    was changed; False when there is nothing to write (no token, not a glossary term, document not writable,
    reference already there)."""
    doc = link.document
    if not get_config().openmetadata_enabled or link.kind != "glossary_term" or (not remove and not writable(doc)):
        return False
    own = client is None
    client = client or _client()
    try:
        term = _get(client, "glossary_term", link.fqn, fields="references")
        if term is None:
            raise CatalogError(f"OpenMetadata has no glossary term named {link.fqn}.")
        mine = _reference(doc)["endpoint"]
        refs = term.get("references") or []
        index = next((i for i, r in enumerate(refs) if r.get("endpoint") == mine), None)
        if remove:
            if index is None:
                return False
            patch = [{"op": "remove", "path": f"/references/{index}"}]
        else:
            if index is not None:
                link.synced_at = utcnow()
                return False
            patch = ([{"op": "add", "path": "/references/-", "value": _reference(doc)}] if "references" in term
                     and term["references"] is not None else
                     [{"op": "add", "path": "/references", "value": [_reference(doc)]}])
        _call(client, "PATCH", f"/glossaryTerms/{term['id']}", json=patch,
              headers={"Content-Type": "application/json-patch+json"})
        if not remove:
            link.synced_at = utcnow()
        return True
    finally:
        if own:
            client.close()
