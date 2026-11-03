"""LAB-76 export without monkeypatch, PROM-46 periodic review confirmed, PROM-44 PDF annotations as remarks,
PROM-48 links to OpenMetadata and LAB-145 domain of a document."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, timedelta

import httpx
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject

from promulga import catalog, export, reminders, site_settings, workflow
from promulga.models import CatalogLink, Comment, Event, OutboxMail, ReminderLog, Taxonomy, Term
from promulga.workflow import WorkflowError

from .conftest import csrf_of, login, make_pdf
from .test_deployment import _migrate, _token
from .test_v02 import _admin, _publish, _user


# --------------------------------------------------------------------------- LAB-76 export

def test_export_lists_the_others_without_files(db):
    admin = _admin(db)
    charter = _publish(db, admin, title="Framework", classification="external", website="public")
    _publish(db, admin, title="Business analysis", classification="confidential")
    _publish(db, admin, title="Intranet note", website="internal")
    _publish(db, admin, title="Audit trail", classification="audit", owner="admin@example.org")
    charter.keywords = "governance, charter"
    charter.domain = "Data governance"
    child = workflow.create_document(db, admin, title="Child", doc_type="Standard", summary="", term_ids=[],
                                     owner_email="owner@example.org", reviewer_emails=[], file=None,
                                     approver_emails=["app@example.org"], parent_id=charter.id)
    child.parent = charter
    db.flush()

    assert [e["title"] for e in json.loads(export.build(db)["catalogue.json"])["documents"]] == ["Framework"]
    assert export.listed_documents(db) == []

    site_settings.set_many(db, {"export_list_others": True})
    files = export.build(db)
    cat = {e["title"]: e for e in json.loads(files["catalogue.json"])["documents"]}
    assert set(cat) == {"Framework", "Business analysis", "Intranet note"}      # never the Audit document
    full, listed = cat["Framework"], cat["Business analysis"]
    assert full["listed_only"] is False and "Framework body" in full["text"]
    assert full["keywords"] == ["governance", "charter"] and full["domain"] == "Data governance"
    assert full["versions"][0]["file"] in files and full["versions"][0]["sha256"]
    assert listed["listed_only"] is True and listed["text"] == ""
    assert set(listed["versions"][0]) == {"version", "published", "approved_by", "change_note", "signed"}
    assert sum(name.endswith(".pdf") for name in files) == 1                   # only the exported PDF
    dcat = json.loads(files["catalogue.jsonld"])
    by_id = {r["dct:identifier"]: r for r in dcat["dcat:resource"]}
    assert by_id[listed["ref"]]["dcat:distribution"] == [] and "charter" in by_id[full["ref"]]["dcat:keyword"]
    rows = list(csv.DictReader(io.StringIO(files["catalogue.csv"].decode())))
    assert {r["title"]: r["file"] for r in rows}["Business analysis"] == ""
    assert "Listed only" in files["index.html"].decode()

    # With scope "all" the internal note gets its file; the parent of a listed document is named.
    site_settings.set_many(db, {"export_scope": "all"})
    cat = {e["title"]: e for e in json.loads(export.build(db)["catalogue.json"])["documents"]}
    assert cat["Intranet note"]["listed_only"] is False and cat["Business analysis"]["listed_only"] is True


def test_export_settings_page(client):
    login(client, "admin@example.org", "admin-password-123")
    page = client.get("/admin/export")
    client.post("/admin/export/scope", data={"csrf": csrf_of(page.text), "scope": "all", "list_others": "1"})
    from promulga import db as database
    with database.session_scope() as s:
        assert site_settings.get(s, "export_scope") == "all" and site_settings.get(s, "export_list_others") is True
    assert "checked" in re.search(r'name="list_others"[^>]*>', client.get("/admin/export").text).group(0)


# --------------------------------------------------------------------------- PROM-46 periodic review

def test_confirm_periodic_review(db):
    admin = _admin(db)
    doc = _publish(db, admin, review_months=6)
    owner, other = _user(db, "owner@example.org"), _user(db, "app@example.org")
    assert "confirm_review" in workflow.allowed_actions(doc, owner)
    assert "confirm_review" not in workflow.allowed_actions(doc, other)
    with pytest.raises(WorkflowError, match="Only the owner"):
        workflow.confirm_review(db, other, doc)

    doc.next_review = date.today() - timedelta(days=3)                       # overdue
    workflow.confirm_review(db, owner, doc, "Checked against NIS2")
    assert doc.status == "Published" and doc.current_version == "1.0" and len(doc.publications) == 1
    assert doc.next_review == workflow._add_months(date.today(), 6)
    assert doc.review_confirmed_by_id == owner.id and doc.review_confirmed_at is not None
    event = db.query(Event).filter_by(document_id=doc.id, action="Periodic review confirmed").one()
    assert "Version 1.0 still valid" in event.detail and "Checked against NIS2" in event.detail

    workflow.start_revision(db, admin, doc, "minor", "Next version")
    with pytest.raises(WorkflowError, match="needs Published"):
        workflow.confirm_review(db, owner, doc)


def test_confirmed_review_restarts_the_reminders(db):
    admin = _admin(db)
    doc = _publish(db, admin, review_months=12)
    owner = _user(db, "owner@example.org")
    doc.next_review = date.today() + timedelta(days=5)
    site_settings.set_many(db, {"review_reminder_days": 30, "overdue_repeat_days": 0, "manager_digest": False})
    reminders.run(db)
    assert db.query(ReminderLog).filter_by(document_id=doc.id, kind="review").count() == 1
    workflow.confirm_review(db, owner, doc)
    db.query(ReminderLog).update({ReminderLog.sent_on: date.today() - timedelta(days=1)})   # sent yesterday
    doc.next_review = date.today() + timedelta(days=5)                       # the next period is due again
    reminders.run(db)
    assert db.query(ReminderLog).filter_by(document_id=doc.id, kind="review").count() == 2


def test_confirm_button_on_the_page(client):
    from promulga import db as database
    with database.session_scope() as s:
        doc = _publish(s, _admin(s), review_months=12)
        doc_id, old = doc.id, doc.next_review
        doc.next_review = date.today() + timedelta(days=2)
    login(client, "admin@example.org", "admin-password-123")
    page = client.get(f"/admin/documents/{doc_id}")
    assert "/action/confirm_review" in page.text
    client.post(f"/admin/documents/{doc_id}/action/confirm_review", data={"csrf": csrf_of(page.text), "note": ""})
    page = client.get(f"/admin/documents/{doc_id}")
    assert "Last review confirmed" in page.text
    with database.session_scope() as s:
        from promulga.models import Document
        assert s.get(Document, doc_id).next_review == old


# --------------------------------------------------------------------------- PROM-44 PDF annotations

def _annotated(notes: list[dict]) -> bytes:
    """A PDF with annotations: {author, text, nm, reply_to (index), subtype}; plus a popup and a link."""
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(make_pdf("Policy text"))))
    refs = []
    for note in notes:
        d = DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject(note.get("subtype", "/Text")),
            NameObject("/Rect"): ArrayObject([FloatObject(v) for v in (100, 100, 120, 120)]),
            NameObject("/Contents"): TextStringObject(note["text"]), NameObject("/T"): TextStringObject(note["author"]),
        })
        if note.get("nm"):
            d[NameObject("/NM")] = TextStringObject(note["nm"])
        if note.get("reply_to") is not None:
            d[NameObject("/IRT")] = refs[note["reply_to"]]
        refs.append(writer._add_object(d))
    for subtype in ("/Popup", "/Link"):
        refs.append(writer._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject(subtype),
            NameObject("/Rect"): ArrayObject([FloatObject(v) for v in (0, 0, 10, 10)]),
            NameObject("/Contents"): TextStringObject("not a remark")})))
    writer.pages[0][NameObject("/Annots")] = ArrayObject(refs)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


NOTES = [
    {"author": "Raf Reviewer", "text": "Define 'critical data'.", "nm": "a1"},
    {"author": "olivi", "text": "Typo in section 2.", "subtype": "/Highlight"},
    {"author": "Raf Reviewer", "text": "", "nm": "empty"},
    {"author": "Raf Reviewer", "text": "See ISO 8000.", "nm": "a2", "reply_to": 0},
]


def test_pdf_annotations_become_remarks(db):
    from promulga import pdf
    found = pdf.annotations(_annotated(NOTES))
    assert [(a["author"], a["text"], a["page"], a["kind"]) for a in found] == [
        ("Raf Reviewer", "Define 'critical data'.", 1, "Text"), ("olivi", "Typo in section 2.", 1, "Highlight"),
        ("Raf Reviewer", "See ISO 8000.", 1, "Text")]
    assert found[2]["reply_to"] == "a1"

    admin = _admin(db)
    doc = workflow.create_document(db, admin, title="Data Policy", doc_type="Policy", summary="", term_ids=[],
                                   owner_email="owner@example.org", reviewer_emails=["reviewer@example.org"],
                                   approver_emails=["app@example.org"], file=None)
    owner, reviewer = _user(db, "owner@example.org"), _user(db, "reviewer@example.org")
    reviewer.name = "Raf Reviewer"
    workflow.check_in(db, owner, doc, "final.pdf", make_pdf("Policy text"))
    workflow.submit(db, owner, doc)
    data = _annotated(NOTES)
    with pytest.raises(WorkflowError, match="as a PDF"):
        workflow.import_pdf_annotations(db, reviewer, doc, "notes.docx", data)
    with pytest.raises(WorkflowError, match="no annotation"):
        workflow.import_pdf_annotations(db, reviewer, doc, "copy.pdf", make_pdf("clean"))
    with pytest.raises(WorkflowError, match="not a readable PDF"):
        workflow.import_pdf_annotations(db, reviewer, doc, "copy.pdf", b"%PDF-broken")

    assert workflow.import_pdf_annotations(db, reviewer, doc, "Review copy annotated.pdf", data) == 3
    imported = [c for c in doc.comments if c.source == "pdf"]
    top = [c for c in imported if c.parent_id is None]
    assert {(c.author_id, c.body, c.location) for c in top} == {
        (reviewer.id, "Define 'critical data'.", "Page 1"),
        (reviewer.id, "Typo in section 2.", "Page 1 - annotation by olivi")}   # unknown name: the uploader's
    reply = next(c for c in imported if c.parent_id)
    assert reply.body == "See ISO 8000." and reply.parent_id == next(c.id for c in top if c.external_id == "pdf:a1")
    assert len({(c.attachment_name, c.attachment_path) for c in imported}) == 1 and imported[0].attachment_path
    assert {c.id for c in workflow.remarks(doc)} >= {c.id for c in top}      # the owner decides on them
    with pytest.raises(WorkflowError, match="already imported"):
        workflow.import_pdf_annotations(db, reviewer, doc, "again.pdf", data)
    more = _annotated(NOTES + [{"author": "Raf Reviewer", "text": "One more.", "nm": "a3"}])
    assert workflow.import_pdf_annotations(db, reviewer, doc, "again.pdf", more) == 2   # the new note, the
    # untitled highlight again: without /NM an annotation is known by its file

    workflow.review_done(db, reviewer, doc)
    assert doc.status == "Revision"
    with pytest.raises(WorkflowError, match="only reply"):
        workflow.import_pdf_annotations(db, reviewer, doc, "late.pdf",
                                        _annotated([{"author": "Raf Reviewer", "text": "Late", "nm": "z"}]))


def test_pdf_annotations_upload_page(client):
    from promulga import db as database
    with database.session_scope() as s:
        admin = _admin(s)
        doc = workflow.create_document(s, admin, title="Data Policy", doc_type="Policy", summary="", term_ids=[],
                                       owner_email="owner@example.org", reviewer_emails=[],
                                       approver_emails=["app@example.org"], file=None)
        doc_id = doc.id
    login(client, "admin@example.org", "admin-password-123")
    page = client.get(f"/admin/documents/{doc_id}")
    assert "Import the annotations" in page.text
    client.post(f"/admin/documents/{doc_id}/annotations", data={"csrf": csrf_of(page.text)},
                files={"file": ("annotated.pdf", _annotated(NOTES[:1]), "application/pdf")})
    page = client.get(f"/admin/documents/{doc_id}")
    assert "from an annotated PDF" in page.text and "Define &#39;critical data&#39;." in page.text


# --------------------------------------------------------------------------- PROM-48 / LAB-145 catalogue

def test_domain_and_links_without_openmetadata(db, client):
    from promulga import db as database
    with database.session_scope() as s:
        admin = _admin(s)
        doc = _publish(s, admin, title="Customer Data Standard")
        workflow.update_metadata(s, admin, doc, title=doc.title, summary=doc.summary, term_ids=[],
                                 domain="  Customer   Data ")
        assert doc.domain == "Customer Data" and workflow.domains(s) == ["Customer Data"]
        link, warning = workflow.add_catalog_link(s, admin, doc, "glossary_term", "Business Glossary.Customer")
        assert (link.label, warning, link.synced_at) == ("", "", None)          # no token: kept as typed
        with pytest.raises(WorkflowError, match="already linked"):
            workflow.add_catalog_link(s, admin, doc, "glossary_term", "Business Glossary.Customer")
        with pytest.raises(WorkflowError, match="glossary term or a data product"):
            workflow.add_catalog_link(s, admin, doc, "table", "x")
        workflow.add_catalog_link(s, admin, doc, "data_product", "Customer 360")
        with pytest.raises(WorkflowError, match="Only the owner"):
            workflow.add_catalog_link(s, _user(s, "app@example.org"), doc, "data_product", "Other")
        ref = doc.ref
        other = _publish(s, admin, title="Other")

    login(client, "admin@example.org", "admin-password-123")
    headers = _token(client, "")
    one = client.get(f"/api/v1/documents/{ref}", headers=headers).json()
    assert one["domain"] == "Customer Data" and one["review_confirmed"] is None
    assert [(l["kind"], l["fqn"], l["url"]) for l in one["catalog_links"]] == [
        ("data_product", "Customer 360", None), ("glossary_term", "Business Glossary.Customer", None)]
    assert [d["ref"] for d in client.get("/api/v1/documents?domain=customer data", headers=headers).json()["documents"]] == [ref]
    linked = client.get("/api/v1/documents", params={"linked": "Business Glossary.Customer"}, headers=headers).json()
    assert [d["ref"] for d in linked["documents"]] == [ref] and other.ref not in str(linked)
    page = client.get("/admin/documents")
    doc_page = client.get(f"/admin/documents/{_id(ref)}")
    assert "Customer 360" in doc_page.text and "OpenMetadata is not connected" in doc_page.text
    assert page.status_code == 200


def _id(ref: str) -> int:
    from promulga import db as database
    from promulga.models import Document
    with database.session_scope() as s:
        return s.query(Document).filter_by(ref=ref).one().id


class FakeOpenMetadata:
    """Glossary term Business Glossary.Customer and data product Customer 360; records the PATCH calls."""

    def __init__(self):
        self.references: list[dict] | None = None
        self.patches: list[list[dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer om-token"
        path = request.url.path
        if request.method == "GET" and path == "/api/v1/glossaryTerms/name/Business Glossary.Customer":
            body = {"id": "t1", "name": "Customer", "displayName": "Customer (glossary)"}
            if "references" in request.url.params.get("fields", ""):
                body["references"] = self.references
            return httpx.Response(200, json=body)
        if request.method == "GET" and path == "/api/v1/dataProducts/name/Customer 360":
            return httpx.Response(200, json={"id": "p1", "name": "Customer 360"})
        if request.method == "PATCH" and path == "/api/v1/glossaryTerms/t1":
            assert request.headers["Content-Type"] == "application/json-patch+json"
            patch = json.loads(request.content)
            self.patches.append(patch)
            for op in patch:
                if op["op"] == "add" and op["path"] == "/references":
                    self.references = op["value"]
                elif op["op"] == "add":
                    self.references.append(op["value"])
                else:
                    self.references.pop(int(op["path"].rsplit("/", 1)[1]))
            return httpx.Response(200, json={"id": "t1"})
        return httpx.Response(404, json={"message": "not found"})


@pytest.fixture()
def openmetadata(app_env, monkeypatch):
    from promulga import config
    monkeypatch.setenv("OPENMETADATA_URL", "https://om.example.org/")
    monkeypatch.setenv("OPENMETADATA_TOKEN", "om-token")
    config.get_config.cache_clear()
    fake = FakeOpenMetadata()
    monkeypatch.setattr(catalog, "_client", lambda: httpx.Client(
        base_url="https://om.example.org/api/v1", transport=httpx.MockTransport(fake),
        headers={"Authorization": "Bearer om-token"}))
    yield fake
    config.get_config.cache_clear()


def test_links_written_in_openmetadata(db, openmetadata):
    admin = _admin(db)
    assert catalog.entity_url("glossary_term", "Business Glossary.Customer") == \
        "https://om.example.org/glossary/Business%20Glossary.Customer"
    assert catalog.entity_url("domain", "Customer Data") == "https://om.example.org/domain/Customer%20Data"

    draft = workflow.create_document(db, admin, title="Draft", doc_type="Policy", summary="", term_ids=[],
                                     owner_email="owner@example.org", reviewer_emails=[],
                                     approver_emails=["app@example.org"], file=None)
    link, warning = workflow.add_catalog_link(db, admin, draft, "glossary_term", "Business Glossary.Customer")
    assert link.label == "Customer (glossary)" and openmetadata.patches == []   # not published: not written
    with pytest.raises(WorkflowError, match="no data product named Unknown"):
        workflow.add_catalog_link(db, admin, draft, "data_product", "Unknown")

    o = _user(db, "owner@example.org")
    workflow.check_in(db, o, draft, "final.pdf", make_pdf("Draft body"))
    workflow.submit(db, o, draft)
    workflow.approve(db, _user(db, "app@example.org"), draft)                 # publication writes the link
    assert openmetadata.references == [{"name": f"{draft.ref} Draft", "endpoint": f"http://testserver/d/{draft.ref}"}]
    assert link.synced_at is not None
    assert workflow.sync_catalog(db, draft) == [] and len(openmetadata.patches) == 1   # already there

    secret = _publish(db, admin, title="Secret", classification="confidential")
    workflow.add_catalog_link(db, admin, secret, "glossary_term", "Business Glossary.Customer")
    assert len(openmetadata.references) == 1                                 # a confidential title stays home
    product, _ = workflow.add_catalog_link(db, admin, draft, "data_product", "Customer 360")
    assert product.label == "Customer 360" and len(openmetadata.patches) == 1  # data products have no references

    assert workflow.remove_catalog_link(db, admin, draft, link) == ""
    assert openmetadata.references == [] and db.query(CatalogLink).filter_by(document_id=draft.id).count() == 1


def test_openmetadata_unreachable_keeps_the_link(db, app_env, monkeypatch):
    from promulga import config
    monkeypatch.setenv("OPENMETADATA_URL", "https://om.example.org")
    monkeypatch.setenv("OPENMETADATA_TOKEN", "om-token")
    config.get_config.cache_clear()
    doc = _publish(db, _admin(db))

    def down(request):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(catalog, "_client", lambda: httpx.Client(transport=httpx.MockTransport(down)))
    with pytest.raises(WorkflowError, match="cannot be reached"):
        workflow.add_catalog_link(db, _admin(db), doc, "glossary_term", "G.T")
    config.get_config.cache_clear()


def test_migration_fills_the_domain_from_the_taxonomy(db):
    from promulga import db as database
    admin = _admin(db)
    tax = Taxonomy(name="Domain", slug="domain")
    top = Term(taxonomy=tax, name="Data governance", slug="data-governance")
    leaf = Term(taxonomy=tax, name="Data quality", slug="data-quality", parent=top)
    other = Taxonomy(name="Regulation", slug="regulation")
    gdpr = Term(taxonomy=other, name="GDPR", slug="gdpr")
    db.add_all([tax, top, leaf, other, gdpr])
    db.flush()
    doc = workflow.create_document(db, admin, title="DQ", doc_type="Policy", summary="", owner_email="o@example.org",
                                   reviewer_emails=[], approver_emails=["p@example.org"],
                                   term_ids=[gdpr.id, leaf.id], file=None)
    plain = workflow.create_document(db, admin, title="Plain", doc_type="Policy", summary="",
                                     owner_email="o@example.org", reviewer_emails=[],
                                     approver_emails=["p@example.org"], term_ids=[gdpr.id], file=None)
    ids = (doc.id, plain.id)
    db.commit()
    db.close()
    engine = database.get_engine()
    _migrate(engine, "0008_channels", "downgrade")
    _migrate(engine, "head", "upgrade")
    with engine.connect() as conn:
        rows = dict(conn.exec_driver_sql("SELECT id, domain FROM documents").fetchall())
    assert (rows[ids[0]], rows[ids[1]]) == ("Data governance", "")
