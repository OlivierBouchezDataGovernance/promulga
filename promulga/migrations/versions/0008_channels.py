"""PROM-61: where a document may be deployed (chatbot, website), apart from its classification

Every document starts at "no". The documents the site export selected until now keep being exported:
- export scope "public": the documents tagged EXPORT_TAG with an external or commercial classification
  get website = "public";
- export scope "all" (intranet): every document that is not limited, confidential or audit gets at least
  website = "internal".

Revision ID: 0008_channels
Revises: 0007_groups
Create Date: 2026-11-02 10:00:00
"""
import json
import os

from alembic import op
import sqlalchemy as sa


revision = '0008_channels'
down_revision = '0007_groups'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('chatbot', sa.String(length=10), nullable=False, server_default='no'))
        batch_op.add_column(sa.Column('website', sa.String(length=10), nullable=False, server_default='no'))

    conn = op.get_bind()
    row = conn.execute(sa.text("SELECT value FROM settings WHERE key = 'export_scope'")).first()
    try:
        scope = json.loads(row[0]) if row else "public"
    except ValueError:
        scope = "public"
    if scope == "all":
        conn.execute(sa.text("UPDATE documents SET website = 'internal' "
                             "WHERE classification NOT IN ('limited', 'confidential', 'audit')"))
    tag = (os.environ.get("EXPORT_TAG") or "public").strip().lower()
    conn.execute(sa.text(
        "UPDATE documents SET website = 'public' WHERE classification IN ('external', 'commercial') "
        "AND id IN (SELECT dt.document_id FROM document_terms dt JOIN terms t ON t.id = dt.term_id "
        "WHERE lower(t.slug) = :tag OR lower(t.name) = :tag)"), {"tag": tag})


def downgrade() -> None:
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_column('website')
        batch_op.drop_column('chatbot')
