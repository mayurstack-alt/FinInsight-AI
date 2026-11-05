"""API schemas for report metadata (never expose internal storage paths)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.reports.models import ReportStatus


class ReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_name: str
    original_filename: str
    reporting_period: str
    file_size: int
    file_type: str
    status: ReportStatus
    created_at: datetime
    updated_at: datetime


class ReportListResponse(BaseModel):
    items: list[ReportResponse]
    total: int
    limit: int
    offset: int
    has_more: bool = False


class ReportStatusResponse(BaseModel):
    id: UUID
    status: ReportStatus
    updated_at: datetime


class ReportDownloadResponse(BaseModel):
    signed_url: str
    expires_in_seconds: int


