"""v0.4: parent documents, approval PDF, comment decisions, translations and external references

Revision ID: 0005_hierarchy_annexes
Revises: 0004_classification
Create Date: 2026-10-30 20:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0005_hierarchy_annexes'
down_revision = '0004_classification'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('parent_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('approval_revision_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('approval_pdf_path', sa.String(length=500), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('approval_pdf_sha', sa.String(length=64), nullable=False, server_default=''))
        batch_op.create_index('ix_documents_parent_id', ['parent_id'], unique=False)
        batch_op.create_foreign_key('fk_documents_parent_id', 'documents', ['parent_id'], ['id'],
                                    ondelete='SET NULL')

    with op.batch_alter_table('comments', schema=None) as batch_op:
        batch_op.alter_column('author_id', existing_type=sa.Integer(), nullable=True)
        batch_op.add_column(sa.Column('author_label', sa.String(length=200), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('source', sa.String(length=20), nullable=False, server_default='promulga'))
        batch_op.add_column(sa.Column('location', sa.String(length=300), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('external_id', sa.String(length=100), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('decision', sa.String(length=20), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('decision_note', sa.Text(), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('decided_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('decided_by_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_comments_decided_by_id', 'users', ['decided_by_id'], ['id'],
                                    ondelete='SET NULL')

    op.create_table(
        'translations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('language', sa.String(length=5), nullable=False),
        sa.Column('version', sa.String(length=20), nullable=False),
        sa.Column('method', sa.String(length=20), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('storage_path', sa.String(length=500), nullable=False),
        sa.Column('content_type', sa.String(length=150), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_translations_document_id', 'translations', ['document_id'], unique=False)

    op.create_table(
        'external_references',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('issuer', sa.String(length=200), nullable=False),
        sa.Column('edition', sa.String(length=100), nullable=False),
        sa.Column('url', sa.String(length=1000), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('storage_path', sa.String(length=500), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'document_references',
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('reference_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reference_id'], ['external_references.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('document_id', 'reference_id'),
    )


def downgrade() -> None:
    op.drop_table('document_references')
    op.drop_table('external_references')
    op.drop_index('ix_translations_document_id', table_name='translations')
    op.drop_table('translations')
    with op.batch_alter_table('comments', schema=None) as batch_op:
        batch_op.drop_constraint('fk_comments_decided_by_id', type_='foreignkey')
        for col in ('decided_by_id', 'decided_at', 'decision_note', 'decision', 'external_id', 'location', 'source',
                    'author_label'):
            batch_op.drop_column(col)
        batch_op.alter_column('author_id', existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_constraint('fk_documents_parent_id', type_='foreignkey')
        batch_op.drop_index('ix_documents_parent_id')
        for col in ('approval_pdf_sha', 'approval_pdf_path', 'approval_revision_id', 'parent_id'):
            batch_op.drop_column(col)
