"""Users and projects; owner and project on files and rasters.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "name", name="uq_project_owner_name"),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])

    # batch mode: SQLite can't ALTER TABLE ... ADD CONSTRAINT, Alembic rebuilds the table.
    for table in ("files", "rasters"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("owner_id", sa.String(36), nullable=True))
            batch.add_column(sa.Column("project_id", sa.String(36), nullable=True))
            batch.create_foreign_key(f"fk_{table}_owner", "users", ["owner_id"], ["id"], ondelete="CASCADE")
            batch.create_foreign_key(
                f"fk_{table}_project", "projects", ["project_id"], ["id"], ondelete="SET NULL"
            )
            batch.create_index(f"ix_{table}_owner_id", ["owner_id"])
            batch.create_index(f"ix_{table}_project_id", ["project_id"])


def downgrade() -> None:
    for table in ("rasters", "files"):
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{table}_project_id")
            batch.drop_index(f"ix_{table}_owner_id")
            batch.drop_constraint(f"fk_{table}_project", type_="foreignkey")
            batch.drop_constraint(f"fk_{table}_owner", type_="foreignkey")
            batch.drop_column("project_id")
            batch.drop_column("owner_id")
    op.drop_table("projects")
    op.drop_table("users")
