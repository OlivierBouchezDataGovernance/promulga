"""v0.3: document classification, keywords and mandatory readers

Revision ID: 0004_classification
Revises: 0003_passkeys
Create Date: 2026-10-29 21:30:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0004_classification'
down_revision = '0003_passkeys'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('classification', sa.String(length=20), nullable=False,
                                      server_default='internal'))
        batch_op.add_column(sa.Column('keywords', sa.String(length=500), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('readers', sa.Text(), nullable=False, server_default=''))


def downgrade() -> None:
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_column('readers')
        batch_op.drop_column('keywords')
        batch_op.drop_column('classification')
