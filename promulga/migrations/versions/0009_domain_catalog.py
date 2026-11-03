"""LAB-145, PROM-48, PROM-46: domain of a document, links to the data catalogue, periodic review confirmed

- documents.domain: the data domain of the document. Filled from its tag in the taxonomy "Domain" when it has
  one (the top of the tag tree, e.g. "Data governance" for "Data quality").
- catalog_links: OpenMetadata glossary terms and data products a document is linked to.
- documents.review_confirmed_at / review_confirmed_by_id: the last periodic review confirmed without a new version.

Revision ID: 0009_domain_catalog
Revises: 0008_channels
Create Date: 2026-11-03 18:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0009_domain_catalog'
down_revision = '0008_channels'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('domain', sa.String(length=200), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('review_confirmed_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('review_confirmed_by_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_documents_review_confirmed_by_id', 'users', ['review_confirmed_by_id'],
                                    ['id'], ondelete='SET NULL')
    op.create_table(
        'catalog_links',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('fqn', sa.String(length=500), nullable=False),
        sa.Column('label', sa.String(length=300), nullable=False, server_default=''),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('synced_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('document_id', 'kind', 'fqn'),
    )
    with op.batch_alter_table('catalog_links', schema=None) as batch_op:
        batch_op.create_index('ix_catalog_links_document_id', ['document_id'], unique=False)

    # Domain from the taxonomy "Domain": the top of the tree of the first tag found.
    conn = op.get_bind()
    terms = {row.id: row for row in conn.execute(sa.text(
        "SELECT t.id, t.parent_id, t.name FROM terms t JOIN taxonomies x ON x.id = t.taxonomy_id "
        "WHERE lower(x.slug) = 'domain' OR lower(x.name) = 'domain'"))}
    if not terms:
        return
    for row in conn.execute(sa.text("SELECT document_id, term_id FROM document_terms ORDER BY document_id, term_id")):
        term = terms.get(row.term_id)
        if term is None:
            continue
        seen = set()
        while term.parent_id in terms and term.id not in seen:
            seen.add(term.id)
            term = terms[term.parent_id]
        conn.execute(sa.text("UPDATE documents SET domain = :name WHERE id = :id AND domain = ''"),
                     {"name": term.name[:200], "id": row.document_id})


def downgrade() -> None:
    with op.batch_alter_table('catalog_links', schema=None) as batch_op:
        batch_op.drop_index('ix_catalog_links_document_id')
    op.drop_table('catalog_links')
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_constraint('fk_documents_review_confirmed_by_id', type_='foreignkey')
        batch_op.drop_column('review_confirmed_by_id')
        batch_op.drop_column('review_confirmed_at')
        batch_op.drop_column('domain')
