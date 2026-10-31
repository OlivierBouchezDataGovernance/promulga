"""Distribution list on the document page: who the document goes to and whether they read it (0.4.3)."""

from __future__ import annotations

import pytest

from promulga import workflow
from promulga.models import OutboxMail, User
from promulga.security import hash_password
from promulga.workflow import WorkflowError

from .conftest import csrf_of, login
from .test_v02 import _admin, _publish, _user


def _readers(db, *emails):
    for e in emails:
        if db.query(User).filter_by(email=e).first() is None:
            db.add(User(email=e, role="viewer", name=e.split("@")[0].title()))
    db.flush()


def test_distribution_rows_and_who_follows_them(db):
    admin = _admin(db)
    doc = _publish(db, admin, ack=True)
    _readers(db, "ann@example.org", "bob@example.org")
    workflow.acknowledge(db, _user(db, "ann@example.org"), doc)
    dist = workflow.distribution(db, doc)
    assert dist["tracked"] and not dist["planned"] and not dist["named"]
    by_mail = {r["user"].email: r["ack"] for r in dist["rows"]}
    assert by_mail["ann@example.org"] is not None and by_mail["bob@example.org"] is None
    assert dist["rows"][-1]["user"].email == "ann@example.org"          # the people who have not read come first
    owner, approver = _user(db, "owner@example.org"), _user(db, "app@example.org")
    assert workflow.follows_distribution(doc, owner) and workflow.follows_distribution(doc, admin)
    assert not workflow.follows_distribution(doc, approver)
    # the owner (a contributor) may remind the people who have not read it; an approver may not
    n = workflow.remind_acknowledgments(db, owner, doc)
    assert n == len(dist["missing"]) and db.query(OutboxMail).filter_by(to="bob@example.org").count() >= 1
    with pytest.raises(WorkflowError, match="Only the owner"):
        workflow.remind_acknowledgments(db, approver, doc)


def test_planned_distribution_before_publication(db):
    admin = _admin(db)
    _readers(db, "ann@example.org", "bob@example.org")
    doc = workflow.create_document(db, admin, title="Data Use Charter", doc_type="Policy", summary="",
                                   owner_email="owner@example.org", reviewer_emails=[],
                                   approver_emails=["app@example.org"], term_ids=[], file=None, ack_required=True,
                                   readers=["ann@example.org"])
    dist = workflow.distribution(db, doc)
    assert dist["planned"] and not dist["tracked"] and dist["named"]
    assert [r["user"].email for r in dist["rows"]] == ["ann@example.org"]
    with pytest.raises(WorkflowError, match="published version"):
        workflow.remind_acknowledgments(db, admin, doc)


def test_owner_sees_the_list_on_the_document_page(client):
    from promulga import db as database
    with database.session_scope() as db:
        doc = _publish(db, _admin(db), ack=True)
        _readers(db, "ann@example.org")
        workflow.acknowledge(db, _user(db, "ann@example.org"), doc)
        owner = _user(db, "owner@example.org")
        owner.role, owner.password_hash = "contributor", hash_password("owner-password-123")
        doc_id = doc.id
    login(client, "owner@example.org", "owner-password-123")
    page = client.get(f"/admin/documents/{doc_id}").text
    assert 'id="distribution"' in page and "ann@example.org" in page and "Not read" in page
    assert "who have not read it" in page
    csv = client.get(f"/admin/documents/{doc_id}/acknowledgments.csv").text
    assert "ann@example.org" in csv and ",yes," in csv and ",no," in csv
    r = client.post(f"/admin/documents/{doc_id}/acknowledgments/remind", data={"csrf": csrf_of(page)},
                    follow_redirects=True)
    assert "Reminder sent" in r.text
