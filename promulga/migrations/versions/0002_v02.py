"""v0.2: ids, codes, reviews, delegation, acknowledgments, api, webhooks

Revision ID: 0002_v02
Revises: 0001_baseline
Create Date: 2026-10-29 17:10:54.590954
"""
from alembic import op
import sqlalchemy as sa


revision = '0002_v02'
down_revision = '0001_baseline'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('webhook_outbox',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('url', sa.String(length=500), nullable=False),
    sa.Column('payload', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('api_tokens',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('last_used_at', sa.DateTime(), nullable=True),
    sa.Column('revoked', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('api_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_api_tokens_token_hash'), ['token_hash'], unique=True)

    op.create_table('stage_log',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=False),
    sa.Column('stage', sa.String(length=20), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('due', sa.Date(), nullable=True),
    sa.Column('ended_at', sa.DateTime(), nullable=True),
    sa.Column('owner_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('stage_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_stage_log_document_id'), ['document_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_stage_log_stage'), ['stage'], unique=False)

    op.create_table('acknowledgments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=False),
    sa.Column('publication_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('acknowledged_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['publication_id'], ['publications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('publication_id', 'user_id')
    )
    with op.batch_alter_table('acknowledgments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_acknowledgments_document_id'), ['document_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_acknowledgments_publication_id'), ['publication_id'], unique=False)

    # New columns get a server default so existing rows are valid.
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('number', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('change_note', sa.Text(), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('review_months', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('next_review', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('ack_required', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.alter_column('ref', existing_type=sa.VARCHAR(length=30), type_=sa.String(length=60),
                              existing_nullable=False)

    # Global document number: existing documents are numbered in creation order.
    conn = op.get_bind()
    ids = [row[0] for row in conn.execute(sa.text('SELECT id FROM documents ORDER BY id'))]
    for n, doc_id in enumerate(ids, start=1):
        conn.execute(sa.text('UPDATE documents SET number = :n WHERE id = :id'), {'n': n, 'id': doc_id})

    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.alter_column('number', existing_type=sa.Integer(), nullable=False)
        batch_op.create_unique_constraint('uq_documents_number', ['number'])

    with op.batch_alter_table('publications', schema=None) as batch_op:
        batch_op.add_column(sa.Column('signed', sa.Boolean(), nullable=False, server_default=sa.false()))

    with op.batch_alter_table('terms', schema=None) as batch_op:
        batch_op.add_column(sa.Column('code', sa.String(length=20), nullable=False, server_default=''))

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('must_change_password', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('deputy_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('absent_until', sa.Date(), nullable=True))
        batch_op.create_foreign_key('fk_users_deputy', 'users', ['deputy_id'], ['id'], ondelete='SET NULL')

    # ### end Alembic commands ###


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('fk_users_deputy', type_='foreignkey')
        batch_op.drop_column('absent_until')
        batch_op.drop_column('deputy_id')
        batch_op.drop_column('must_change_password')
    with op.batch_alter_table('terms', schema=None) as batch_op:
        batch_op.drop_column('code')
    with op.batch_alter_table('publications', schema=None) as batch_op:
        batch_op.drop_column('signed')
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_constraint('uq_documents_number', type_='unique')
        for column in ('ack_required', 'next_review', 'review_months', 'change_note', 'number'):
            batch_op.drop_column(column)
    for table in ('acknowledgments', 'stage_log', 'api_tokens', 'webhook_outbox'):
        op.drop_table(table)
