"""PROM-73: demo setting, Chatbot use and Website publication beyond the classification."""

from __future__ import annotations

import pytest

from promulga import export, site_settings, workflow
from promulga.models import BEYOND_SETTING, ChannelLog, Document, Event
from promulga.workflow import WorkflowError

from .conftest import csrf_of, login
from .test_deployment import _token
from .test_v02 import _admin, _publish


def test_setting_off_keeps_the_cap(db):
    admin = _admin(db)
    with pytest.raises(WorkflowError, match="can only be No or Internal"):
        _publish(db, admin, title="Intranet", chatbot="public")
    doc = _publish(db, admin, title="Intranet", chatbot="internal", website="internal")
    db.flush()
    assert doc.feed_level == "internal" and doc.beyond_classification == []
    doc.chatbot = "public"           # written in the database directly, as on the demo VM
    db.flush()
    assert doc.feed_level == "internal" and doc.chatbot_feed == "internal"
    assert doc.beyond_classification == ["Chatbot use"]


def test_setting_on_feeds_internal_documents_as_public(db):
    admin = _admin(db)
    intranet = _publish(db, admin, title="Intranet", chatbot="internal", website="internal")
    secret = _publish(db, admin, title="Secret", classification="confidential")
    intranet.chatbot, intranet.website = "public", "public"
    db.flush()
    before = db.query(ChannelLog).count()

    assert workflow.set_distribution_beyond(db, admin, True) == 1
    db.flush()
    assert intranet.feed_level == "public" and intranet.chatbot_feed == "public"
    assert db.query(ChannelLog).count() == before + 1
    assert db.query(Event).filter(Event.action.like("Distribution beyond classification allowed%")).count() == 1
    assert workflow.set_distribution_beyond(db, admin, True) == 0          # unchanged: nothing logged
    assert [d.title for d in export.exported_documents(db)] == ["Intranet"]

    # New documents may ask for Public; restricted ones keep the cap.
    _publish(db, admin, title="Demo", chatbot="public", website="public")
    with pytest.raises(WorkflowError, match="can only be No"):
        _publish(db, admin, title="Secret 2", classification="limited", chatbot="internal")
    secret.chatbot = "public"
    db.flush()
    assert secret.feed_level == "no"

    assert workflow.set_distribution_beyond(db, admin, False) == 2           # Intranet and Demo capped again
    db.flush()
    assert intranet.feed_level == "internal" and intranet.chatbot == "public"   # the stored value is kept
    assert [d.title for d in export.exported_documents(db)] == []


def test_corpus_and_form_round_trip(db, client):
    from promulga import db as database
    with database.session_scope() as s:
        admin = _admin(s)
        doc = _publish(s, admin, title="Intranet", chatbot="internal", website="internal")
        _publish(s, admin, title="Secret", classification="confidential")
        doc_id, ref = doc.id, doc.ref
    login(client, "admin@example.org", "admin-password-123")
    public_feed = _token(client, "public")
    assert client.get("/api/v1/corpus", headers=public_feed).json()["count"] == 0

    page = client.get("/admin/settings")
    assert 'action="/admin/settings/distribution"' in page.text
    done = client.post("/admin/settings/distribution", data={"csrf": csrf_of(page.text), "beyond": "1"},
                       follow_redirects=True)
    assert "Chatbot level of 0 document(s) changed" in done.text
    with database.session_scope() as s:
        assert site_settings.get(s, BEYOND_SETTING) is True

    form = client.get(f"/admin/documents/{doc_id}/edit")
    assert "Demo setting on" in form.text
    data = {"csrf": csrf_of(form.text), "title": "Intranet", "summary": "About data quality.",
            "owner": "owner@example.org", "approvers": "app@example.org", "classification": "internal",
            "chatbot": "public", "website": "public"}
    page = client.post(f"/admin/documents/{doc_id}/edit", data=data, follow_redirects=True)
    assert "Chatbot: <strong>Public</strong>" in page.text and "Website: <strong>Public</strong>" in page.text
    assert "Beyond classification" in page.text
    again = client.get(f"/admin/documents/{doc_id}/edit")       # saving again keeps Public
    data["csrf"] = csrf_of(again.text)
    client.post(f"/admin/documents/{doc_id}/edit", data=data, follow_redirects=True)
    with database.session_scope() as s:
        doc = s.get(Document, doc_id)
        assert (doc.chatbot, doc.website, doc.chatbot_feed) == ("public", "public", "public")

    corpus = client.get("/api/v1/corpus", headers=public_feed).json()
    assert [d["ref"] for d in corpus["documents"]] == [ref] and corpus["documents"][0]["chatbot"] == "public"

    page = client.get("/admin/settings")
    client.post("/admin/settings/distribution", data={"csrf": csrf_of(page.text)})       # off again
    assert client.get("/api/v1/corpus", headers=public_feed).json()["count"] == 0
    assert "(capped)" in client.get(f"/admin/documents/{doc_id}").text
