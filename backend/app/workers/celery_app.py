"""Celery application configured for Redis and conservative worker defaults."""

from celery import Celery
from celery.signals import setup_logging
from kombu import Exchange, Queue

from app.core.logging import configure_logging

configure_logging()

from app.config import settings  # noqa: E402


@setup_logging.connect
def _configure_worker_logging(**_kwargs: object) -> None:
    """Keep Celery worker output on the same JSON/redaction pipeline as the API."""
    configure_logging()

celery_app = Celery("fininsight", broker=settings.redis_url, backend=settings.redis_url)

default_exchange = Exchange("fininsight", type="direct")
dlx_exchange = Exchange("fininsight_dlx", type="direct")

celery_app.conf.update(
    task_default_queue="fininsight",
    task_queues=(
        Queue("fininsight", default_exchange, routing_key="fininsight"),
        Queue("fininsight_dlq", dlx_exchange, routing_key="fininsight_dlq"),
    ),
    task_default_exchange="fininsight",
    task_default_routing_key="fininsight",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_ignore_result=True,
    worker_concurrency=1,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_time_limit=1800,
    task_soft_time_limit=1740,
)
celery_app.autodiscover_tasks(["app.workers"])
