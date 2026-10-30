"""Workflow rules, tested directly on the domain functions."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from promulga import docx_meta, reminders, site_settings, storage, workflow
from promulga.models import OutboxMail, Publication, User
from promulga.workflow import WorkflowError

from .conftest import make_pdf


def _people(db):
    admin = db.query(User).filter_by(email="admin@example.org").one()
    return admin


def _new(db, admin, reviewers=("rev@example.org",), approvers=("app@example.org",)):
    return workflow.create_document(
        db, admin, title="Data Quality Policy", doc_type="Policy", summary="", owner_email="owner@example.org",
        reviewer_emails=list(reviewers), approver_emails=list(approvers), term_ids=[], file=None)


def _user(db, email):
    return db.query(User).filter_by(email=email).one()


def test_full_cycle_with_rejection_and_new_revision(db):
    admin = _people(db)
    doc = _new(db, admin)
    assert doc.ref == "POL-0001" and doc.status == "Draft"
    assert doc.stage_due == date.today() + timedelta(days=28)
    owner, rev, app = _user(db, "owner@example.org"), _user(db, "rev@example.org"), _user(db, "app@example.org")

    first = doc.latest_revision        # no file given: a blank Word document with its cover page
    assert first.filename.endswith(".docx") and docx_meta.has_cover(storage.read_bytes(first.storage_path))
    with pytest.raises(WorkflowError, match="owner"):
        workflow.check_in(db, rev, doc, "x.pdf", make_pdf())

    workflow.check_in(db, owner, doc, "draft.pdf", make_pdf("v1"), "first")
    workflow.submit(db, owner, doc)
    assert doc.status == "Review"
    with pytest.raises(WorkflowError):
        workflow.check_in(db, owner, doc, "x.pdf", make_pdf())   # frozen during review

    remark = workflow.add_comment(db, rev, doc, "Define the scope.")
    workflow.review_done(db, rev, doc)
    assert doc.status == "Revision"
    workflow.check_in(db, owner, doc, "draft.pdf", make_pdf("v2"), "scope defined")
    with pytest.raises(WorkflowError, match="Decide on every remark"):
        workflow.submit(db, owner, doc)
    with pytest.raises(WorkflowError, match="Explain why"):
        workflow.decide_comment(db, owner, doc, remark, "rejected", "")
    with pytest.raises(WorkflowError, match="only reply"):
        workflow.add_comment(db, rev, doc, "One more thing.")      # no second review round
    workflow.add_comment(db, rev, doc, "Thanks.", parent_id=remark.id)
    workflow.decide_comment(db, owner, doc, remark, "accepted")
    workflow.submit(db, owner, doc)
    assert doc.status == "Approval"

    with pytest.raises(WorkflowError, match="explain"):
        workflow.reject(db, app, doc, "")
    workflow.reject(db, app, doc, "Missing roles section")
    assert doc.status == "Revision"
    workflow.submit(db, owner, doc)
    workflow.approve(db, app, doc)
    assert doc.status == "Published" and doc.current_version == "1.0" and doc.published_at == date.today()
    pub = db.query(Publication).filter_by(document_id=doc.id).one()
    assert pub.version == "1.0" and "v2" in pub.text and "POL-0001" in pub.text   # stamped footer

    with pytest.raises(WorkflowError, match="change note"):
        workflow.start_revision(db, admin, doc, "minor")
    workflow.start_revision(db, admin, doc, "minor", "Scope extended to suppliers")
    assert doc.status == "Draft" and doc.next_version == "1.1" and doc.current_version == "1.0"
    workflow.submit(db, owner, doc)          # last revision is reused
    workflow.review_done(db, rev, doc)
    workflow.submit(db, owner, doc)
    workflow.approve(db, app, doc)
    assert doc.current_version == "1.1"
    assert doc.latest_publication.change_note == "Scope extended to suppliers"
    workflow.start_revision(db, admin, doc, "major", "Rewrite")
    assert doc.next_version == "2.0"


def test_no_reviewers_goes_straight_to_approval_and_all_approvers_needed(db):
    admin = _people(db)
    doc = _new(db, admin, reviewers=(), approvers=("a1@example.org", "a2@example.org"))
    owner = _user(db, "owner@example.org")
    workflow.check_in(db, owner, doc, "d.pdf", make_pdf())
    workflow.submit(db, owner, doc)
    assert doc.status == "Approval"
    workflow.approve(db, _user(db, "a1@example.org"), doc)
    assert doc.status == "Approval"
    with pytest.raises(WorkflowError, match="already"):
        workflow.approve(db, _user(db, "a1@example.org"), doc)
    workflow.approve(db, _user(db, "a2@example.org"), doc)
    assert doc.status == "Published"


def test_participants_change_completes_review(db):
    admin = _people(db)
    doc = _new(db, admin, reviewers=("r1@example.org", "r2@example.org"))
    owner = _user(db, "owner@example.org")
    workflow.check_in(db, owner, doc, "d.pdf", make_pdf())
    workflow.submit(db, owner, doc)
    workflow.review_done(db, _user(db, "r1@example.org"), doc)
    workflow.update_participants(db, admin, doc, owner_email="owner@example.org",
                                 reviewer_emails=["r1@example.org"], approver_emails=["app@example.org"])
    assert doc.status == "Revision"


def test_checkout_blocks_others_and_submit(db):
    admin = _people(db)
    doc = _new(db, admin)
    owner = _user(db, "owner@example.org")
    workflow.check_in(db, owner, doc, "d.pdf", make_pdf())
    workflow.check_out(db, owner, doc)
    with pytest.raises(WorkflowError, match="checked out"):
        workflow.check_in(db, admin, doc, "d.pdf", make_pdf())
    with pytest.raises(WorkflowError, match="checked out"):
        workflow.submit(db, owner, doc)
    workflow.release_check_out(db, admin, doc)
    workflow.submit(db, owner, doc)


def test_rejects_bad_files(db):
    admin = _people(db)
    doc = _new(db, admin)
    owner = _user(db, "owner@example.org")
    with pytest.raises(WorkflowError, match="not accepted"):
        workflow.check_in(db, owner, doc, "evil.exe", b"MZ")


def test_reminders_due_soon_overdue_and_repeat(db):
    admin = _people(db)
    doc = _new(db, admin)
    db.flush()
    site_settings.set_many(db, {"reminder_before_days": 3, "overdue_repeat_days": 2})
    today = date.today()

    doc.stage_due = today + timedelta(days=2)
    assert reminders.run(db, today) == 1                    # due soon, owner
    assert reminders.run(db, today) == 0                    # not twice
    doc.stage_due = today - timedelta(days=1)
    assert reminders.run(db, today) == 2                    # overdue: owner + manager digest
    assert reminders.run(db, today + timedelta(days=1)) == 1  # digest only, owner waits 2 days
    assert reminders.run(db, today + timedelta(days=2)) == 2
    subjects = [m.subject for m in db.query(OutboxMail).all()]
    assert any("overdue" in s for s in subjects) and any("due soon" in s for s in subjects)


def test_versions():
    assert workflow.bump_version("1.0", "minor") == "1.1"
    assert workflow.bump_version("1.9", "minor") == "1.10"
    assert workflow.bump_version("1.9", "major") == "2.0"
    assert workflow.bump_version("", "minor") == "0.1"
    assert workflow.normalise_version("2") == "2.0"
