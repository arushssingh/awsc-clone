"""Add subdomain column to instances

Revision ID: 001_add_subdomain
Revises: None
Create Date: 2026-03-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "001_add_subdomain"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use batch mode for SQLite compatibility
    with op.batch_alter_table("instances") as batch_op:
        batch_op.add_column(sa.Column("subdomain", sa.String(), nullable=True))
        batch_op.create_index("ix_instances_subdomain", ["subdomain"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("instances") as batch_op:
        batch_op.drop_index("ix_instances_subdomain")
        batch_op.drop_column("subdomain")
