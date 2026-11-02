"""PROM-61: where a document may be deployed (chatbot, website), apart from its classification

Every document starts at "no". The documents the site export selected until now keep being exported:
- export scope "public": the documents tagged EXPORT_TAG with an external or commercial classification
  get website = "public";
- export scope "all" (intranet): every document that is not limited, confidential or audit gets at least
  website = "internal".

PROM-65 (knowledge feed): documents.chatbot_feed (what a chatbot may really use), the channel_log table of its
changes, and api_tokens.audience ("" = catalogue token, as every existing token; "internal" / "public" = feed).

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
        batch_op.add_column(sa.Column('chatbot_feed', sa.String(length=10), nullable=False, server_default='no'))
    with op.batch_alter_table('api_tokens', schema=None) as batch_op:
        batch_op.add_column(sa.Column('audience', sa.String(length=10), nullable=False, server_default=''))
    op.create_table(
        'channel_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=True),
        sa.Column('ref', sa.String(length=30), nullable=False, server_default=''),
        sa.Column('channel', sa.String(length=20), nullable=False, server_default='chatbot'),
        sa.Column('level', sa.String(length=10), nullable=False),
        sa.Column('version', sa.String(length=20), nullable=False, server_default=''),
        sa.Column('at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], name='fk_channel_log_document_id',
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_channel_log_document_id', 'channel_log', ['document_id'], unique=False)
    op.create_index('ix_channel_log_at', 'channel_log', ['at'], unique=False)

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
    op.drop_index('ix_channel_log_at', table_name='channel_log')
    op.drop_index('ix_channel_log_document_id', table_name='channel_log')
    op.drop_table('channel_log')
    with op.batch_alter_table('api_tokens', schema=None) as batch_op:
        batch_op.drop_column('audience')
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_column('chatbot_feed')
        batch_op.drop_column('website')
        batch_op.drop_column('chatbot')
