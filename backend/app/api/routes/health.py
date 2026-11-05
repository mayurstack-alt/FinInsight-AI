"""Health and readiness probes."""

import logging

from fastapi import APIRouter, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_engine

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Process liveness probe."""
    return {"status": "ok"}


@router.get("/ready")
def readiness(response: Response) -> dict[str, str]:
    """Dependency readiness probe asserting database connectivity."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ready", "database": "connected"}
    except SQLAlchemyError as exc:
        logger.error("Readiness probe database check failed", extra={"error": str(exc)})
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "database": "disconnected"}
