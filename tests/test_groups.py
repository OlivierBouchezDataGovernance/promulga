"""User groups as readers, reviewers and approvers (PROM-70)."""

from __future__ import annotations

import pytest

from promulga import accounts, workflow
from promulga.models import GROUP_LOCAL, GROUP_SSO, Group, User
from promulga.workflow import WorkflowError

from .conftest import csrf_of, login, make_pdf
from .test_v02 import _admin, _user


def _people(db, *emails, role="contributor"):
    out = []
    for e in emails:
        u = db.query(User).filter_by(email=e).first()
        if u is None:
            u = User(email=e, role=role, name=e.split("@")[0].title())
            db.add(u)
        out.append(u)
    db.flush()
    return out


def _group(db, name, members, any_member=False):
    g = Group(name=name, source=GROUP_LOCAL, any_member=any_member, members=list(members))
    db.add(g)
    db.flush()
    return g


def test_parse_people_keeps_group_tokens():
    assert accounts.parse_people("ann@example.org, @DPO-team; bob@example.org") == [
        "ann@example.org", "@dpo-team", "bob@example.org"]
    with pytest.raises(accounts.AccountError):
        accounts.parse_people("@x")


def test_group_of_approvers_where_one_member_is_enough(db):
    admin = _admin(db)
    ann, bob = _people(db, "ann@example.org", "bob@example.org")
    _group(db, "dpo-team", [ann, bob], any_member=True)
    doc = workflow.create_document(db, admin, title="Privacy notice", doc_type="Policy", summary="",
                                   owner_email="owner@example.org", reviewer_emails=[],
                                   approver_emails=["@dpo-team"], term_ids=[], file=None)
    assert sorted(a.user.email for a in doc.approvers) == ["ann@example.org", "bob@example.org"]
    assert workflow.participant_tokens(doc, "approver") == ["@dpo-team"]
    owner = _user(db, "owner@example.org")
    workflow.check_in(db, owner, doc, "final.pdf", make_pdf("Privacy"))
    workflow.submit(db, owner, doc)
    workflow.approve(db, bob, doc)
    assert doc.status == "Published", "one member of the group approved for the group"


def test_group_where_every_member_acts(db):
    admin = _admin(db)
    ann, bob = _people(db, "ann@example.org", "bob@example.org")
    _group(db, "council", [ann, bob])
    doc = workflow.create_document(db, admin, title="Charter", doc_type="Policy", summary="",
                                   owner_email="owner@example.org", reviewer_emails=["@council"],
                                   approver_emails=["app@example.org"], term_ids=[], file=None)
    owner = _user(db, "owner@example.org")
    workflow.check_in(db, owner, doc, "final.pdf", make_pdf("Charter"))
    workflow.submit(db, owner, doc)
    workflow.review_done(db, ann, doc)
    assert doc.status == "Review" and workflow.pending_users(doc) == [bob]
    workflow.review_done(db, bob, doc)
    assert doc.status == "Revision"


def test_group_of_readers_can_read_a_restricted_document(db):
    admin = _admin(db)
    ann, bob, carl = _people(db, "ann@example.org", "bob@example.org", "carl@example.org", role="viewer")
    _group(db, "finance", [ann, bob])
    doc = workflow.create_document(db, admin, title="Budget", doc_type="Policy", summary="",
                                   owner_email="owner@example.org", reviewer_emails=[],
                                   approver_emails=["app@example.org"], term_ids=[], file=None,
                                   classification="confidential", readers=["@finance"])
    assert doc.audience_emails == ["ann@example.org", "bob@example.org"]
    assert doc.readable_by(ann) and not doc.readable_by(carl)
    assert [u.email for u in workflow.acknowledgment_audience(db, doc)] == ["ann@example.org", "bob@example.org"]


def test_unknown_or_empty_group_is_refused(db):
    admin = _admin(db)
    _group(db, "empty", [])
    for token in ("@nobody", "@empty"):
        with pytest.raises(WorkflowError):
            workflow.create_document(db, admin, title="X", doc_type="Policy", summary="",
                                     owner_email="owner@example.org", reviewer_emails=[], approver_emails=[token],
                                     term_ids=[], file=None)


def test_sso_groups_follow_the_directory(db):
    ann, = _people(db, "ann@example.org")
    local = _group(db, "local-team", [ann])
    accounts.sync_sso_groups(db, ann, ["dg-lead", "data-steward"])
    db.flush()
    names = sorted(g.name for g in db.query(Group).filter_by(source=GROUP_SSO).all() if ann in g.members)
    assert names == ["data-steward", "dg-lead"]
    accounts.sync_sso_groups(db, ann, "dg-lead")
    db.flush()
    steward = db.query(Group).filter_by(name="data-steward").one()
    assert ann not in steward.members and ann in local.members, "local groups are never touched"


def test_groups_page_creates_a_group_used_in_the_forms(client):
    from promulga import db as database
    with database.session_scope() as db:
        _people(db, "ann@example.org", "bob@example.org")
        ids = [u.id for u in db.query(User).filter(User.email.in_(["ann@example.org", "bob@example.org"]))]
    login(client, "admin@example.org", "admin-password-123")
    page = client.get("/admin/groups")
    r = client.post("/admin/groups", data={"csrf": csrf_of(page.text), "name": "@DPO-team", "any_member": "1",
                                           "members": [str(i) for i in ids]}, follow_redirects=False)
    assert r.status_code == 303
    assert "@dpo-team" in client.get("/admin/groups").text
    assert 'value="@dpo-team"' in client.get("/admin/documents/new").text
