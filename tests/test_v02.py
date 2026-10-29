"""Version 0.2 features: identifiers, names, Word metadata, governance, export, API, editor, security."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date, timedelta

import httpx
import pytest
from pypdf import PdfReader

from promulga import docx_meta, export, insights, naming, onlyoffice, site_settings, storage, workflow
from promulga.models import Acknowledgment, Document, StageLog, Term, Taxonomy, User
from promulga.workflow import WorkflowError

from .conftest import csrf_of, login, make_pdf


def _admin(db):
    return db.query(User).filter_by(email="admin@example.org").one()


def _user(db, email):
    return db.query(User).filter_by(email=email).one()


def _publish(db, admin, title="Data Quality Policy", owner="owner@example.org", reviewers=(), terms=(),
             ack=False, review_months=None):
    doc = workflow.create_document(db, admin, title=title, doc_type="Policy", summary="About data quality.",
                                   owner_email=owner, reviewer_emails=list(reviewers),
                                   approver_emails=["app@example.org"], term_ids=list(terms), file=None,
                                   ack_required=ack, review_months=review_months)
    o = _user(db, owner)
    workflow.check_in(db, o, doc, "final.pdf", make_pdf(f"{title} body"))
    workflow.submit(db, o, doc)
    for r in reviewers:
        workflow.review_done(db, _user(db, r), doc)
    if doc.status == "Revision":
        workflow.submit(db, o, doc)
    workflow.approve(db, _user(db, "app@example.org"), doc)
    return doc


def test_global_ids_and_reference_pattern(db):
    admin = _admin(db)
    a = workflow.create_document(db, admin, title="A", doc_type="Policy", summary="", owner_email="o@example.org",
                                 reviewer_emails=[], approver_emails=["p@example.org"], term_ids=[], file=None)
    b = workflow.create_document(db, admin, title="B", doc_type="Standard", summary="", owner_email="o@example.org",
                                 reviewer_emails=[], approver_emails=["p@example.org"], term_ids=[], file=None)
    assert (a.ref, b.ref) == ("POL-0001", "STD-0002")          # one global sequence, unique in the system
    assert naming.id_label(9999) == "9999" and naming.id_label(10000) == "10000"


def test_file_name_pattern_with_tag_codes(db, monkeypatch):
    admin = _admin(db)
    tax = Taxonomy(name="Domain", slug="domain")
    term = Term(taxonomy=tax, name="Data governance", slug="data-governance", code="DG")
    db.add_all([tax, term])
    db.flush()
    doc = _publish(db, admin, terms=[term.id])
    from promulga.config import get_config
    object.__setattr__(get_config(), "filename_pattern", "{CODE:domain}-{TYPE_CODE}-{VERSION}-{SUBVERSION}-{TITLE}")
    site = site_settings.get_all(db)
    assert naming.file_name(doc, ".pdf", site) == "DG-POL-1-0-Data Quality Policy.pdf"
    doc.title = 'Bad/name: "x"?'
    assert "/" not in naming.file_name(doc, ".pdf", site) and '"' not in naming.file_name(doc, ".pdf", site)


def test_publication_of_pdf_gets_cover_page_and_history(db):
    admin = _admin(db)
    doc = _publish(db, admin)
    pdf = storage.read_bytes(doc.latest_publication.pdf_path)
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) == 2                                # generated cover + the document
    cover = reader.pages[0].extract_text()
    assert "Data Quality Policy" in cover and "Version history" in cover and "Initial version" in cover


def test_word_cover_and_variables_on_check_in(db):
    import docx
    admin = _admin(db)
    doc = workflow.create_document(db, admin, title="Retention", doc_type="Policy", summary="Keep records.",
                                   owner_email="owner@example.org", reviewer_emails=[],
                                   approver_emails=["app@example.org"], term_ids=[], file=None)
    d = docx.Document()
    d.add_paragraph("Document {{REF}} version {{VERSION}}.{{SUBVERSION}} - {{TITLE}}")
    buf = io.BytesIO()
    d.save(buf)
    rev = workflow.check_in(db, _user(db, "owner@example.org"), doc, "mine.docx", buf.getvalue())
    data = storage.read_bytes(rev.storage_path)
    assert docx_meta.has_cover(data)
    assert f"Document {doc.ref} version 1.0 - Retention" in docx_meta.docx_paragraphs(data)
    assert docx_meta.capability_warning("x.pdf") and not docx_meta.capability_warning("x.docx")


def test_segregation_of_duties_warning_and_delegation(db):
    admin = _admin(db)
    doc = workflow.create_document(db, admin, title="SoD", doc_type="Policy", summary="", owner_email="o@example.org",
                                   reviewer_emails=[], approver_emails=["o@example.org", "boss@example.org"],
                                   term_ids=[], file=None)
    assert workflow.segregation_warnings(doc)
    o, boss = _user(db, "o@example.org"), _user(db, "boss@example.org")
    workflow.check_in(db, o, doc, "final.pdf", make_pdf("SoD"))
    deputy = User(email="deputy@example.org", role="contributor")
    db.add(deputy)
    db.flush()
    workflow.submit(db, o, doc)
    workflow.approve(db, o, doc)
    with pytest.raises(WorkflowError):
        workflow.approve(db, deputy, doc)                     # not a deputy yet
    boss.deputy_id, boss.absent_until = deputy.id, date.today() + timedelta(days=5)
    db.flush()
    db.refresh(boss)
    assert "approve" in workflow.allowed_actions(doc, deputy)
    workflow.approve(db, deputy, doc)
    assert doc.status == "Published"
    assert any("on behalf of boss@example.org" in e.action for e in db.query(workflow.Event).all())


def test_periodic_review_acknowledgment_and_indicators(db):
    admin = _admin(db)
    doc = _publish(db, admin, ack=True, review_months=6)
    assert doc.next_review == workflow._add_months(date.today(), 6)
    reader = User(email="reader@example.org", role="viewer")
    db.add(reader)
    db.flush()
    workflow.acknowledge(db, reader, doc)
    workflow.acknowledge(db, reader, doc)                     # idempotent
    status = workflow.acknowledgment_status(db, doc)
    assert db.query(Acknowledgment).count() == 1 and status["done"][0][0].email == "reader@example.org"
    assert db.query(StageLog).filter_by(document_id=doc.id).count() >= 2
    k = insights.kpis(db)
    assert k["published_period"] == 1 and k["per_stage"]["Approval"]["completed"] == 1


def test_export_package_and_github_push(db):
    admin = _admin(db)
    tax = Taxonomy(name="Visibility", slug="visibility")
    public = Term(taxonomy=tax, name="Public", slug="public")
    db.add_all([tax, public])
    db.flush()
    _publish(db, admin, title="Open Policy", terms=[public.id])
    _publish(db, admin, title="Internal Policy")
    files = export.build(db)
    assert {"index.html", "catalogue.json", "catalogue.csv", "catalogue.jsonld", "SHA256SUMS"} <= set(files)
    catalogue = json.loads(files["catalogue.json"])
    assert [d["title"] for d in catalogue["documents"]] == ["Open Policy"]
    assert json.loads(files["catalogue.jsonld"])["@type"] == "dcat:Catalog"
    assert zipfile.ZipFile(io.BytesIO(export.zip_bytes(files))).namelist()

    calls = []

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "c0"}})
        if path.endswith("/git/commits/c0"):
            return httpx.Response(200, json={"tree": {"sha": "t0"}})
        if "/git/trees/t0" in path:
            return httpx.Response(200, json={"tree": [{"path": "policies/old.pdf", "type": "blob", "sha": "x"}]})
        if path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "b1"})
        if path.endswith("/git/trees"):
            deleted = [e for e in json.loads(request.content)["tree"] if e["sha"] is None]
            assert deleted and deleted[0]["path"] == "policies/old.pdf"
            return httpx.Response(201, json={"sha": "t1"})
        if path.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "c1"})
        if path.endswith("/git/refs/heads/main"):
            return httpx.Response(200, json={})
        return httpx.Response(404)

    from promulga.config import get_config
    object.__setattr__(get_config(), "github_repo", "me/site")
    object.__setattr__(get_config(), "github_token", "test-token")
    sha = export.push_github(files, "test", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert sha == "c1" and ("PATCH", "/repos/me/site/git/refs/heads/main") in calls


def test_onlyoffice_permissions_and_signed_config(db):
    from promulga.config import get_config
    object.__setattr__(get_config(), "onlyoffice_url", "/onlyoffice")
    object.__setattr__(get_config(), "onlyoffice_jwt_secret", "s3cret")
    admin = _admin(db)
    doc = workflow.create_document(db, admin, title="Online", doc_type="Policy", summary="", owner_email="o@example.org",
                                   reviewer_emails=["r@example.org"], approver_emails=["a@example.org"], term_ids=[],
                                   file=None)
    o, r = _user(db, "o@example.org"), _user(db, "r@example.org")
    assert onlyoffice.permissions(doc, o)["edit"] and not onlyoffice.permissions(doc, r)["comment"]
    workflow.submit(db, o, doc)
    p_owner, p_rev = onlyoffice.permissions(doc, o), onlyoffice.permissions(doc, r)
    assert not p_owner["edit"] and p_owner["comment"] and p_rev["review"] and p_rev["comment"] and not p_rev["edit"]
    import jwt
    config = onlyoffice.editor_config(doc, doc.latest_revision, r)
    assert jwt.decode(config["token"], "s3cret", algorithms=["HS256"])["document"]["key"] == config["document"]["key"]
    rev = workflow.save_online(db, r, doc, storage.read_bytes(doc.latest_revision.storage_path))
    assert rev.number == 2 and doc.status == "Review"     # reviewers' comments saved in the single version


def test_api_tokens(client):
    login(client, "admin@example.org", "admin-password-123")
    page = client.get("/admin/api-tokens")
    created = client.post("/admin/api-tokens", data={"csrf": csrf_of(page.text), "name": "OpenMetadata"})
    import re
    token = re.search(r"(prm_[A-Za-z0-9_-]+)", created.text).group(1)
    assert client.get("/api/v1/documents").status_code == 401
    r = client.get("/api/v1/documents", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["count"] == 0
    assert "prm_" not in client.get("/admin/api-tokens").text   # shown once only


def test_forced_password_change_and_security_page(app_env):
    from fastapi.testclient import TestClient

    from promulga.web.app import create_app
    with TestClient(create_app(app_env)) as c:
        login(c, "admin@example.org", "admin-password-123")
        r = c.get("/admin", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/account"
        page = c.get("/admin/account")
        c.post("/admin/account", data={"csrf": csrf_of(page.text), "name": "Admin", "current": "admin-password-123",
                                       "password": "a-brand-new-password", "password2": "a-brand-new-password"})
        assert c.get("/admin", follow_redirects=False).status_code == 200
        sec = c.get("/admin/security").text
        assert "ADMIN_PASSWORD removed" in sec and "To fix" in sec


def test_migration_from_v01_database(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from promulga import migrate
    from alembic import command
    engine = create_engine(f"sqlite:///{(tmp_path / 'old.sqlite3').as_posix()}")
    with engine.begin() as c:
        command.upgrade(migrate._config(c), migrate.BASELINE)
        c.execute(text("DROP TABLE alembic_version"))          # what a 0.1 database looks like
        c.execute(text("INSERT INTO users (id, email, name, role, active, created_at) "
                       "VALUES (1, 'u@example.org', '', 'admin', 1, '2026-01-01')"))
        c.execute(text("INSERT INTO documents (id, ref, title, summary, doc_type, status, owner_id, stage_started_at, "
                       "current_version, next_version, created_at, updated_at) VALUES (7, 'POL-0001', 't', '', "
                       "'Policy', 'Draft', 1, '2026-01-01', '', '1.0', '2026-01-01', '2026-01-01')"))
        c.execute(text("INSERT INTO assignments (document_id, user_id, role) VALUES (7, 1, 'approver')"))
    from promulga import db as database
    engine = database.init_engine(str(engine.url))           # with PRAGMA foreign_keys=ON, as in production
    with engine.connect() as c:
        assert c.execute(text("SELECT number FROM documents WHERE id = 7")).scalar() == 1
        assert "stage_log" in inspect(c).get_table_names()
