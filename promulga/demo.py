"""Demo content: taxonomies, example participants and documents in every stage.

Example users get NO password: their invitation e-mails are in Admin > Outbox,
so the person giving the demo opens those links (in private windows) to play
each role. Nothing is ever sent to the example addresses (example.org is a
reserved domain), but keep SMTP unconfigured on a demo instance anyway.
"""

from __future__ import annotations

import io

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from sqlalchemy.orm import Session

from . import site_settings, workflow
from .accounts import create_user, find
from .models import ROLE_ADMIN, ROLE_CONTRIBUTOR, ROLE_MANAGER, Document, Taxonomy, Term, User

TAXONOMIES = {
    ("Domain", "Governance domain the document belongs to"): {
        "Data governance": ["Data quality", "Metadata & catalogue", "Data ownership"],
        "Information security": ["Access control", "Incident management"],
        "Privacy": ["Personal data", "Retention"],
        "AI governance": [],
    },
    ("Regulation", "Laws and frameworks the document helps comply with"): {
        "GDPR": [], "NIS2": [], "EU AI Act": [], "ISO 27001": [],
    },
    ("Audience", "Who must know the document"): {
        "All staff": [], "Data owners": [], "IT": [], "Management": [],
    },
}

PEOPLE = [
    ("manager@example.org", "Maria Manager", ROLE_MANAGER),
    ("owner@example.org", "Olga Owner", ROLE_CONTRIBUTOR),
    ("reviewer@example.org", "Raf Reviewer", ROLE_CONTRIBUTOR),
    ("approver@example.org", "Anna Approver", ROLE_CONTRIBUTOR),
]

DOCS = [
    # title, type, terms, target status
    ("Data Quality Policy", "Policy", ["Data quality", "GDPR", "Data owners"], "Published"),
    ("Data Ownership Standard", "Standard", ["Data ownership", "All staff"], "Published"),
    ("Access Review Procedure", "Procedure", ["Access control", "ISO 27001", "IT"], "Published"),
    ("Records Retention Policy", "Policy", ["Retention", "GDPR", "All staff"], "Review"),
    ("Responsible AI Guideline", "Guideline", ["AI governance", "EU AI Act", "Management"], "Draft"),
]


def _sample_pdf(title: str) -> bytes:
    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    story = [Paragraph(title, styles["Title"]), Spacer(1, 12)]
    for heading in ["1. Purpose", "2. Scope", "3. Principles", "4. Roles and responsibilities",
                    "5. Compliance and exceptions"]:
        story += [Paragraph(heading, styles["Heading2"]),
                  Paragraph(f"Example text of the section '{heading[3:]}' of the {title.lower()}. "
                            "Replace this demo document by your own content.", styles["BodyText"]),
                  Spacer(1, 8)]
    SimpleDocTemplate(buffer, pagesize=A4, title=title).build(story)
    return buffer.getvalue()


def load_demo(db: Session) -> str:
    if db.query(Document).count():
        return "The database already contains documents: demo data not loaded."
    site = site_settings.get_all(db)
    admin = db.query(User).filter_by(role=ROLE_ADMIN).first()
    if admin is None:
        return "Create the administrator first (ADMIN_EMAIL or 'promulga create-admin')."

    terms: dict[str, Term] = {}
    for sort, ((name, description), tree) in enumerate(TAXONOMIES.items()):
        tax = Taxonomy(name=name, slug=name.lower(), description=description, sort=sort)
        db.add(tax)
        for parent_name, kids in tree.items():
            parent = Term(taxonomy=tax, name=parent_name, slug=workflow_slug(parent_name))
            db.add(parent)
            terms[parent_name] = parent
            for kid in kids:
                terms[kid] = Term(taxonomy=tax, name=kid, slug=workflow_slug(kid), parent=parent)
                db.add(terms[kid])
    db.flush()

    users = {}
    for email, name, role in PEOPLE:
        users[email] = find(db, email) or create_user(db, email, name, role, admin, site)
    manager, owner = users["manager@example.org"], users["owner@example.org"]
    reviewer, approver = users["reviewer@example.org"], users["approver@example.org"]

    for title, doc_type, tag_names, target in DOCS:
        doc = workflow.create_document(
            db, manager, title=title, doc_type=doc_type, summary=f"Demo {doc_type.lower()}: {title}.",
            owner_email=owner.email, reviewer_emails=[reviewer.email], approver_emails=[approver.email],
            term_ids=[terms[t].id for t in tag_names], file=None)
        workflow.check_in(db, owner, doc, f"{title}.pdf", _sample_pdf(title), "First draft")
        if target == "Draft":
            continue
        workflow.submit(db, owner, doc)
        if target == "Review":
            workflow.add_comment(db, reviewer, doc, "Section 3 should mention the retention of backups.")
            continue
        workflow.add_comment(db, reviewer, doc, "Please define 'critical data element' in section 2.")
        workflow.review_done(db, reviewer, doc)
        workflow.check_in(db, owner, doc, f"{title}.pdf", _sample_pdf(title), "Definitions added")
        workflow.submit(db, owner, doc)
        workflow.approve(db, approver, doc, "Approved for publication.")
    return ("Demo data loaded: 3 taxonomies, 4 example users, 5 documents. The example users' invitation links "
            "are in Admin > Outbox.")


def workflow_slug(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
