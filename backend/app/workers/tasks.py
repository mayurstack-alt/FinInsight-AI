"""Task registration placeholder; report processing is not implemented yet."""

from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.scaffold_healthcheck")
def scaffold_healthcheck() -> str:
    """Minimal task for confirming worker registration during setup."""
    return "worker-ok"
