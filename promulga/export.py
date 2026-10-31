"""Public export: a static package of the documents tagged EXPORT_TAG (default "public").

Scope (Settings > export): "public" (default) = the documents tagged EXPORT_TAG whose classification is an
external or commercial publication; "all" = every published document, whatever its tags and classification,
for a demonstration or an intranet site. Drafts never leave the server in either case.

The package is a folder that any web server can serve as it is:

    index.html          searchable list, in the site's colours
    <file name>.pdf     every published version, named with FILENAME_PATTERN
    catalogue.json      metadata of every document and file (with SHA-256)
    catalogue.csv       the same, for spreadsheets
    catalogue.jsonld    DCAT-AP 3 catalogue (European standard for catalogues)
    SHA256SUMS          checksums, to verify that nothing was altered

It can be downloaded as a .zip, or pushed in one commit to a folder of a GitHub
repository (GITHUB_REPO, GITHUB_BRANCH, GITHUB_FOLDER, GITHUB_TOKEN), for
example the repository of a website published by GitHub Pages, Netlify or
Cloudflare Pages. Only published versions leave the server, never drafts.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone

import httpx
from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup
from sqlalchemy.orm import Session, selectinload

from . import __version__, i18n, icons, naming, site_settings, storage
from .config import get_config
from .models import PUBLIC_CLASSIFICATIONS, RETIRED, Document

_env = Environment(loader=PackageLoader("promulga", "templates/export"), autoescape=select_autoescape(["html"]))
i18n.install(_env)


class ExportError(RuntimeError):
    pass


TEXT_MAX = 60_000   # characters of each document's text in catalogue.json, for full-text search on the site


def exported_documents(db: Session) -> list[Document]:
    tag = get_config().export_tag
    docs = (db.query(Document).options(selectinload(Document.terms), selectinload(Document.publications))
            .filter(Document.current_version != "", Document.status != RETIRED).order_by(Document.ref).all())
    docs = [d for d in docs if not d.is_strict]   # Audit documents never leave the server
    if site_settings.get(db, "export_scope") == "all":
        return docs
    # The tag selects the documents; the classification is a second lock: only external and
    # commercial publications may leave the server, whatever their tags.
    return [d for d in docs if d.classification in PUBLIC_CLASSIFICATIONS
            and any(t.slug.lower() == tag or t.name.lower() == tag for t in d.terms)]


def build(db: Session) -> dict[str, bytes]:
    """All files of the package, {relative path: content}."""
    cfg = get_config()
    site = site_settings.get_all(db)
    base = cfg.export_site_url
    files: dict[str, bytes] = {}
    entries = []
    exported = exported_documents(db)
    for doc in exported:
        versions = []
        for pub in doc.publications:   # newest first
            name = naming.file_name(doc, ".pdf", site, version=pub.version, draft=False,
                                    published=pub.published_at)
            files[name] = storage.read_bytes(pub.pdf_path)
            versions.append({"version": pub.version, "published": pub.published_at.isoformat(), "file": name,
                             "url": f"{base}/{name}" if base else name, "sha256": pub.sha256,
                             "approved_by": pub.approved_by, "change_note": pub.change_note,
                             "signed": pub.signed, "bytes": len(files[name])})
        entries.append({
            "ref": doc.ref, "id": doc.id_label, "title": doc.latest_publication.title, "description": doc.summary,
            "type": doc.doc_type, "type_icon": site_settings.doc_type_icon(site, doc.doc_type),
            "version": doc.current_version, "published": doc.published_at.isoformat(),
            "next_review": doc.next_review.isoformat() if doc.next_review else "",
            "owner": doc.owner.display if site.get("portal_show_owner") else "",
            "tags": [{"taxonomy": t.taxonomy.name, "name": t.name, "code": t.code} for t in doc.terms],
            "parent": doc.parent.ref if doc.parent and doc.parent in exported else "",
            "classification": doc.classification_label, "classification_code": doc.classification,
            "keywords": [k.strip() for k in (doc.keywords or "").split(",") if k.strip()],
            "references": [{"title": r.title, "issuer": r.issuer, "edition": r.edition, "url": r.url, "label": r.label}
                           for r in doc.references],
            "translations": [{"language": t.language, "version": t.version, "notice": t.notice}
                             for t in doc.translations if t.is_current],
            "text": (doc.latest_publication.text or "")[:TEXT_MAX],
            "versions": versions,
        })
    generated = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    catalogue = {"title": site["site_name"], "description": site["tagline"], "generated": generated,
                 "generator": f"Promulga {__version__}", "documents": entries}
    files["catalogue.json"] = json.dumps(catalogue, indent=2, ensure_ascii=False).encode()
    files["catalogue.csv"] = _csv(entries)
    files["catalogue.jsonld"] = json.dumps(_dcat(catalogue, base), indent=2, ensure_ascii=False).encode()
    logo_path = storage.asset_path(site["logo_asset"]) if site.get("logo_asset") else None
    if logo_path is not None:
        mime = "image/svg+xml" if logo_path.suffix == ".svg" else f"image/{logo_path.suffix.lstrip('.')}"
        logo_html = Markup('<img src="data:{};base64,{}" alt="">').format(
            mime, base64.b64encode(logo_path.read_bytes()).decode())
    else:
        logo_html = Markup(_mark_svg())
    files["index.html"] = _env.get_template("index.html").render(
        site=site, documents=entries, generated=generated[:10], version=__version__,
        icon=icons.svg, logo_html=logo_html).encode()
    files["SHA256SUMS"] = "".join(f"{hashlib.sha256(data).hexdigest()}  {path}\n"
                                  for path, data in sorted(files.items())).encode()
    return files


def zip_bytes(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path, data in sorted(files.items()):
            z.writestr(path, data)
    return out.getvalue()


def _mark_svg() -> str:
    from pathlib import Path
    return (Path(__file__).parent / "static" / "promulga-mark.svg").read_text(encoding="utf-8")


def _csv(entries: list[dict]) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["ref", "title", "type", "version", "published", "next_review", "tags", "file", "sha256"])
    for e in entries:
        current = e["versions"][0]
        w.writerow([e["ref"], e["title"], e["type"], e["version"], e["published"], e["next_review"],
                    "; ".join(f"{t['taxonomy']}: {t['name']}" for t in e["tags"]), current["file"], current["sha256"]])
    return out.getvalue().encode()


def _dcat(catalogue: dict, base: str) -> dict:
    """DCAT-AP 3 JSON-LD: the catalogue lists each document as a dcat:Resource with its PDF distributions."""
    def uri(path: str) -> str:
        return f"{base}/{path}" if base else path

    resources = []
    for e in catalogue["documents"]:
        resources.append({
            "@id": uri(f"#{e['ref']}"), "@type": "dcat:Resource",
            "dct:identifier": e["ref"], "dct:title": e["title"], "dct:description": e["description"],
            "dct:type": e["type"], "owl:versionInfo": e["version"], "dct:issued": e["published"],
            "dcat:keyword": [t["name"] for t in e["tags"]],
            "dcat:distribution": [{
                "@type": "dcat:Distribution", "dct:title": f"{e['ref']} version {v['version']}",
                "dcat:downloadURL": {"@id": uri(v["file"])}, "dct:format": "PDF", "dcat:mediaType": "application/pdf",
                "dct:issued": v["published"], "dcat:byteSize": v["bytes"],
                "spdx:checksum": {"@type": "spdx:Checksum", "spdx:algorithm": "spdx:checksumAlgorithm_sha256",
                                  "spdx:checksumValue": v["sha256"]},
            } for v in e["versions"]],
        })
    return {
        "@context": {"dcat": "http://www.w3.org/ns/dcat#", "dct": "http://purl.org/dc/terms/",
                     "owl": "http://www.w3.org/2002/07/owl#", "spdx": "http://spdx.org/rdf/terms#",
                     "foaf": "http://xmlns.com/foaf/0.1/"},
        "@id": uri("catalogue.jsonld"), "@type": "dcat:Catalog",
        "dct:title": catalogue["title"], "dct:description": catalogue["description"],
        "dct:modified": catalogue["generated"], "foaf:homepage": {"@id": uri("index.html")},
        "dcat:resource": resources,
    }


# ---------------------------------------------------------------------------
# GitHub: one commit replacing the content of GITHUB_FOLDER
# ---------------------------------------------------------------------------

def _git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def push_github(files: dict[str, bytes], message: str, client: httpx.Client | None = None) -> str:
    """Pushes the package; returns the commit SHA ('' when nothing changed)."""
    cfg = get_config()
    if not cfg.github_enabled:
        raise ExportError("GitHub is not configured (GITHUB_REPO and GITHUB_TOKEN or GITHUB_TOKEN_FILE).")
    api = f"https://api.github.com/repos/{cfg.github_repo}"
    prefix = f"{cfg.github_folder}/" if cfg.github_folder else ""
    own = client is None
    client = client or httpx.Client(timeout=60, headers={
        "Authorization": f"Bearer {cfg.github_token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": f"Promulga/{__version__}"})
    try:
        def call(method: str, url: str, **kw):
            r = client.request(method, url, **kw)
            if r.status_code >= 300:
                raise ExportError(f"GitHub {method} {url.replace(api, '')} failed ({r.status_code}): {r.text[:300]}")
            return r.json()

        head = call("GET", f"{api}/git/ref/heads/{cfg.github_branch}")["object"]["sha"]
        base_tree = call("GET", f"{api}/git/commits/{head}")["tree"]["sha"]
        existing = {e["path"]: e["sha"] for e in call("GET", f"{api}/git/trees/{base_tree}?recursive=1")["tree"]
                    if e["type"] == "blob"}
        tree = []
        for path, data in sorted(files.items()):
            full = prefix + path
            if existing.get(full) == _git_blob_sha(data):
                continue                                  # unchanged: no upload
            blob = call("POST", f"{api}/git/blobs", json={"content": base64.b64encode(data).decode(),
                                                          "encoding": "base64"})["sha"]
            tree.append({"path": full, "mode": "100644", "type": "blob", "sha": blob})
        for path in existing:
            if path.startswith(prefix) and path[len(prefix):] not in files:
                tree.append({"path": path, "mode": "100644", "type": "blob", "sha": None})   # removed document
        if not tree:
            return ""
        new_tree = call("POST", f"{api}/git/trees", json={"base_tree": base_tree, "tree": tree})["sha"]
        commit = call("POST", f"{api}/git/commits", json={"message": message, "tree": new_tree,
                                                          "parents": [head]})["sha"]
        call("PATCH", f"{api}/git/refs/heads/{cfg.github_branch}", json={"sha": commit})
        return commit
    finally:
        if own:
            client.close()
