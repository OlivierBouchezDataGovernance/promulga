"""Classification "Audit - Confidential": the audience of the document only, no role overrides it."""

from __future__ import annotations

import pytest

from promulga import export, insights, site_settings, workflow
from promulga.models import User
from promulga.web.portal import visible_documents
from promulga.workflow import WorkflowError

from .conftest import login, make_pdf
from .test_v02 import _admin, _user


def _audit(db, admin, readers=("admin@example.org", "auditor@example.org"), title="Internal audit 2026"):
    doc = workflow.create_document(db, admin, title=title, doc_type="Policy", summary="Findings of the audit.",
                                   owner_email="owner@example.org", reviewer_emails=[],
                                   approver_emails=["app@example.org"], term_ids=[], file=None,
                                   classification="audit", readers=list(readers))
    owner = _user(db, "owner@example.org")
    workflow.check_in(db, owner, doc, "audit.pdf", make_pdf("Audit findings"))
    workflow.submit(db, owner, doc)
    workflow.approve(db, _user(db, "app@example.org"), doc)
    return doc


def _people(db):
    for email, role in (("auditor@example.org", "viewer"), ("lead@example.org", "manager"),
                        ("staff@example.org", "viewer")):
        if db.query(User).filter_by(email=email).first() is None:
            db.add(User(email=email, role=role))
    db.flush()


def test_audit_audience_only(db):
    admin = _admin(db)
    _people(db)
    doc = _audit(db, admin)
    assert doc.status == "Published" and doc.classification_label == "Audit - Confidential"
    lead, staff, auditor = (_user(db, e) for e in ("lead@example.org", "staff@example.org", "auditor@example.org"))
    # the audience: owner, approver, named readers (a reader reads the portal, not the working area)
    for u in (_user(db, "owner@example.org"), _user(db, "app@example.org"), admin):
        assert doc.readable_by(u) and workflow.can_see(doc, u)
    assert doc.readable_by(auditor) and not workflow.can_see(doc, auditor)
    # a Document Manager outside the audience does not even see it
    assert not doc.readable_by(lead) and not workflow.can_see(doc, lead)
    assert not doc.readable_by(staff) and not doc.readable_by(None)
    assert doc not in visible_documents(db, lead) and doc in visible_documents(db, auditor)


def test_audit_creator_must_stay_in_audience(db):
    admin = _admin(db)
    _people(db)
    with pytest.raises(WorkflowError, match="add yourself"):
        _audit(db, admin, readers=("auditor@example.org",))
    doc = _audit(db, admin)
    with pytest.raises(WorkflowError, match="add yourself"):
        workflow.update_metadata(db, admin, doc, title=doc.title, summary="", term_ids=[], classification="audit",
                                 readers=["auditor@example.org"])


def test_audit_never_leaves_the_server(db):
    admin = _admin(db)
    _people(db)
    doc = _audit(db, admin)
    site_settings.set_many(db, {"export_scope": "all", "webhook_urls": ["https://hooks.example.org/x"]})
    assert doc not in export.exported_documents(db)
    from promulga.models import WebhookDelivery
    assert all(doc.title not in w.payload for w in db.query(WebhookDelivery).all())
    k = insights.kpis(db, viewer=_user(db, "lead@example.org"))
    assert all(a["doc"] is not doc for a in k["acknowledgments"])


def test_audit_cannot_be_a_parent(db):
    admin = _admin(db)
    _people(db)
    doc = _audit(db, admin)
    site = site_settings.get_all(db)
    assert doc not in workflow.parent_candidates(db, site, "Standard")
    with pytest.raises(WorkflowError, match="cannot be the parent"):
        workflow.create_document(db, admin, title="Std", doc_type="Standard", summary="", owner_email="o@example.org",
                                 reviewer_emails=[], approver_emails=["app@example.org"], term_ids=[], file=None,
                                 parent_id=doc.id)


def test_audit_hidden_from_portal_and_api(client):
    from promulga import db as database
    with database.session_scope() as db:
        _people(db)
        doc = _audit(db, _admin(db), title="Payroll audit")
        ref = doc.ref
    login(client, "admin@example.org", "admin-password-123")
    assert client.get(f"/d/{ref}").status_code == 200
    page = client.get("/admin/documents/new")
    assert "Audit - Confidential" in page.text
