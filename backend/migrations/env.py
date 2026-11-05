"""Alembic environment with application model discovery and env-based URL."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings
from app.database import Base
from app.modules.auth import models as auth_models  # noqa: F401
from app.modules.reports import models as report_models  # noqa: F401

# Import future ORM modules here so their models register on Base.metadata.
target_metadata = Base.metadata
config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))


def include_object(object_, name, type_, reflected, compare_to) -> bool:
    """Keep Supabase-managed Auth tables out of application migrations."""
    return not (type_ == "table" and getattr(object_, "schema", None) == "auth")


if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    if connectable.dialect.name == "sqlite":
        connectable = connectable.execution_options(schema_translate_map={"auth": None})
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
