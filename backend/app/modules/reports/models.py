"""Annual report metadata and per-attempt background processing records."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.modules.auth.models import Profile


class ReportStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ProcessingJobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TaskType(str, enum.Enum):
    """Approved Celery task types for the PDF → analysis → RAG pipeline.

    Adding a new task type here (and creating the matching Alembic migration
    to extend the CHECK constraint) is the only way to introduce a new stage.
    This prevents ad-hoc strings from accumulating in ``processing_jobs``.
    """

    PDF_EXTRACTION = "pdf_extraction"
    FINANCIAL_ANALYSIS = "financial_analysis"
    RAG_INGESTION = "rag_ingestion"


def _enum_values(enum_class: type[enum.Enum]) -> list[str]:
    return [item.value for item in enum_class]


class Report(Base):
    """Metadata for a user's annual report; PDF bytes remain in private storage."""

    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint(
            "length(trim(company_name)) BETWEEN 1 AND 255",
            name="company_name_valid",
        ),
        CheckConstraint(
            "length(trim(original_filename)) BETWEEN 1 AND 255",
            name="original_filename_valid",
        ),
        CheckConstraint(
            "length(trim(reporting_period)) BETWEEN 1 AND 32",
            name="reporting_period_valid",
        ),
        CheckConstraint(
            "length(trim(storage_path)) BETWEEN 1 AND 1024",
            name="storage_path_valid",
        ),
        CheckConstraint("file_size > 0", name="file_size_positive"),
        CheckConstraint("file_type = 'application/pdf'", name="file_type_pdf"),
        UniqueConstraint("storage_path", name="uq_reports_storage_path"),
        Index("ix_reports_user_created_at", "user_id", "created_at"),
        Index(
            "ix_reports_user_active_created_at",
            "user_id",
            "created_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_reports_user_status", "user_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    reporting_period: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="application/pdf",
        server_default="application/pdf",
    )
    status: Mapped[ReportStatus] = mapped_column(
        Enum(
            ReportStatus,
            name="report_status",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=ReportStatus.UPLOADED,
        server_default=ReportStatus.UPLOADED.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    storage_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    owner: Mapped[Profile] = relationship(back_populates="reports")
    processing_jobs: Mapped[list[ProcessingJob]] = relationship(
        back_populates="report",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ProcessingJob(Base):
    """One execution attempt; retries share a task_group_id and increment retry_count."""

    __tablename__ = "processing_jobs"
    __table_args__ = (
        # task_type is constrained by the TaskType Enum column definition above.
        CheckConstraint("retry_count >= 0", name="retry_count_nonnegative"),
        CheckConstraint(
            "completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at",
            name="completion_after_start",
        ),
        CheckConstraint(
            "error_message IS NULL OR length(error_message) <= 4000",
            name="error_message_length",
        ),
        UniqueConstraint(
            "task_group_id", "retry_count", name="uq_processing_jobs_group_attempt"
        ),
        Index("ix_processing_jobs_report_status", "report_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_group_id: Mapped[UUID] = mapped_column(
        default=uuid4,
        nullable=False,
    )
    task_type: Mapped[TaskType] = mapped_column(
        Enum(
            TaskType,
            name="task_type",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    status: Mapped[ProcessingJobStatus] = mapped_column(
        Enum(
            ProcessingJobStatus,
            name="processing_job_status",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=ProcessingJobStatus.QUEUED,
        server_default=ProcessingJobStatus.QUEUED.value,
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    report: Mapped[Report] = relationship(back_populates="processing_jobs")
