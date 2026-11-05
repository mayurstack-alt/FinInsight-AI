"""Shared API dependency exports."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.service import AuthenticatedUser
from app.modules.reports import service as report_service
from app.modules.reports.models import Report


def verify_report_ownership(
    report_id: UUID,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Report:
    """Return an active report only when it belongs to the verified current user."""
    return report_service.get_owned_report(db, user, report_id)


def verify_report_ownership_for_deletion(
    report_id: UUID,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Report:
    """Return an owned report including its tombstone for storage cleanup retries."""
    return report_service.get_owned_report(db, user, report_id, include_deleted=True)

__all__ = [
    "get_current_user",
    "get_db",
    "verify_report_ownership",
    "verify_report_ownership_for_deletion",
]
