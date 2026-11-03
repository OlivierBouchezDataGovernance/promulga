"""Chatbot use and Website publication: where a document may be deployed, apart from its classification."""

from __future__ import annotations

import json
import re

import pytest

from promulga import export, naming, site_settings, workflow
from promulga.models import Document, Event, Taxonomy, Term, allowed_distributions, utcnow
from promulga.workflow import WorkflowError

from .conftest import csrf_of, login, make_pdf
from .test_v02 import _admin, _publish


def _new(db, admin, **kw):
    return workflow.create_document(db, admin, title=kw.pop("title", "Policy"), doc_type="Policy",
                                    summary="About data.", owner_email="owner@example.org", reviewer_emails=[],
                                    approver_emails=["app@example.org"], term_ids=kw.pop("term_ids", []), file=None,
                                    **kw)


def test_rules_follow_the_classification():
    assert allowed_distributions("external") == allowed_distributions("commercial") == ["no", "internal", "public"]
    assert allowed_distributions("internal") == ["no", "internal"]
    for code in ("limited", "confidential", "audit"):
        assert allowed_distributions(code) == ["no"]


def test_create_defaults_and_refusals(db):
    admin = _admin(db)
    doc = _new(db, admin)
    assert (doc.chatbot, doc.website) == ("no", "no")
    ok = _new(db, admin, title="Open", classification="external", chatbot="public", website="internal")
    assert (ok.chatbot, ok.website) == ("public", "internal")
    with pytest.raises(WorkflowError, match="Website publication"):
        _new(db, admin, title="Bad", classification="internal", website="public")
    with pytest.raises(WorkflowError, match="Chatbot use"):
        _new(db, admin, title="Bad", classification="confidential", chatbot="internal")
    with pytest.raises(WorkflowError, match="Unknown value"):
        _new(db, admin, title="Bad", chatbot="everywhere")


def test_restrictive_classification_lowers_and_logs(db):
    admin = _admin(db)
    doc = _new(db, admin, classification="external", chatbot="public", website="public")
    lowered = workflow.update_metadata(db, admin, doc, title=doc.title, summary=doc.summary, term_ids=[],
                                       classification="internal", chatbot="public", website="internal")
    assert (doc.chatbot, doc.website) == ("internal", "internal")
    assert lowered == ["Chatbot use lowered from Public to Internal: the classification Internal publication "
                       "does not allow more."]
    workflow.update_metadata(db, admin, doc, title=doc.title, summary=doc.summary, term_ids=[],
                             classification="confidential")
    assert (doc.chatbot, doc.website) == ("no", "no")
    event = db.query(Event).filter_by(document_id=doc.id).order_by(Event.id.desc()).first()
    assert "classification Internal publication" in event.detail and "website publication Internal" in event.detail
    assert "lowered" in event.detail
    # The classification does not change: asking for a wider distribution is refused, nothing changes.
    with pytest.raises(WorkflowError, match="can only be No"):
        workflow.update_metadata(db, admin, doc, title=doc.title, summary=doc.summary, term_ids=[], website="internal")


def test_variables_printed_in_the_control_table(db):
    admin = _admin(db)
    doc = _new(db, admin, classification="external", chatbot="internal", website="public")
    vals = naming.values(doc, site=site_settings.get_all(db))
    assert (vals["CHATBOT"], vals["WEBSITE"]) == ("Internal", "Public")
    keys = [k for k, _ in naming.LABELS]
    assert keys.index("CLASSIFICATION") < keys.index("CHATBOT") < keys.index("WEBSITE")


def test_export_and_corpus(db, client):
    from promulga import db as database
    with database.session_scope() as s:
        admin = _admin(s)
        _publish(s, admin, title="Open", classification="external", chatbot="public", website="public")
        _publish(s, admin, title="Intranet", chatbot="internal", website="internal")
        _publish(s, admin, title="Hidden")
        refs = {d.title: d.ref for d in s.query(Document).all()}
        assert [d.title for d in export.exported_documents(s)] == ["Open"]
        cat = json.loads(export.build(s)["catalogue.json"])
        assert (cat["documents"][0]["chatbot"], cat["documents"][0]["website"]) == ("public", "public")
        site_settings.set_many(s, {"export_scope": "all"})
        assert {d.title for d in export.exported_documents(s)} == {"Open", "Intranet"}

    login(client, "admin@example.org", "admin-password-123")
    catalogue, internal_feed, public_feed = (_token(client, a) for a in ("", "internal", "public"))
    public = client.get("/api/v1/corpus", headers=public_feed).json()     # the audience of the token
    assert public["audience"] == "public" and "withdrawals" not in public
    assert [d["ref"] for d in public["documents"]] == [refs["Open"]] and "Open body" in public["documents"][0]["text"]
    internal = client.get("/api/v1/corpus", headers=internal_feed).json()
    assert {d["ref"] for d in internal["documents"]} == {refs["Open"], refs["Intranet"]}
    assert client.get("/api/v1/corpus?audience=public", headers=internal_feed).json()["count"] == 1
    assert client.get("/api/v1/corpus?audience=internal", headers=public_feed).status_code == 403
    assert client.get("/api/v1/corpus?audience=everyone", headers=internal_feed).status_code == 400
    assert client.get("/api/v1/corpus", headers=catalogue).status_code == 403          # catalogue token
    assert client.get("/api/v1/documents", headers=public_feed).status_code == 403     # chatbot token
    listed = client.get("/api/v1/documents?website=internal", headers=catalogue).json()
    assert [d["ref"] for d in listed["documents"]] == [refs["Intranet"]] and listed["documents"][0]["chatbot"] == "internal"

    register = client.get("/admin/documents?chatbot=public")
    assert refs["Open"] in register.text and refs["Hidden"] not in register.text
    assert "Chatbot,Website" in client.get("/admin/documents.csv").text


def test_edit_form_lowers_with_a_warning(client):
    from promulga import db as database
    with database.session_scope() as s:
        doc = _new(s, _admin(s), classification="commercial", website="public")
        doc_id = doc.id
    login(client, "admin@example.org", "admin-password-123")
    form = client.get(f"/admin/documents/{doc_id}/edit")
    assert 'name="chatbot"' in form.text and 'name="website"' in form.text
    data = {"csrf": csrf_of(form.text), "title": "Policy", "summary": "About data.", "owner": "owner@example.org",
            "approvers": "app@example.org", "classification": "limited", "chatbot": "no", "website": "public"}
    page = client.post(f"/admin/documents/{doc_id}/edit", data=data, follow_redirects=True)
    assert "Website publication lowered from Public to No" in page.text
    assert "Website: <strong>No</strong>" in page.text


def _token(client, audience: str) -> dict:
    page = client.get("/admin/api-tokens")
    created = client.post("/admin/api-tokens", data={"csrf": csrf_of(page.text), "name": f"Bot {audience}",
                                                     "audience": audience})
    return {"Authorization": f"Bearer {re.search(r'(prm_[A-Za-z0-9_-]+)', created.text).group(1)}"}


def test_feed_since_and_withdrawals(db, client):
    from promulga import db as database
    with database.session_scope() as s:
        admin = _admin(s)
        a = _publish(s, admin, title="Alpha", classification="external", chatbot="public")
        b = _publish(s, admin, title="Beta", classification="external", chatbot="public")
        c = _publish(s, admin, title="Gamma", classification="external", chatbot="internal")
        d = _publish(s, admin, title="Delta", chatbot="internal")
        refs = {x.title: x.ref for x in (a, b, c, d)}
        ids = {x.title: x.id for x in (a, b, c, d)}
    login(client, "admin@example.org", "admin-password-123")
    public_feed, internal_feed = _token(client, "public"), _token(client, "internal")
    mark = utcnow().isoformat()
    with database.session_scope() as s:
        admin = _admin(s)
        alpha, beta, gamma, delta = (s.get(Document, ids[t]) for t in ("Alpha", "Beta", "Gamma", "Delta"))
        workflow.update_metadata(s, admin, alpha, title=alpha.title, summary=alpha.summary, term_ids=[],
                                 classification="confidential")                 # lowered to no
        workflow.update_metadata(s, admin, beta, title=beta.title, summary=beta.summary, term_ids=[],
                                 chatbot="internal")                            # public -> internal
        workflow.update_metadata(s, admin, gamma, title=gamma.title, summary=gamma.summary, term_ids=[],
                                 chatbot="public")                              # internal -> public
        workflow.update_metadata(s, admin, delta, title=delta.title, summary=delta.summary, term_ids=[],
                                 classification="limited")                      # internal only, lowered
    pub = client.get(f"/api/v1/corpus?since={mark}", headers=public_feed).json()
    assert [x["ref"] for x in pub["documents"]] == [refs["Gamma"]]
    assert {(w["ref"], w["reason"]) for w in pub["withdrawals"]} == {(refs["Alpha"], "classification"),
                                                                   (refs["Beta"], "chatbot use")}
    # Delta was never public: the public client never learns of it.
    assert refs["Delta"] not in json.dumps(pub)
    internal = client.get(f"/api/v1/corpus?since={mark}", headers=internal_feed).json()
    assert {w["ref"] for w in internal["withdrawals"]} == {refs["Alpha"], refs["Delta"]}
    assert internal["documents"] == []      # Beta and Gamma stay available to an internal chatbot: nothing new
    later = client.get(f"/api/v1/corpus?since={utcnow().isoformat()}", headers=public_feed).json()
    assert later["documents"] == [] and later["withdrawals"] == []
    assert client.get("/api/v1/corpus?since=yesterday", headers=public_feed).status_code == 400
    # A new version in force is sent again, even though Chatbot use did not change.
    mark = utcnow().isoformat()
    with database.session_scope() as s:
        admin, gamma = _admin(s), s.get(Document, ids["Gamma"])
        workflow.start_revision(s, admin, gamma, "minor", "Update")
        owner = s.query(type(admin)).filter_by(email="owner@example.org").one()
        workflow.check_in(s, owner, gamma, "v2.pdf", make_pdf("Gamma second version"))
        workflow.submit(s, owner, gamma)
        workflow.approve(s, s.query(type(admin)).filter_by(email="app@example.org").one(), gamma)
        assert gamma.current_version == "1.1"
    again = client.get(f"/api/v1/corpus?since={mark}", headers=public_feed).json()
    assert [(x["ref"], x["version"]) for x in again["documents"]] == [(refs["Gamma"], "1.1")]
    assert "Gamma second version" in again["documents"][0]["text"]


def _migrate(engine, revision: str, direction: str) -> None:
    from alembic import command

    from promulga import migrate
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.commit()
        with conn.begin():
            getattr(command, direction)(migrate._config(conn), revision)
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        conn.commit()


@pytest.mark.parametrize("scope", ["public", "all"])
def test_migration_keeps_the_exported_documents(db, scope):
    from promulga import db as database
    admin = _admin(db)
    tax = Taxonomy(name="Visibility", slug="visibility")
    tag = Term(taxonomy=tax, name="Public", slug="public")
    db.add_all([tax, tag])
    db.flush()
    docs = {
        "tagged_external": _new(db, admin, title="Tagged external", classification="external", term_ids=[tag.id]),
        "tagged_internal": _new(db, admin, title="Tagged internal", term_ids=[tag.id]),
        "confidential": _new(db, admin, title="Secret", classification="confidential"),
    }
    site_settings.set_many(db, {"export_scope": scope})
    ids = {k: d.id for k, d in docs.items()}
    db.commit()
    db.close()
    engine = database.get_engine()
    _migrate(engine, "0007_groups", "downgrade")
    _migrate(engine, "head", "upgrade")
    with engine.connect() as conn:
        rows = {r[0]: (r[1], r[2]) for r in conn.exec_driver_sql("SELECT id, chatbot, website FROM documents")}
        terms = conn.exec_driver_sql("SELECT count(*) FROM document_terms").scalar()
    assert terms == 2                                            # the table rebuild kept the tags
    assert rows[ids["tagged_external"]] == ("no", "public")
    assert rows[ids["tagged_internal"]] == ("no", "internal" if scope == "all" else "no")
    assert rows[ids["confidential"]] == ("no", "no")
