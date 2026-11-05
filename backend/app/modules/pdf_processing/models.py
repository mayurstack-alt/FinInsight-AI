"""Typed data structures shared across the PDF processing stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any


class StatementType(StrEnum):
    INCOME_STATEMENT = "income_statement"
    BALANCE_SHEET = "balance_sheet"
    CASH_FLOW_STATEMENT = "cash_flow_statement"


class ExtractionMethod(StrEnum):
    PDF_TEXT = "pdf_text"
    OCR = "ocr"


class ValidationStatus(StrEnum):
    VALID = "valid"
    WARNING = "warning"
    INVALID = "invalid"
    MISSING = "missing"


@dataclass
class PageText:
    page_number: int
    text: str
    character_count: int
    has_images: bool
    insufficient_text: bool
    ocr_text: str | None = None
    ocr_confidence: float | None = None
    ocr_attempted: bool = False

    @property
    def analysis_text(self) -> str:
        return "\n".join(part for part in (self.text, self.ocr_text) if part).strip()

    @property
    def extraction_method(self) -> ExtractionMethod:
        if self.ocr_text and self.insufficient_text:
            return ExtractionMethod.OCR
        return ExtractionMethod.PDF_TEXT


@dataclass
class PDFDocument:
    pages: list[PageText]
    metadata: dict[str, str]
    page_count: int


@dataclass
class OCRPageResult:
    page_number: int
    text: str
    lines: list[str]
    rows: list[list[str | None]]
    confidence: float | None


@dataclass
class StatementDetection:
    statement_type: StatementType
    page_numbers: list[int]
    start_page: int
    end_page: int
    confidence: float
    evidence: list[str] = field(default_factory=list)


@dataclass
class FinancialTable:
    table_ref: str
    page_number: int
    statement_type: StatementType
    rows: list[list[str | None]]
    headers: list[str | None]
    context: str = ""
    extraction_method: ExtractionMethod = ExtractionMethod.PDF_TEXT
    confidence: float | None = None
    continued_from: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class FinancialValue:
    statement_type: StatementType
    metric_label: str
    normalized_metric_id: str | None
    value: Decimal | None
    reporting_period: str | None
    period_label: str | None
    currency: str | None
    currency_symbol: str | None
    unit_scale: str | None
    source_page: int
    source_table_ref: str
    extraction_method: ExtractionMethod
    validation_status: ValidationStatus = ValidationStatus.VALID
    confidence: float | None = None
    unit: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement_type": self.statement_type.value,
            "metric_label": self.metric_label,
            "normalized_metric_id": self.normalized_metric_id,
            "value": format(self.value, "f") if self.value is not None else None,
            "reporting_period": self.reporting_period,
            "period_label": self.period_label,
            "currency": self.currency,
            "currency_symbol": self.currency_symbol,
            "unit_scale": self.unit_scale,
            "unit": self.unit,
            "source_page": self.source_page,
            "source_table_ref": self.source_table_ref,
            "extraction_method": self.extraction_method.value,
            "validation_status": self.validation_status.value,
            "confidence": self.confidence,
            "warnings": list(self.warnings),
        }


@dataclass
class ProcessingWarning:
    code: str
    message: str
    severity: str = "warning"
    page_number: int | None = None
    table_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "page_number": self.page_number,
            "table_ref": self.table_ref,
        }


@dataclass
class PDFProcessingResult:
    source_report_id: str | None
    document_metadata: dict[str, str]
    page_texts: list[PageText]
    statements: list[StatementDetection]
    tables: list[FinancialTable]
    values: list[FinancialValue]
    warnings: list[ProcessingWarning] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(warning.severity == "error" for warning in self.warnings):
            return "partial"
        if not self.values:
            return "no_financial_values"
        return "completed"

    def to_dict(self) -> dict[str, Any]:
        """Return safe structured output; raw page text stays in the in-memory object."""
        return {
            "source_report_id": self.source_report_id,
            "status": self.status,
            "document_metadata": dict(self.document_metadata),
            "pages": [
                {
                    "page_number": page.page_number,
                    "character_count": page.character_count,
                    "has_images": page.has_images,
                    "insufficient_text": page.insufficient_text,
                    "ocr_attempted": page.ocr_attempted,
                    "ocr_confidence": page.ocr_confidence,
                }
                for page in self.page_texts
            ],
            "statements": [
                {
                    "statement_type": item.statement_type.value,
                    "page_numbers": list(item.page_numbers),
                    "start_page": item.start_page,
                    "end_page": item.end_page,
                    "confidence": item.confidence,
                    "evidence": list(item.evidence),
                }
                for item in self.statements
            ],
            "values": [value.to_dict() for value in self.values],
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


class PDFProcessingError(Exception):
    """Safe, classified error raised by PDF input or processing stages."""

    def __init__(self, code: str, public_message: str) -> None:
        super().__init__(code)
        self.code = code
        self.public_message = public_message
