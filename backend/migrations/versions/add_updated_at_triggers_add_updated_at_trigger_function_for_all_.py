"""add updated_at trigger function for all tables

Creates a shared PostgreSQL trigger function and attaches a BEFORE UPDATE
trigger to profiles, reports, and processing_jobs. This guarantees that
``updated_at`` is refreshed to ``NOW()`` by the database itself — not only
when SQLAlchemy generates the UPDATE — so direct SQL edits (Supabase Studio,
migration scripts, Celery workers that bypass the ORM) also keep the column
accurate.

Revision ID: add_updated_at_triggers
Revises: 9ac41e2d6f30
Create Date: 2026-11-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "add_updated_at_triggers"
down_revision: str | None = "9ac41e2d6f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables that carry an ``updated_at`` column managed by this trigger.
_TABLES = ("profiles", "reports", "processing_jobs")


def upgrade() -> None:
    # One shared function used by all triggers.
    op.execute("""
        CREATE OR REPLACE FUNCTION public.set_updated_at()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$
    """)

    for table in _TABLES:
        op.execute(f"""
            CREATE TRIGGER trg_{table}_set_updated_at
            BEFORE UPDATE ON public.{table}
            FOR EACH ROW
            EXECUTE FUNCTION public.set_updated_at()
        """)


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_set_updated_at ON public.{table}")

    op.execute("DROP FUNCTION IF EXISTS public.set_updated_at()")
