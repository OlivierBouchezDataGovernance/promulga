"""Knowledge base for chatbots (PROM-58): named readers of restricted documents, workflow profiles per document
type, direct upload sent to approval."""

from __future__ import annotations

import io
import zipfile

import pytest

from promulga import site_settings, storage, text, workflow
from promulga.models import Event, OutboxMail, User
from promulga.workflow import WorkflowError

from .conftest import make_pdf
from .test_v02 import _admin, _publish, _user


def _reader(db, email, role="viewer"):
    user = db.query(User).filter_by(email=email).first()
    if user is None:
        user = User(email=email, role=role, name=email.split("@")[0].title())
        db.add(user)
        db.flush()
    return user


# -- PROM-66 -----------------------------------------------------------------------------------------------------

def test_named_reader_reads_a_limited_or_confidential_document(db):
    admin = _admin(db)
    ann, bob = _reader(db, "ann@example.org"), _reader(db, "bob@example.org")
    for classification in ("limited", "confidential"):
        doc = _publish(db, admin, title=f"Doc {classification}", ack=True, classification=classification)
        workflow.update_metadata(db, admin, doc, title=doc.title, summary=doc.summary, term_ids=[],
                                 readers=["ann@example.org"])
        assert ann in workflow.acknowledgment_audience(db, doc)
        assert doc.readable_by(ann), "a named reader is asked to acknowledge: they must be able to read"
        assert not doc.readable_by(bob)
        assert not doc.readable_by(None)


def test_audit_documents_keep_their_audience_rules(db):
    admin = _admin(db)
    ann, bob = _reader(db, "ann@example.org"), _reader(db, "bob@example.org", role="manager")
    doc = workflow.create_document(db, admin, title="Audit report", doc_type="Policy", summary="",
                                   owner_email="admin@example.org", reviewer_emails=[],
                                   approver_emails=["app@example.org"], term_ids=[], file=None,
                                   classification="audit", readers=["ann@example.org"])
    assert doc.readable_by(ann)
    assert not doc.readable_by(bob), "no role overrides the audience of an Audit document"


# -- PROM-62, PROM-63, PROM-64 -----------------------------------------------------------------------------------

MD = b"# Data Use Charter\n\nWe use data fairly.\n\n## Rights\n\nYou may ask.\n"


def _docx(paragraphs):
    """A minimal .docx: [(style or '', text)]."""
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = "".join(
        f'<w:p>{f"<w:pPr><w:pStyle w:val={chr(34)}{st}{chr(34)}/></w:pPr>" if st else ""}<w:r><w:t>{t}</w:t></w:r></w:p>'
        for st, t in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
                   'officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {ns}><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


def test_knowledge_type_has_the_approval_only_profile(db):
    site = site_settings.get_all(db)
    assert site_settings.doc_type_workflow(site, "Knowledge") == "approval"
    assert site_settings.doc_type_workflow(site, "Policy") == "full"
    assert site_settings.approval_types(site) == ["Knowledge"]


def test_approval_only_has_no_reviewers(db):
    admin = _admin(db)
    with pytest.raises(WorkflowError, match="no reviewers"):
        workflow.create_document(db, admin, title="FAQ", doc_type="Knowledge", summary="",
                                 owner_email="owner@example.org", reviewer_emails=["rev@example.org"],
                                 approver_emails=["app@example.org"], term_ids=[], file=("faq.md", MD))


def test_approval_only_goes_straight_to_approval_and_back_to_its_owner_when_rejected(db):
    admin = _admin(db)
    doc = workflow.create_document(db, admin, title="FAQ", doc_type="Knowledge", summary="",
                                   owner_email="owner@example.org", reviewer_emails=[],
                                   approver_emails=["app@example.org"], term_ids=[], file=("faq.pdf", make_pdf("FAQ")))
    owner, approver = _user(db, "owner@example.org"), _user(db, "app@example.org")
    assert "checkout" not in workflow.allowed_actions(doc, owner)
    assert workflow.workflow_label(doc) == "Approval only"
    workflow.submit(db, owner, doc)
    assert doc.status == "Approval"
    workflow.reject(db, approver, doc, "Wrong file")
    assert doc.status == "Draft", "no Revision stage in the Approval only profile"
    workflow.check_in(db, owner, doc, "faq-v2.pdf", make_pdf("FAQ v2"))
    workflow.submit(db, owner, doc)
    workflow.approve(db, approver, doc)
    assert doc.status == "Published" and doc.current_version == "1.0"


def test_full_profile_is_unchanged(db):
    doc = _publish(db, _admin(db), reviewers=["rev@example.org"])
    assert doc.status == "Published"
    assert workflow.workflow_label(doc) == "Full"


def test_author_uploads_files_sent_to_approval_in_one_batch(db):
    _reader(db, "author@example.org", role="contributor")
    _reader(db, "app@example.org", role="contributor")
    author = _user(db, "author@example.org")
    docx = _docx([("Heading1", "Onboarding"), ("", "Welcome to the team.")])
    out = workflow.upload_for_approval(db, author, [("data-use_charter.md", MD), ("onboarding guide.docx", docx)],
                                       doc_type="Knowledge", approver_emails=["app@example.org"],
                                       classification="internal")
    docs = [d for d, _ in out]
    assert [d.title for d in docs] == ["Data use charter", "Onboarding guide"]
    assert all(d.status == "Approval" and d.owner_id == author.id and not d.reviewers for d in docs)
    mails = db.query(OutboxMail).filter_by(to="app@example.org").all()
    assert len(mails) == 1 and "2 document(s)" in mails[0].subject, "one e-mail for the batch"
    # the stored file is the uploaded one, byte for byte
    assert storage.read_bytes(docs[1].latest_revision.storage_path) == docx
    # a second upload of the same file is flagged
    again = workflow.upload_for_approval(db, author, [("copy.md", MD)], doc_type="Knowledge",
                                         approver_emails=["app@example.org"])
    assert again[0][1] == [docs[0].ref]
    assert db.query(Event).filter_by(document_id=again[0][0].id, action="Possible duplicate").count() == 1


def test_upload_refuses_full_types_unknown_approvers_and_other_formats(db):
    _reader(db, "author@example.org", role="contributor")
    author = _user(db, "author@example.org")
    with pytest.raises(WorkflowError, match="Full workflow"):
        workflow.upload_for_approval(db, author, [("a.md", MD)], doc_type="Policy", approver_emails=["admin@example.org"])
    with pytest.raises(WorkflowError, match="Unknown approver"):
        workflow.upload_for_approval(db, author, [("a.md", MD)], doc_type="Knowledge",
                                     approver_emails=["nobody@example.org"])
    with pytest.raises(WorkflowError, match="only"):
        workflow.upload_for_approval(db, author, [("a.xlsx", b"x")], doc_type="Knowledge",
                                     approver_emails=["admin@example.org"])
    with pytest.raises(WorkflowError, match="Document Manager"):
        workflow.create_document(db, author, title="X", doc_type="Policy", summary="", owner_email=author.email,
                                 reviewer_emails=[], approver_emails=["admin@example.org"], term_ids=[], file=None)


def test_publication_keeps_a_clean_text_for_the_chatbot(db):
    _reader(db, "app@example.org", role="contributor")
    admin = _admin(db)
    (doc, _), = workflow.upload_for_approval(db, admin, [("charter.md", MD)], doc_type="Knowledge",
                                             approver_emails=["app@example.org"])
    # no converter in the tests: give the approvers a PDF to read
    workflow.reject(db, _user(db, "app@example.org"), doc, "Please send a PDF")
    workflow.check_in(db, admin, doc, "charter.pdf", make_pdf("Data Use Charter body"))
    workflow.submit(db, admin, doc)
    workflow.approve(db, _user(db, "app@example.org"), doc)
    pub = doc.latest_publication
    assert "Data Use Charter body" in pub.text and "Approved by" not in pub.text


def test_clean_text_keeps_headings_and_tables():
    assert text.clean_text("a.md", MD).startswith("# Data Use Charter")
    docx = _docx([("Heading2", "Scope"), ("", "All staff.")])
    assert text.clean_text("a.docx", docx) == "## Scope\n\nAll staff."
    assert "Hello" in text.clean_text("a.pdf", make_pdf("Hello"))
    assert text.clean_text("a.docx", b"not a zip") == ""


def test_upload_page_and_settings_round_trip(client):
    from promulga import db as database
    from .conftest import csrf_of, login
    with database.session_scope() as db:
        _reader(db, "app@example.org", role="contributor")
    login(client, "admin@example.org", "admin-password-123")
    page = client.get("/admin/documents/upload")
    assert page.status_code == 200 and "Knowledge" in page.text
    r = client.post("/admin/documents/upload", data={"csrf": csrf_of(page.text), "doc_type": "Knowledge",
                                                     "approvers_pick": ["app@example.org"], "classification": "internal"},
                    files=[("files", ("faq one.md", MD, "text/markdown")), ("files", ("faq-two.txt", b"Hello", "text/plain"))],
                    follow_redirects=False)
    assert r.status_code == 303, r.text[:500]
    with database.session_scope() as db:
        from promulga.models import Document
        docs = db.query(Document).filter_by(doc_type="Knowledge").order_by(Document.number).all()
        assert [(d.title, d.status) for d in docs] == [("Faq one", "Approval"), ("Faq two", "Approval")]
        page = client.get(f"/admin/documents/{docs[0].id}")
        assert "Approval only" in page.text
    # the settings keep the profile of each type
    settings = client.get("/admin/settings")
    assert "Knowledge=KNW=book-open=approval" in settings.text


# -- PROM-69, PROM-71 --------------------------------------------------------------------------------------------

def test_audience_in_plain_words(db):
    admin = _admin(db)
    site = site_settings.get_all(db)
    doc = _publish(db, admin, classification="internal")
    a = workflow.audience(doc, site)
    assert a["label"] == "Public" and a["warning"], "an Internal document on an open portal is in fact public"
    site["portal_access"] = "login"
    assert workflow.audience(doc, site) == {"label": "Internal", "detail": "Anyone signed in to the portal.",
                                            "warning": ""}
    doc.classification = "confidential"
    assert workflow.audience(doc, site)["label"] == "Restricted"


def test_approver_decides_several_documents_at_once(db):
    _reader(db, "app@example.org", role="contributor")
    admin, approver = _admin(db), _user(db, "app@example.org")
    out = workflow.upload_for_approval(db, admin, [("a.pdf", make_pdf("A")), ("b.pdf", make_pdf("B")),
                                                   ("c.pdf", make_pdf("C"))],
                                       doc_type="Knowledge", approver_emails=["app@example.org"])
    a, b, c = [d for d, _ in out]
    with pytest.raises(WorkflowError, match="explain"):
        workflow.decide_many(db, approver, [a], "reject")
    done, errors = workflow.decide_many(db, approver, [a, b], "approve")
    assert done == [a.ref, b.ref] and not errors and a.status == b.status == "Published"
    done, errors = workflow.decide_many(db, approver, [a, c], "reject", "Out of date")
    assert done == [c.ref] and len(errors) == 1 and a.ref in errors[0]
    assert c.status == "Draft" and a.status == "Published"
