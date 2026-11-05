"""support report upload status and retryable storage cleanup

Revision ID: 9ac41e2d6f30
Revises: db932b5c82dd
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9ac41e2d6f30"
down_revision: str | None = "db932b5c82dd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "reports", sa.Column("storage_deleted_at", sa.DateTime(timezone=True), nullable=True)
    )

    # SQLAlchemy's non-native Enum constraint is named for the enum type.
    op.drop_constraint("report_status", "reports", type_="check")
    op.create_check_constraint(
        "report_status",
        "reports",
        "status IN ('uploaded', 'pending', 'processing', 'completed', 'failed')",
    )
    op.alter_column(
        "reports", "status", server_default=sa.text("'uploaded'")
    )
    op.create_index(
        "ix_reports_user_active_created_at",
        "reports",
        ["user_id", "created_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # Data API clients may read only their own live report metadata. Writes and
    # all Storage access go through the validated server API using service role.
    op.execute("ALTER TABLE public.reports ENABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON TABLE public.reports FROM anon, authenticated")
    op.execute("GRANT SELECT ON TABLE public.reports TO authenticated")
    op.execute("""
        CREATE POLICY reports_read_own_live
        ON public.reports FOR SELECT TO authenticated
        USING (auth.uid() = user_id AND deleted_at IS NULL)
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS reports_read_own_live ON public.reports")
    op.execute("REVOKE SELECT ON TABLE public.reports FROM authenticated")
    op.drop_index("ix_reports_user_active_created_at", table_name="reports")
    op.drop_constraint("report_status", "reports", type_="check")
    op.create_check_constraint(
        "report_status",
        "reports",
        "status IN ('pending', 'processing', 'completed', 'failed')",
    )
    op.alter_column(
        "reports", "status", server_default=sa.text("'pending'")
    )
    op.drop_column("reports", "storage_deleted_at")
    op.drop_column("reports", "deleted_at")
