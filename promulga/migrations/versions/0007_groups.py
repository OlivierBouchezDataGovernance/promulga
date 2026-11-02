"""PROM-70: user groups (local or from the SSO) as readers, reviewers and approvers

Revision ID: 0007_groups
Revises: 0005_hierarchy_annexes
Create Date: 2026-11-02 18:00:00

If the Chatbot and Website channels (PROM-61, migration 0006) are merged first, set down_revision to its id.
"""
from alembic import op
import sqlalchemy as sa


revision = '0007_groups'
down_revision = '0005_hierarchy_annexes'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'groups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=50), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('source', sa.String(length=10), nullable=False, server_default='local'),
        sa.Column('any_member', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_table(
        'group_members',
        sa.Column('group_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('group_id', 'user_id'),
    )
    with op.batch_alter_table('assignments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('group_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_assignments_group_id', 'groups', ['group_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    with op.batch_alter_table('assignments', schema=None) as batch_op:
        batch_op.drop_constraint('fk_assignments_group_id', type_='foreignkey')
        batch_op.drop_column('group_id')
    op.drop_table('group_members')
    op.drop_table('groups')
