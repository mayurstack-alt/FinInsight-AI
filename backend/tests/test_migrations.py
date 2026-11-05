from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.database import Base
from app.modules.auth import models as auth_models  # noqa: F401
from app.modules.reports import models as report_models  # noqa: F401

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_alembic_discovers_three_application_tables() -> None:
    assert {"profiles", "reports", "processing_jobs"}.issubset(Base.metadata.tables)
    assert "auth.users" in Base.metadata.tables

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    script = ScriptDirectory.from_config(config)
    revisions = list(script.walk_revisions())
    # Expect: initial → upload/soft-delete → updated_at triggers
    assert len(revisions) == 3
    assert revisions[-1].down_revision is None  # initial migration has no parent


def test_initial_migration_has_expected_tables_foreign_keys_and_rls() -> None:
    versions_dir = BACKEND_DIR / "migrations" / "versions"
    revision_files = list(versions_dir.glob("*.py"))
    assert len(revision_files) == 3
    migration = next(
        path for path in revision_files if path.name.startswith("db932b5c82dd")
    ).read_text(encoding="utf-8")

    for table_name in ("profiles", "reports", "processing_jobs"):
        assert f'op.create_table(\n        "{table_name}"' in migration
    assert "auth.users.id" in migration
    assert 'ondelete="CASCADE"' in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "CREATE TABLE auth.users" not in migration


def test_initial_migration_renders_for_postgresql_without_connecting() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    output = StringIO()

    with redirect_stdout(output):
        command.upgrade(config, "head", sql=True)

    sql = output.getvalue()
    for table_name in ("profiles", "reports", "processing_jobs"):
        assert f"CREATE TABLE {table_name}" in sql
    assert "REFERENCES auth.users (id) ON DELETE CASCADE" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "uploaded" in sql
    assert "CREATE POLICY reports_read_own_live" in sql
    assert "GRANT SELECT ON TABLE public.reports TO authenticated" in sql
    assert "storage_deleted_at" in sql
    assert "CREATE TABLE auth.users" not in sql
    # Trigger migration SQL should be present
    assert "set_updated_at" in sql


def test_report_upload_migration_is_additive_and_has_ownership_policy() -> None:
    migration = (
        BACKEND_DIR / "migrations" / "versions" / "9ac41e2d6f30_report_upload_and_soft_delete.py"
    ).read_text(encoding="utf-8")
    assert 'sa.Column("deleted_at"' in migration
    assert 'sa.Column("storage_deleted_at"' in migration
    assert "uploaded" in migration
    assert "reports_read_own_live" in migration
    assert "auth.uid() = user_id" in migration


def test_trigger_migration_covers_all_three_tables() -> None:
    versions_dir = BACKEND_DIR / "migrations" / "versions"
    trigger_file = next(
        path for path in versions_dir.glob("*.py") if "updated_at_trigger" in path.name
    )
    migration = trigger_file.read_text(encoding="utf-8")
    for table in ("profiles", "reports", "processing_jobs"):
        assert f'"{table}"' in migration or f"'{table}'" in migration
    assert "trg_{table}_set_updated_at" in migration or "set_updated_at" in migration
    assert "set_updated_at" in migration
    assert "BEFORE UPDATE" in migration
    assert "downgrade" in migration
