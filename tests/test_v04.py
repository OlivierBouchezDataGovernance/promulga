"""v0.4 (sprint 1): parent documents, stage permissions, decisions on remarks, Word comments,
PDF copies instead of the Word file, approval PDF, translations and external references."""

from __future__ import annotations

import io
import zipfile

import pytest

from promulga import docx_meta, onlyoffice, site_settings, storage, workflow
from promulga.workflow import WorkflowError

from .conftest import csrf_of, login, make_pdf
from .test_v02 import _admin, _publish, _user


CHARTER_TYPES = [
    {"name": "Charter", "prefix": "CHT", "icon": "scale"},
    {"name": "Policy", "prefix": "POL", "icon": "shield-check"},
    {"name": "Standard", "prefix": "STD", "icon": "ruler"},
    {"name": "Guideline", "prefix": "GDL", "icon": "compass"},
    {"name": "Procedure", "prefix": "PRO", "icon": "list-ordered"},
    {"name": "Control", "prefix": "CTR", "icon": "clipboard-check"},
]


def _create(db, admin, title, doc_type, parent=None, reviewers=(), owner="owner@example.org"):
    return workflow.create_document(db, admin, title=title, doc_type=doc_type, summary="", owner_email=owner,
                                    reviewer_emails=list(reviewers), approver_emails=["app@example.org"],
                                    term_ids=[], file=None, parent_id=parent.id if parent else None)


def _word_with_comment(author: str, text: str, passage: str) -> bytes:
    """A minimal .docx with one Word comment on `passage`."""
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    document = (f'<w:document {w}><w:body><w:p><w:r><w:t>Intro </w:t></w:r><w:commentRangeStart w:id="0"/>'
                f'<w:r><w:t>{passage}</w:t></w:r><w:commentRangeEnd w:id="0"/></w:p></w:body></w:document>')
    comments = (f'<w:comments {w}><w:comment w:id="0" w:author="{author}" w:date="2026-10-30T10:00:00Z">'
                f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:comment></w:comments>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("word/document.xml", document)
        z.writestr("word/comments.xml", comments)
    return buf.getvalue()


# --------------------------------------------------------------------------- hierarchy

def test_hierarchy_charter_policy_standard(db):
    admin = _admin(db)
    site_settings.set_many(db, {"doc_types": CHARTER_TYPES})
    with pytest.raises(WorkflowError, match="hangs under a Charter"):
        _create(db, admin, "Security", "Policy")                     # a Policy needs a Charter
    charter = _create(db, admin, "Data Governance Framework", "Charter")
    with pytest.raises(WorkflowError, match="top of the hierarchy"):
        _create(db, admin, "Second charter", "Charter", parent=charter)
    policy = _create(db, admin, "Security", "Policy", parent=charter)
    with pytest.raises(WorkflowError, match="not under a Charter"):
        _create(db, admin, "Passwords", "Standard", parent=charter)  # a Standard hangs under a Policy
    std = _create(db, admin, "Passwords", "Standard", parent=policy)
    ctr = _create(db, admin, "Password check", "Control", parent=std)
    assert ctr.ancestors == [std, policy, charter] and [d.ref for d in policy.children] == [std.ref]

    # Candidates and cycles
    site = site_settings.get_all(db)
    assert workflow.parent_types(site, "Guideline") == ["Policy"] and workflow.parent_types(site, "Charter") == []
    assert workflow.parent_candidates(db, site, "Standard") == [policy]
    other = _create(db, admin, "Privacy", "Policy", parent=charter)
    workflow.update_metadata(db, admin, std, title=std.title, summary="", term_ids=[], parent_id=other.id)
    assert std.parent == other and ctr.ancestors[-1] == charter
    with pytest.raises(WorkflowError):
        workflow.update_metadata(db, admin, std, title=std.title, summary="", term_ids=[], parent_id=ctr.id)

    rows = workflow.tree(db)
    assert [(depth, d.title) for depth, d in rows][:2] == [(0, "Data Governance Framework"), (1, "Security")]
    assert (3, "Password check") in [(depth, d.title) for depth, d in rows]


def test_parent_on_document_control_page_and_tree_page(client):
    from promulga import db as database
    from promulga.models import User
    with database.session_scope() as db:
        admin = db.query(User).filter_by(email="admin@example.org").one()
        site_settings.set_many(db, {"doc_types": CHARTER_TYPES})
        charter = _create(db, admin, "Framework", "Charter")
        policy = _create(db, admin, "Security", "Policy", parent=charter)
        from promulga import naming
        assert naming.values(policy)["PARENT"] == "CHT-0001 Framework"
        assert ("PARENT", "Parent document") in naming.LABELS
        pid = policy.id
    login(client, "admin@example.org", "admin-password-123")
    page = client.get(f"/admin/documents/{pid}").text
    assert "Hangs under" in page and "CHT-0001 Framework" in page
    tree = client.get("/admin/documents/tree").text
    assert "CHT-0001" in tree and "POL-0002" in tree


# --------------------------------------------------------------------------- stage permissions

def test_draft_is_private_and_revision_accepts_replies_only(db):
    admin = _admin(db)
    doc = _create(db, admin, "Retention", "Policy", reviewers=["rev@example.org"])
    owner, rev, app = _user(db, "owner@example.org"), _user(db, "rev@example.org"), _user(db, "app@example.org")
    assert workflow.can_see(doc, owner) and not workflow.can_see(doc, rev) and not workflow.can_see(doc, app)
    workflow.check_in(db, owner, doc, "draft.pdf", make_pdf("v1"))
    workflow.submit(db, owner, doc)
    assert workflow.can_see(doc, rev) and "comment" in workflow.allowed_actions(doc, rev)
    remark = workflow.add_comment(db, rev, doc, "Add the legal basis.")
    workflow.review_done(db, rev, doc)
    assert doc.status == "Revision"
    actions = workflow.allowed_actions(doc, rev)
    assert "reply" in actions and "comment" not in actions and "decide" not in actions
    assert "decide" in workflow.allowed_actions(doc, owner)
    assert workflow.undecided(doc) == [remark]
    workflow.decide_comment(db, owner, doc, remark, "rejected", "Covered by the parent policy.")
    assert remark.decision == "rejected" and remark.resolved and workflow.undecided(doc) == []
    rows = workflow.comments_csv_rows(doc)
    assert rows[1][5] == "Add the legal basis." and rows[1][6:8] == ["rejected", "Covered by the parent policy."]


def test_word_file_only_in_draft_and_revision(client):
    from promulga import db as database
    from promulga.models import Document, User
    login(client, "admin@example.org", "admin-password-123")
    with database.session_scope() as db:
        admin = db.query(User).filter_by(email="admin@example.org").one()
        doc = _create(db, admin, "Access", "Policy", reviewers=["admin@example.org"], owner="admin@example.org")
        doc_id, rev_id = doc.id, doc.latest_revision.id
    r = client.get(f"/admin/documents/{doc_id}/revisions/{rev_id}", follow_redirects=False)
    assert r.status_code == 200 and r.content[:2] == b"PK"           # Draft: the owner gets the Word file
    with database.session_scope() as db:
        d = db.get(Document, doc_id)
        workflow.submit(db, d.owner, d)
    with database.session_scope() as db:
        d = db.get(Document, doc_id)
        d.owner_id = db.query(User).filter_by(email="admin@example.org").one().id
        assert d.status == "Review"
    # A manager who owns it still gets a PDF copy during the review (no converter in tests: an error message).
    r = client.get(f"/admin/documents/{doc_id}/revisions/{rev_id}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith(f"/revisions/{rev_id}/copy.pdf")
    r = client.get(r.headers["location"], follow_redirects=True)
    assert "The PDF copy cannot be made" in r.text


def test_review_copy_is_watermarked(db, monkeypatch):
    admin = _admin(db)
    doc = _create(db, admin, "Logging", "Policy", reviewers=["rev@example.org"])
    owner = _user(db, "owner@example.org")
    workflow.check_in(db, owner, doc, "draft.pdf", make_pdf("Logging body"))
    workflow.submit(db, owner, doc)
    from pypdf import PdfReader
    copy = workflow.review_copy(db, doc, doc.latest_revision)
    text = PdfReader(io.BytesIO(copy)).pages[0].extract_text()
    assert "REVIEW COPY" in text and "not in force" in text
    assert workflow.review_copy(db, doc, doc.latest_revision) == copy          # kept on disk


def test_onlyoffice_reviewers_comment_only(db):
    admin = _admin(db)
    doc = _create(db, admin, "Backup", "Policy", reviewers=["rev@example.org"])
    owner, rev = _user(db, "owner@example.org"), _user(db, "rev@example.org")
    assert onlyoffice.permissions(doc, owner)["download"]                     # Draft: the owner edits
    workflow.submit(db, owner, doc)
    p = onlyoffice.permissions(doc, rev)
    assert p["comment"] and not p["edit"] and not p["review"] and not p["download"]


def test_approval_pdf_is_frozen_and_published(db):
    admin = _admin(db)
    doc = _create(db, admin, "Encryption", "Policy")
    owner, app = _user(db, "owner@example.org"), _user(db, "app@example.org")
    workflow.check_in(db, owner, doc, "final.pdf", make_pdf("Encryption body"))
    workflow.submit(db, owner, doc)
    assert doc.status == "Approval" and doc.approval_pdf_path and doc.approval_revision_id == doc.latest_revision.id
    sha = doc.approval_pdf_sha
    with pytest.raises(WorkflowError):
        workflow.save_online(db, owner, doc, make_pdf("changed"))             # frozen
    workflow.approve(db, app, doc)
    assert doc.status == "Published"
    from promulga.models import Event
    published = db.query(Event).filter_by(document_id=doc.id).filter(Event.action == "Published v1.0").one()
    assert sha in published.detail


def test_word_comments_are_imported_as_remarks(db):
    data = _word_with_comment("Rita Reviewer", "Too vague.", "every year")
    assert docx_meta.word_comments(data) == [{"id": "0", "author": "Rita Reviewer", "date": "2026-10-30T10:00:00Z",
                                              "text": "Too vague.", "anchor": "every year"}]
    admin = _admin(db)
    doc = _create(db, admin, "Review cycle", "Policy", reviewers=["rev@example.org"])
    owner, rev = _user(db, "owner@example.org"), _user(db, "rev@example.org")
    rev.name = "Rita Reviewer"
    path, digest = storage.save_bytes(str(doc.id), "draft.docx", data)
    from promulga.models import Revision
    db.add(Revision(document=doc, number=9, filename="draft.docx", content_type="application/octet-stream",
                    storage_path=path, size=len(data), sha256=digest, uploaded_by=owner, status_at_upload="Draft"))
    db.flush()
    db.refresh(doc)
    workflow.submit(db, owner, doc)
    workflow.review_done(db, rev, doc)
    imported = [c for c in doc.comments if c.source == "word"]
    assert len(imported) == 1 and imported[0].author == rev and imported[0].location == "every year"
    assert workflow.undecided(doc) == imported
    assert workflow.import_word_comments(db, doc) == 0                        # only once


# --------------------------------------------------------------------------- annexes

def test_translations_follow_the_english_version(db):
    admin = _admin(db)
    draft = _create(db, admin, "Unpublished", "Policy")
    owner = _user(db, "owner@example.org")
    with pytest.raises(WorkflowError, match="publish the English version first"):
        workflow.add_translation(db, owner, draft, "fr", ("fr.pdf", make_pdf("Bonjour")))
    doc = _publish(db, admin)
    with pytest.raises(WorkflowError, match="Unknown language"):
        workflow.add_translation(db, owner, doc, "de", ("de.pdf", make_pdf("Hallo")))
    fr = workflow.add_translation(db, owner, doc, "fr", ("fr.pdf", make_pdf("Bonjour")))
    nl = workflow.add_translation(db, admin, doc, "nl", ("nl.docx", b"PK fake"), method="human")
    assert doc.translation("fr") == fr and fr.is_current and "English version prevails" in fr.notice
    assert nl.notice.startswith("Translation of version 1.0")
    with pytest.raises(WorkflowError, match="Only the owner"):
        workflow.add_translation(db, _user(db, "app@example.org"), doc, "fr", ("x.pdf", make_pdf()))
    # A new English version: the old translation stays with version 1.0, marked outdated.
    workflow.start_revision(db, admin, doc, "minor", "Typos")
    workflow.check_in(db, owner, doc, "v11.pdf", make_pdf("v1.1"))
    workflow.submit(db, owner, doc)
    workflow.approve(db, _user(db, "app@example.org"), doc)
    assert doc.current_version == "1.1" and doc.translation("fr") is None and not fr.is_current


def test_external_references_are_shared(db, client):
    admin = _admin(db)
    a = _publish(db, admin, title="Privacy Policy")
    b = _publish(db, admin, title="Retention Policy")
    with pytest.raises(WorkflowError, match="https"):
        workflow.add_reference(db, admin, a, title="GDPR", url="ftp://x")
    gdpr = workflow.add_reference(db, admin, a, title="General Data Protection Regulation", issuer="European Union",
                                  edition="2016/679", url="https://eur-lex.europa.eu/eli/reg/2016/679/oj")
    workflow.link_reference(db, admin, b, gdpr)
    assert gdpr.documents == [a, b] and gdpr.label == "European Union - General Data Protection Regulation - 2016/679"
    workflow.unlink_reference(db, admin, b, gdpr)
    assert b.references == []
    db.commit()
    page = client.get(f"/d/{a.ref}").text
    assert "General Data Protection Regulation" in page and "eur-lex.europa.eu" in page
    workflow.add_translation(db, admin, a, "fr", ("fr.pdf", make_pdf("Vie privée")))
    db.commit()
    login(client, "admin@example.org", "admin-password-123")
    page = client.get(f"/admin/documents/{a.id}").text
    assert "Annexes" in page and "Français - version 1.0" in page and "Unlink" in page
    assert f"Attach to version 1.0" in page and "Retention Policy" not in page.split("Annexes")[0][-200:]


def test_portal_shows_hierarchy_and_translations(db, client):
    admin = _admin(db)
    site_settings.set_many(db, {"doc_types": CHARTER_TYPES})
    charter = _create(db, admin, "Framework", "Charter")
    owner, app = _user(db, "owner@example.org"), _user(db, "app@example.org")
    for d in [charter]:
        workflow.check_in(db, owner, d, "final.pdf", make_pdf("Charter"))
        workflow.submit(db, owner, d)
        workflow.approve(db, app, d)
    policy = _create(db, admin, "Security", "Policy", parent=charter)
    workflow.check_in(db, owner, policy, "final.pdf", make_pdf("Security"))
    workflow.submit(db, owner, policy)
    workflow.approve(db, app, policy)
    workflow.add_translation(db, owner, policy, "nl", ("nl.pdf", make_pdf("Beveiliging")))
    db.commit()
    page = client.get(f"/d/{policy.ref}").text
    assert f"Applies <a href=\"/d/{charter.ref}\">" in page and "Nederlands" in page
    assert policy.ref in client.get(f"/d/{charter.ref}").text
    t = policy.translations[0]
    assert client.get(f"/d/{policy.ref}/translations/{t.id}").content.startswith(b"%PDF")


def test_file_of_a_refused_creation_is_kept(client):
    """Bug found in the test of sprint 1: the form was refused, shown again with an empty file field, and the
    next attempt created the document from the template instead of the uploaded file."""
    login(client, "admin@example.org", "admin-password-123")
    form = client.get("/admin/documents/new")
    data = {"csrf": csrf_of(form.text), "title": "Framework", "doc_type": "Policy", "owner": "o@example.org"}
    r = client.post("/admin/documents/new", data=data, files={"file": ("charter.pdf", make_pdf("Charter"), "application/pdf")})
    assert r.status_code == 400 and "charter.pdf" in r.text and 'name="pending_file"' in r.text
    import re as _re
    token = _re.search(r'name="pending_file" value="([0-9a-f]+)"', r.text).group(1)
    r = client.post("/admin/documents/new", data={**data, "csrf": csrf_of(r.text), "approvers": "a@example.org",
                                                  "pending_file": token}, follow_redirects=True)
    assert "created with the file charter.pdf" in r.text


def test_participant_lists_and_default_approvers(client):
    login(client, "admin@example.org", "admin-password-123")
    form = client.get("/admin/documents/new").text
    assert 'name="approvers_pick" value="admin@example.org"' in form            # admin proposed by default
    import re as _re
    assert _re.search(r'name="approvers_pick" value="admin@example.org"\s+checked', form)
    assert "Select all" in form and 'name="readers_pick"' in form
    r = client.post("/admin/documents/new", data={
        "csrf": csrf_of(form), "title": "Picked", "doc_type": "Policy", "owner": "__other",
        "owner_other": "owner@example.org", "approvers_pick": ["admin@example.org", "owner@example.org"],
        "approvers": "extra@example.org", "readers_pick": ["admin@example.org"]}, follow_redirects=False)
    assert r.status_code == 303
    from promulga import db as database
    from promulga.models import Document
    with database.session_scope() as db:
        d = db.query(Document).filter_by(title="Picked").one()
        assert d.owner.email == "owner@example.org" and d.readers == "admin@example.org"
        assert sorted(a.user.email for a in d.approvers) == ["admin@example.org", "extra@example.org", "owner@example.org"]
        assert workflow.segregation_warnings(d) == []


def test_people_shown_by_name(db):
    admin = _admin(db)
    doc = workflow.create_document(db, admin, title="Names", doc_type="Policy", summary="",
                                   owner_email="owner@example.org", reviewer_emails=[],
                                   approver_emails=["owner@example.org", "app@example.org"], term_ids=[], file=None,
                                   readers=["owner@example.org", "nobody@example.org"])
    _user(db, "owner@example.org").name = "Olga Owner"
    from promulga import naming
    assert naming.values(doc)["READERS"] == "Olga Owner, nobody@example.org"
    assert naming.values(doc)["AUTHOR"] == "Olga Owner"


def test_reserved_document_number(db):
    admin = _admin(db)
    a = _create(db, admin, "First", "Policy")
    b = workflow.create_document(db, admin, title="Eighth", doc_type="Policy", summary="", owner_email="o@example.org",
                                 reviewer_emails=[], approver_emails=["p@example.org"], term_ids=[], file=None, number=8)
    assert (a.ref, b.ref) == ("POL-0001", "POL-0008")
    with pytest.raises(WorkflowError, match="already used"):
        workflow.create_document(db, admin, title="Again", doc_type="Policy", summary="", owner_email="o@example.org",
                                 reviewer_emails=[], approver_emails=["p@example.org"], term_ids=[], file=None, number=8)
    c = workflow.create_document(db, admin, title="Seventh", doc_type="Policy", summary="", owner_email="o@example.org",
                                 reviewer_emails=[], approver_emails=["p@example.org"], term_ids=[], file=None, number=7)
    assert c.ref == "POL-0007"


def test_published_pdf_shows_the_next_review(db):
    from datetime import date
    from pypdf import PdfReader
    admin = _admin(db)
    doc = _publish(db, admin, review_months=24)
    cover = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(storage.read_bytes(doc.latest_publication.pdf_path))).pages[:2])
    assert doc.next_review.year == date.today().year + 2 and doc.next_review.isoformat() in cover


def test_description_fits_on_the_title_page(db):
    admin = _admin(db)
    with pytest.raises(WorkflowError, match="300 characters at most"):
        workflow.create_document(db, admin, title="Long", doc_type="Policy", summary="x" * 301,
                                 owner_email="o@example.org", reviewer_emails=[], approver_emails=["p@example.org"],
                                 term_ids=[], file=None)


def test_export_scope_all_documents(db):
    import json
    from promulga import export
    admin = _admin(db)
    site_settings.set_many(db, {"doc_types": CHARTER_TYPES})
    charter = _create(db, admin, "Framework", "Charter")
    o, a = _user(db, "owner@example.org"), _user(db, "app@example.org")
    for d in [charter]:
        workflow.check_in(db, o, d, "c.pdf", make_pdf("Charter text"))
        workflow.submit(db, o, d)
        workflow.approve(db, a, d)
    pol = _create(db, admin, "Security", "Policy", parent=charter)
    pol.keywords = "access, encryption"
    workflow.check_in(db, o, pol, "p.pdf", make_pdf("Encryption at rest"))
    workflow.submit(db, o, pol)
    workflow.approve(db, a, pol)
    workflow.add_reference(db, admin, pol, title="GDPR", issuer="EU", edition="2016/679", url="https://eur-lex.europa.eu")
    assert export.exported_documents(db) == []                 # internal, untagged: not public
    site_settings.set_many(db, {"export_scope": "all"})
    files = export.build(db)
    cat = json.loads(files["catalogue.json"])
    docs = {d["ref"]: d for d in cat["documents"]}
    assert set(docs) == {charter.ref, pol.ref}
    p = docs[pol.ref]
    assert p["parent"] == charter.ref and p["keywords"] == ["access", "encryption"]
    assert p["classification"] == "Internal publication" and p["references"][0]["edition"] == "2016/679"
    assert "Encryption at rest" in p["text"] and p["versions"][0]["file"] in files
