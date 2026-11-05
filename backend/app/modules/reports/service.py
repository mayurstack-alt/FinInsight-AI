"""Ownership-scoped report metadata and storage orchestration."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import ApplicationError
from app.modules.auth.models import Profile
from app.modules.auth.service import AuthenticatedUser
from app.modules.reports.models import (
    ProcessingJob,
    ProcessingJobStatus,
    Report,
    ReportStatus,
    TaskType,
)
from app.modules.reports.validation import validate_pdf_upload
from app.modules.storage.service import ReportStorage, StorageOperationError, storage

logger = logging.getLogger(__name__)


def _user_uuid(user: AuthenticatedUser) -> UUID:
    # AuthenticatedUser is created only by Supabase token verification.
    try:
        return UUID(user.id)
    except (TypeError, ValueError) as exc:
        raise ApplicationError(401, "invalid_identity", "Authentication required") from exc


def _report_or_404(
    db: Session,
    report_id: UUID,
    user_id: UUID,
    *,
    include_deleted: bool = False,
    owned_report: Report | None = None,
) -> Report:
    if owned_report is not None:
        if (
            owned_report.id != report_id
            or owned_report.user_id != user_id
            or (not include_deleted and owned_report.deleted_at is not None)
        ):
            raise ApplicationError(404, "report_not_found", "Report not found")
        return owned_report
    statement = select(Report).where(Report.id == report_id, Report.user_id == user_id)
    if not include_deleted:
        statement = statement.where(Report.deleted_at.is_(None))
    report = db.scalar(statement)
    if report is None:
        raise ApplicationError(404, "report_not_found", "Report not found")
    return report


def get_owned_report(
    db: Session,
    user: AuthenticatedUser,
    report_id: UUID,
    *,
    include_deleted: bool = False,
) -> Report:
    """Load a report scoped to the verified user, hiding non-owned rows as 404."""
    return _report_or_404(
        db,
        report_id,
        _user_uuid(user),
        include_deleted=include_deleted,
    )


def _storage_error(exc: StorageOperationError) -> ApplicationError:
    if exc.operation in {"configuration", "client_initialization"}:
        return ApplicationError(503, "storage_unavailable", "Report storage is temporarily unavailable")
    if exc.status_code == 404:
        return ApplicationError(404, "report_file_not_found", "The report file is unavailable")
    return ApplicationError(502, "storage_unavailable", "Report storage is temporarily unavailable")


def create_report(
    db: Session,
    user: AuthenticatedUser,
    upload: UploadFile,
    company_name: str,
    reporting_period: str,
    report_storage: ReportStorage | None = None,
) -> Report:
    if report_storage is None:
        report_storage = storage
    validated = validate_pdf_upload(upload, get_settings().max_pdf_size_bytes)
    normalized_company = company_name.strip()
    normalized_period = reporting_period.strip()
    if not normalized_company or len(normalized_company) > 255:
        raise ApplicationError(422, "invalid_company_name", "A valid company name is required")
    if not normalized_period or len(normalized_period) > 32:
        raise ApplicationError(422, "invalid_reporting_period", "A valid reporting period is required")

    user_id = _user_uuid(user)
    report_id = uuid4()
    object_path = f"{user_id}/{report_id}/{validated.filename}"
    uploaded = False
    try:
        report_storage.upload(object_path, upload.file)
        uploaded = True
        if db.get(Profile, user_id) is None:
            db.add(Profile(id=user_id))
        report = Report(
            id=report_id,
            user_id=user_id,
            company_name=normalized_company,
            original_filename=validated.filename,
            reporting_period=normalized_period,
            storage_path=object_path,
            file_size=validated.size,
            file_type="application/pdf",
            status=ReportStatus.UPLOADED,
        )
        db.add(report)
        db.commit()
        db.refresh(report)
        return report
    except StorageOperationError as exc:
        db.rollback()
        try:
            report_storage.delete(object_path)
        except StorageOperationError:
            logger.error("Failed upload cleanup requires reconciliation", extra={"report_id": str(report_id)})
        raise _storage_error(exc) from exc
    except Exception as exc:
        db.rollback()
        if uploaded:
            try:
                report_storage.delete(object_path)
            except StorageOperationError:
                # The generated report ID allows operational reconciliation without logging a path/key.
                logger.error("Orphaned report upload cleanup failed", extra={"report_id": str(report_id)})
        if isinstance(exc, ApplicationError):
            raise
        logger.error("Report metadata persistence failed", extra={"report_id": str(report_id)})
        raise ApplicationError(500, "report_create_failed", "The report could not be saved") from exc


def list_reports(
    db: Session,
    user: AuthenticatedUser,
    limit: int = 20,
    offset: int = 0,
    status_filter: ReportStatus | None = None,
) -> tuple[list[Report], int]:
    user_id = _user_uuid(user)
    filters = [Report.user_id == user_id, Report.deleted_at.is_(None)]
    if status_filter is not None:
        filters.append(Report.status == status_filter)
    total = db.scalar(select(func.count()).select_from(Report).where(*filters)) or 0
    items = list(
        db.scalars(
            select(Report)
            .where(*filters)
            .order_by(Report.created_at.desc(), Report.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )
    return items, total


def get_report(
    db: Session,
    user: AuthenticatedUser,
    report_id: UUID,
    *,
    owned_report: Report | None = None,
) -> Report:
    return _report_or_404(db, report_id, _user_uuid(user), owned_report=owned_report)


def get_report_status(
    db: Session,
    user: AuthenticatedUser,
    report_id: UUID,
    *,
    owned_report: Report | None = None,
) -> Report:
    return get_report(db, user, report_id, owned_report=owned_report)


def update_report_status(
    db: Session,
    report_id: UUID,
    status: ReportStatus,
    *,
    user_id: UUID | None = None,
) -> Report:
    """Transition a report's lifecycle status (used by background workers or admin ops)."""
    statement = select(Report).where(Report.id == report_id)
    if user_id is not None:
        statement = statement.where(Report.user_id == user_id)
    report = db.scalar(statement)
    if report is None:
        raise ApplicationError(404, "report_not_found", "Report not found")
    report.status = status
    try:
        db.commit()
        db.refresh(report)
        return report
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Failed to update report status", extra={"report_id": str(report_id)})
        raise ApplicationError(500, "report_update_failed", "The report status could not be updated") from exc


def retry_report(
    db: Session,
    user: AuthenticatedUser,
    report_id: UUID,
    *,
    owned_report: Report | None = None,
) -> Report:
    """Reset a failed report to pending and record a new queued processing attempt."""
    user_id = _user_uuid(user)
    report = _report_or_404(db, report_id, user_id, owned_report=owned_report)
    if report.status != ReportStatus.FAILED:
        raise ApplicationError(
            409,
            "invalid_report_state",
            f"Only reports in 'failed' status can be retried; current status is '{report.status.value}'",
        )

    latest_job = db.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.report_id == report.id)
        .order_by(ProcessingJob.retry_count.desc())
        .limit(1)
    )

    if latest_job is not None:
        new_job = ProcessingJob(
            report_id=report.id,
            task_group_id=latest_job.task_group_id,
            task_type=latest_job.task_type,
            retry_count=latest_job.retry_count + 1,
            status=ProcessingJobStatus.QUEUED,
        )
    else:
        new_job = ProcessingJob(
            report_id=report.id,
            task_type=TaskType.PDF_EXTRACTION,
            retry_count=0,
            status=ProcessingJobStatus.QUEUED,
        )

    report.status = ReportStatus.PENDING
    db.add(new_job)
    try:
        db.commit()
        db.refresh(report)
        return report
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Failed to commit report retry state", extra={"report_id": str(report.id)})
        raise ApplicationError(500, "retry_failed", "The report retry could not be initialized") from exc


def get_download_url(
    db: Session,
    user: AuthenticatedUser,
    report_id: UUID,
    report_storage: ReportStorage | None = None,
    *,
    owned_report: Report | None = None,
) -> tuple[str, int]:
    if report_storage is None:
        report_storage = storage
    report = get_report(db, user, report_id, owned_report=owned_report)
    try:
        if not report_storage.exists(report.storage_path):
            raise ApplicationError(404, "report_file_not_found", "The report file is unavailable")
        return report_storage.create_signed_download_url(report.storage_path)
    except StorageOperationError as exc:
        raise _storage_error(exc) from exc


def delete_report(
    db: Session,
    user: AuthenticatedUser,
    report_id: UUID,
    report_storage: ReportStorage | None = None,
    *,
    owned_report: Report | None = None,
) -> None:
    if report_storage is None:
        report_storage = storage
    report = _report_or_404(
        db,
        report_id,
        _user_uuid(user),
        include_deleted=True,
        owned_report=owned_report,
    )
    if report.storage_deleted_at is not None:
        return

    if report.deleted_at is None:
        report.deleted_at = datetime.now(UTC)
        try:
            db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            logger.error("Report deletion state could not be saved", extra={"report_id": str(report.id)})
            raise ApplicationError(500, "report_delete_failed", "The report could not be deleted") from exc

    try:
        report_storage.delete(report.storage_path)
    except StorageOperationError as exc:
        # Tombstone remains; subsequent DELETE retries storage cleanup and the report is hidden from reads.
        raise ApplicationError(502, "storage_cleanup_pending", "Report deletion is pending storage cleanup") from exc

    report.storage_deleted_at = datetime.now(UTC)
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Report storage cleanup state could not be saved", extra={"report_id": str(report.id)})
        raise ApplicationError(500, "report_delete_failed", "The report could not be deleted") from exc
