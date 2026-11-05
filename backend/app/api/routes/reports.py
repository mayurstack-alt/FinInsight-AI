"""Authenticated report management API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_current_user,
    get_db,
    verify_report_ownership,
    verify_report_ownership_for_deletion,
)
from app.core.rate_limit import (
    limit_by_authenticated_user,
    limit_by_ip,
    limit_sensitive_action_by_authenticated_user,
)
from app.modules.auth.service import AuthenticatedUser
from app.modules.reports import service as report_service
from app.modules.reports.models import Report, ReportStatus
from app.schemas.reports import (
    ReportDownloadResponse,
    ReportListResponse,
    ReportResponse,
    ReportStatusResponse,
)

router = APIRouter(
    tags=["reports"],
    dependencies=[Depends(limit_by_ip), Depends(limit_by_authenticated_user)],
)


@router.post(
    "",
    response_model=ReportResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(limit_sensitive_action_by_authenticated_user)],
)
def create_report(
    file: Annotated[UploadFile, File(...)],
    company_name: Annotated[str, Form(min_length=1, max_length=255)],
    reporting_period: Annotated[str, Form(min_length=1, max_length=32)],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ReportResponse:
    return report_service.create_report(db, user, file, company_name, reporting_period)


@router.get("", response_model=ReportListResponse)
def list_reports(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    status_filter: Annotated[ReportStatus | None, Query(alias="status")] = None,
) -> ReportListResponse:
    reports, total = report_service.list_reports(db, user, limit, offset, status_filter)
    has_more = (offset + len(reports)) < total
    return ReportListResponse(
        items=reports,
        total=total,
        limit=limit,
        offset=offset,
        has_more=has_more,
    )


@router.get("/{report_id}", response_model=ReportResponse)
def get_report(
    report_id: UUID,
    report: Annotated[Report, Depends(verify_report_ownership)],
) -> ReportResponse:
    return report


@router.get("/{report_id}/status", response_model=ReportStatusResponse)
def get_report_status(
    report_id: UUID,
    report: Annotated[Report, Depends(verify_report_ownership)],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> ReportStatusResponse:
    return ReportStatusResponse(id=report.id, status=report.status, updated_at=report.updated_at)


@router.post(
    "/{report_id}/retry",
    response_model=ReportResponse,
    dependencies=[Depends(limit_sensitive_action_by_authenticated_user)],
)
def retry_report(
    report_id: UUID,
    owned_report: Annotated[Report, Depends(verify_report_ownership)],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ReportResponse:
    return report_service.retry_report(db, user, report_id, owned_report=owned_report)


@router.get(
    "/{report_id}/download",
    response_model=ReportDownloadResponse,
    dependencies=[Depends(limit_sensitive_action_by_authenticated_user)],
)
def get_report_download(
    report_id: UUID,
    owned_report: Annotated[Report, Depends(verify_report_ownership)],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ReportDownloadResponse:
    url, expires_in = report_service.get_download_url(
        db,
        user,
        report_id,
        owned_report=owned_report,
    )
    return ReportDownloadResponse(signed_url=url, expires_in_seconds=expires_in)


@router.delete(
    "/{report_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(limit_sensitive_action_by_authenticated_user)],
)
def delete_report(
    report_id: UUID,
    owned_report: Annotated[Report, Depends(verify_report_ownership_for_deletion)],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    report_service.delete_report(db, user, report_id, owned_report=owned_report)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
