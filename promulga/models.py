"""Database tables.

Vocabulary
----------
- *Document*: one governance document (policy, standard, ...) with a stable
  reference such as ``POL-0001``. It lives through cycles; each cycle ends
  with a *Publication* (an immutable, stamped PDF with a version number).
- *Revision*: a working file uploaded during a cycle (check-in). Revisions are
  kept for traceability but only publications are shown on the portal.
- *Assignment*: a reviewer or approver of a document, with the moment they
  acted in the current round (reset when a new round starts).
- *Event*: one line of the audit log. Never updated nor deleted.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Table, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Global roles, from weakest to strongest.
ROLE_VIEWER = "viewer"            # may read the portal when it requires a login
ROLE_CONTRIBUTOR = "contributor"  # may be owner, reviewer or approver of documents
ROLE_MANAGER = "manager"          # Document Manager: creates documents, steers the workflow
ROLE_ADMIN = "admin"              # manager + users, taxonomies, site settings
ROLES = [ROLE_VIEWER, ROLE_CONTRIBUTOR, ROLE_MANAGER, ROLE_ADMIN]

# Workflow stages.
DRAFT, REVIEW, REVISION, APPROVAL, PUBLISHED, RETIRED = (
    "Draft", "Review", "Revision", "Approval", "Published", "Retired")
STATUSES = [DRAFT, REVIEW, REVISION, APPROVAL, PUBLISHED, RETIRED]
ACTIVE_STATUSES = [DRAFT, REVIEW, REVISION, APPROVAL]

REVIEWER, APPROVER = "reviewer", "approver"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(20), default=ROLE_CONTRIBUTOR)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    oidc_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def display(self) -> str:
        return self.name or self.email

    def has_role(self, role: str) -> bool:
        return self.active and ROLES.index(self.role) >= ROLES.index(role)


class Token(Base):
    """Single-use tokens for invitations and password resets (only the hash is stored)."""

    __tablename__ = "tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(20))      # invite | reset
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship()


document_terms = Table(
    "document_terms",
    Base.metadata,
    Column("document_id", ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
    Column("term_id", ForeignKey("terms.id", ondelete="CASCADE"), primary_key=True),
)


class Taxonomy(Base):
    """A navigation hierarchy of the portal, e.g. 'Domain', 'Regulation', 'Process'."""

    __tablename__ = "taxonomies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)

    terms: Mapped[list[Term]] = relationship(back_populates="taxonomy", cascade="all, delete-orphan",
                                             order_by="Term.sort, Term.name")


class Term(Base):
    """A tag inside a taxonomy; terms form a tree through parent_id."""

    __tablename__ = "terms"
    __table_args__ = (UniqueConstraint("taxonomy_id", "slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    taxonomy_id: Mapped[int] = mapped_column(ForeignKey("taxonomies.id", ondelete="CASCADE"))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("terms.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(150))
    slug: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)

    taxonomy: Mapped[Taxonomy] = relationship(back_populates="terms")
    parent: Mapped[Term | None] = relationship(remote_side="Term.id")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    ref: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text, default="")
    doc_type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default=DRAFT, index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    stage_due: Mapped[date | None] = mapped_column(Date, nullable=True)
    stage_started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    current_version: Mapped[str] = mapped_column(String(20), default="")   # last published version
    next_version: Mapped[str] = mapped_column(String(20), default="1.0")   # version being prepared
    published_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    checked_out_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    checked_out_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    owner: Mapped[User] = relationship(foreign_keys=[owner_id])
    checked_out_by: Mapped[User | None] = relationship(foreign_keys=[checked_out_by_id])
    assignments: Mapped[list[Assignment]] = relationship(back_populates="document",
                                                         cascade="all, delete-orphan")
    revisions: Mapped[list[Revision]] = relationship(back_populates="document", cascade="all, delete-orphan",
                                                     order_by="Revision.number.desc()")
    publications: Mapped[list[Publication]] = relationship(back_populates="document",
                                                           cascade="all, delete-orphan",
                                                           order_by="Publication.published_at.desc(), Publication.id.desc()")
    comments: Mapped[list[Comment]] = relationship(back_populates="document", cascade="all, delete-orphan",
                                                   order_by="Comment.created_at")
    terms: Mapped[list[Term]] = relationship(secondary=document_terms, order_by="Term.name")

    def people(self, role: str) -> list[Assignment]:
        return [a for a in self.assignments if a.role == role]

    @property
    def reviewers(self) -> list[Assignment]:
        return self.people(REVIEWER)

    @property
    def approvers(self) -> list[Assignment]:
        return self.people(APPROVER)

    @property
    def latest_revision(self) -> Revision | None:
        return self.revisions[0] if self.revisions else None

    @property
    def latest_publication(self) -> Publication | None:
        return self.publications[0] if self.publications else None

    @property
    def is_overdue(self) -> bool:
        return self.status in ACTIVE_STATUSES and self.stage_due is not None and self.stage_due < date.today()


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (UniqueConstraint("document_id", "user_id", "role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(20))          # reviewer | approver
    acted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    document: Mapped[Document] = relationship(back_populates="assignments")
    user: Mapped[User] = relationship()


class Revision(Base):
    """A working file checked in during a cycle."""

    __tablename__ = "revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(150))
    storage_path: Mapped[str] = mapped_column(String(500))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(Text, default="")
    uploaded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status_at_upload: Mapped[str] = mapped_column(String(20))

    document: Mapped[Document] = relationship(back_populates="revisions")
    uploaded_by: Mapped[User] = relationship()


class Comment(Base):
    """A review remark or a reply, attached to the revision that was current."""

    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    revision_id: Mapped[int | None] = mapped_column(ForeignKey("revisions.id", ondelete="SET NULL"), nullable=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("comments.id", ondelete="CASCADE"), nullable=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(String(20))
    version: Mapped[str] = mapped_column(String(20), default="")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    attachment_name: Mapped[str] = mapped_column(String(255), default="")   # e.g. an annotated copy
    attachment_path: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    document: Mapped[Document] = relationship(back_populates="comments")
    author: Mapped[User] = relationship()
    revision: Mapped[Revision | None] = relationship()


class Publication(Base):
    """An official, immutable version: stamped PDF + metadata."""

    __tablename__ = "publications"
    __table_args__ = (UniqueConstraint("document_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(20))
    published_at: Mapped[date] = mapped_column(Date)
    pdf_path: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64))
    source_revision_id: Mapped[int | None] = mapped_column(ForeignKey("revisions.id", ondelete="SET NULL"),
                                                           nullable=True)
    approved_by: Mapped[str] = mapped_column(Text, default="")   # names, frozen at publication
    title: Mapped[str] = mapped_column(String(300))               # title at publication time
    text: Mapped[str] = mapped_column(Text, default="")           # extracted text, for search
    change_note: Mapped[str] = mapped_column(Text, default="")

    document: Mapped[Document] = relationship(back_populates="publications")


class Event(Base):
    """Audit log (append-only)."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"),
                                                    nullable=True, index=True)
    document_ref: Mapped[str] = mapped_column(String(30), default="")
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_label: Mapped[str] = mapped_column(String(254), default="system")
    action: Mapped[str] = mapped_column(String(100))
    from_status: Mapped[str] = mapped_column(String(20), default="")
    to_status: Mapped[str] = mapped_column(String(20), default="")
    version: Mapped[str] = mapped_column(String(30), default="")
    detail: Mapped[str] = mapped_column(Text, default="")


class Setting(Base):
    """Key/value store for everything configurable from the admin site (JSON values)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class OutboxMail(Base):
    """Every e-mail is written here first, then sent by the background job."""

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    to: Mapped[str] = mapped_column(String(254), index=True)
    subject: Mapped[str] = mapped_column(String(300))
    html: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")


class ReminderLog(Base):
    """Which reminder was sent to whom for which stage, to respect the configured frequency."""

    __tablename__ = "reminder_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))       # soon | overdue
    stage_started_at: Mapped[datetime] = mapped_column(DateTime)
    sent_on: Mapped[date] = mapped_column(Date)
