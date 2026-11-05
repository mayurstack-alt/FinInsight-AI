"""Composable PDF-processing orchestration for tests and future Celery tasks."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.pdf_processing.detection import detect_financial_statements
from app.modules.pdf_processing.loader import _read_source, load_pdf
from app.modules.pdf_processing.models import (
    OCRPageResult,
    PDFProcessingError,
    PDFProcessingResult,
    ProcessingWarning,
)
from app.modules.pdf_processing.normalization import normalize_financial_tables
from app.modules.pdf_processing.ocr import run_ocr
from app.modules.pdf_processing.tables import extract_financial_tables
from app.modules.pdf_processing.validation import validate_financial_values
from app.modules.reports.models import Report
from app.modules.storage.service import ReportStorage, StorageOperationError, storage

logger = logging.getLogger(__name__)
PDFInput = bytes | bytearray | memoryview | str | Path | BinaryIO
OCRRunner = Callable[[bytes, int], OCRPageResult]
_OCR_SAFE_MESSAGES = {
    "ocr_page_invalid": "OCR page selection was invalid.",
    "ocr_page_too_large": "The page is too large for safe OCR rendering.",
    "ocr_unavailable": "Tesseract OCR is not installed or not on PATH.",
    "ocr_failed": "OCR could not process this page.",
}


def process_pdf(
    source: PDFInput,
    *,
    source_report_id: str | None = None,
    ocr_runner: OCRRunner = run_ocr,
) -> PDFProcessingResult:
    """Run the complete extraction pipeline without HTTP, Celery, or DB writes."""
    pdf_bytes = _read_source(source)
    document = load_pdf(pdf_bytes)
    warnings: list[ProcessingWarning] = []
    ocr_results: dict[int, OCRPageResult] = {}

    # Only pages with insufficient selectable text and embedded images use OCR.
    for page in document.pages:
        if not page.insufficient_text:
            continue
        if not page.has_images:
            warnings.append(
                ProcessingWarning(
                    code="insufficient_text_without_image",
                    message="The page has too little selectable text and no embedded image for OCR.",
                    page_number=page.page_number,
                )
            )
            continue
        page.ocr_attempted = True
        try:
            ocr_result = ocr_runner(pdf_bytes, page.page_number)
            ocr_results[page.page_number] = ocr_result
            page.ocr_text = ocr_result.text
            page.ocr_confidence = ocr_result.confidence
            if not ocr_result.text:
                warnings.append(
                    ProcessingWarning(
                        code="ocr_no_text",
                        message="OCR did not find readable text on this scanned page.",
                        page_number=page.page_number,
                    )
                )
            if ocr_result.confidence is not None and ocr_result.confidence < 45:
                warnings.append(
                    ProcessingWarning(
                        code="low_ocr_confidence",
                        message="OCR confidence is low; extracted values require manual review.",
                        page_number=page.page_number,
                    )
                )
        except PDFProcessingError as exc:
            warnings.append(
                ProcessingWarning(
                    code=exc.code,
                    message=_OCR_SAFE_MESSAGES.get(exc.code, "OCR could not process this page."),
                    severity="error",
                    page_number=page.page_number,
                )
            )
        except Exception:  # noqa: BLE001 - isolate per-page OCR failures from the whole document.
            # Avoid putting provider errors or PDF-derived text in logs/responses.
            logger.warning("OCR fallback failed", extra={"page_number": page.page_number})
            warnings.append(
                ProcessingWarning(
                    code="ocr_failed",
                    message="OCR could not process this page.",
                    severity="error",
                    page_number=page.page_number,
                )
            )

    statements = detect_financial_statements(document.pages)
    if not statements:
        warnings.append(
            ProcessingWarning(
                code="no_financial_statements_detected",
                message="No financial statement pages were identified with sufficient evidence.",
            )
        )

    tables, table_warnings = extract_financial_tables(
        pdf_bytes,
        document.pages,
        statements,
        ocr_results,
    )
    warnings.extend(table_warnings)
    values, normalization_warnings = normalize_financial_tables(tables)
    warnings.extend(normalization_warnings)
    warnings.extend(validate_financial_values(values, tables))
    return PDFProcessingResult(
        source_report_id=source_report_id,
        document_metadata=document.metadata,
        page_texts=document.pages,
        statements=statements,
        tables=tables,
        values=values,
        warnings=warnings,
    )


def process_report(
    report_id: UUID | str,
    db: Session,
    *,
    authorized_user_id: UUID | str,
    report_storage: ReportStorage = storage,
    ocr_runner: OCRRunner = run_ocr,
) -> PDFProcessingResult:
    """Load a live report's stored PDF and process it for a trusted internal caller.

    This function is intentionally not mounted as an HTTP endpoint. A future worker
    should receive a report ID from an authorized API request, then call this function.
    Storage paths are read only from the active database row, never from caller input.
    ``authorized_user_id`` must come from the verified API identity; a future task
    should receive it only from that trusted enqueue path, not from client payloads.
    """
    try:
        canonical_id = UUID(str(report_id))
        canonical_user_id = UUID(str(authorized_user_id))
    except (ValueError, TypeError) as exc:
        raise PDFProcessingError("report_not_found", "Report not found") from exc
    try:
        report = db.scalar(
            select(Report).where(
                Report.id == canonical_id,
                Report.user_id == canonical_user_id,
                Report.deleted_at.is_(None),
            )
        )
    except SQLAlchemyError as exc:
        raise PDFProcessingError("report_lookup_failed", "Report metadata could not be loaded") from exc
    if report is None:
        raise PDFProcessingError("report_not_found", "Report not found")
    if report.file_type != "application/pdf":
        raise PDFProcessingError("report_not_pdf", "Stored report is not a supported PDF")
    if report.file_size > get_settings().max_pdf_size_bytes:
        raise PDFProcessingError("pdf_too_large", "PDF exceeds the configured size limit")
    try:
        pdf_bytes = report_storage.download(report.storage_path)
    except StorageOperationError as exc:
        logger.error(
            "Stored report could not be retrieved for processing",
            extra={"report_id": str(canonical_id), "operation": exc.operation},
        )
        raise PDFProcessingError(
            "report_pdf_unavailable", "The stored report file could not be retrieved"
        ) from exc
    return process_pdf(pdf_bytes, source_report_id=str(canonical_id), ocr_runner=ocr_runner)
