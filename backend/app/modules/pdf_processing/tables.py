"""Financial-table extraction from detected pages using pdfplumber."""

from __future__ import annotations

import io
import re

import pdfplumber

from app.modules.pdf_processing.models import (
    ExtractionMethod,
    FinancialTable,
    OCRPageResult,
    PageText,
    ProcessingWarning,
    StatementDetection,
    StatementType,
)

_PERIOD_HINT = re.compile(
    r"\b(?:FY\s*)?20\d{2}\b|\b(?:year|period) ended\b|\b(?:as at|as of)\b",
    flags=re.IGNORECASE,
)
_HEADER_HINTS = {"particulars", "description", "account", "accounts", "line item", "financials"}
_NUMBER_HINT = re.compile(r"(?:\(?\s*[-−]?\s*[$₹€£]?\s*\d[\d,]*(?:\.\d+)?\s*\)?%?|[—–-])")


def _clean_rows(raw_table: list[list[str | None]]) -> list[list[str | None]]:
    rows: list[list[str | None]] = []
    for raw_row in raw_table:
        row = [str(cell).replace("\n", " ").strip() if cell is not None else None for cell in raw_row]
        if any(cell for cell in row):
            rows.append(row)
    return rows


def _looks_like_header(row: list[str | None]) -> bool:
    text = " ".join(cell or "" for cell in row)
    lower = text.casefold()
    return bool(_PERIOD_HINT.search(text)) or any(hint in lower for hint in _HEADER_HINTS)


def _quality(rows: list[list[str | None]]) -> bool:
    width = max((len(row) for row in rows), default=0)
    if len(rows) < 2 or width < 2:
        return False
    return any(
        _NUMBER_HINT.search(cell or "") or _PERIOD_HINT.search(cell or "")
        for row in rows
        for cell in row[1:]
    )


def _make_table(
    *,
    ref: str,
    page_number: int,
    statement_type: StatementType,
    raw_rows: list[list[str | None]],
    context: str,
    method: ExtractionMethod,
    confidence: float | None,
    previous: FinancialTable | None,
) -> FinancialTable | None:
    rows = _clean_rows(raw_rows)
    if not _quality(rows):
        return None

    header_index = next((i for i, row in enumerate(rows[:3]) if _looks_like_header(row)), None)
    continued_from = None
    if header_index is not None:
        headers = rows[header_index]
        data_rows = rows[header_index + 1 :]
    elif (
        previous is not None
        and previous.page_number == page_number - 1
        and previous.statement_type is statement_type
        and max(map(len, rows), default=0) == len(previous.headers)
        and any(_PERIOD_HINT.search(cell or "") for cell in previous.headers)
    ):
        headers = previous.headers
        data_rows = rows
        continued_from = previous.table_ref
    else:
        width = max(map(len, rows), default=0)
        headers = ["label"] + [f"column_{index}" for index in range(1, width)]
        data_rows = rows

    if not data_rows:
        return None
    original_row_widths = [len(row) for row in data_rows]
    inconsistent_width = any(row_width != len(headers) for row_width in original_row_widths)
    width = max(len(headers), max(original_row_widths, default=0))
    headers = headers + [f"column_{index}" for index in range(len(headers), width)]
    padded_rows = [row + [None] * (width - len(row)) for row in data_rows]
    warnings: list[str] = []
    if inconsistent_width or len(set(original_row_widths)) > 1:
        warnings.append("inconsistent_column_count")
    return FinancialTable(
        table_ref=ref,
        page_number=page_number,
        statement_type=statement_type,
        rows=padded_rows,
        headers=headers,
        context=context[:3000],
        extraction_method=method,
        confidence=confidence,
        continued_from=continued_from,
        warnings=warnings,
    )


def extract_financial_tables(
    pdf_bytes: bytes,
    pages: list[PageText],
    statements: list[StatementDetection],
    ocr_results: dict[int, OCRPageResult] | None = None,
) -> tuple[list[FinancialTable], list[ProcessingWarning]]:
    """Extract statement tables, retrying with text-based table settings when needed."""
    page_types: dict[int, StatementType] = {}
    for detection in statements:
        for page_number in detection.page_numbers:
            page_types.setdefault(page_number, detection.statement_type)
    page_lookup = {page.page_number: page for page in pages}
    ocr_results = ocr_results or {}
    tables: list[FinancialTable] = []
    warnings: list[ProcessingWarning] = []
    latest_by_statement: dict[StatementType, FinancialTable] = {}

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_number in sorted(page_types):
                if page_number < 1 or page_number > len(pdf.pages):
                    continue
                page = pdf.pages[page_number - 1]
                statement_type = page_types[page_number]
                context = "\n".join(
                    part
                    for part in (
                        page_lookup[page_number].analysis_text if page_number in page_lookup else "",
                        page.extract_text() or "",
                    )
                    if part
                )
                candidates: list[list[list[str | None]]] = []
                try:
                    candidates = page.extract_tables() or []
                    candidates = [rows for rows in candidates if _quality(_clean_rows(rows))]
                except Exception:  # noqa: BLE001 - pdfplumber exposes parser errors from varied PDF layouts.
                    candidates = []
                if not candidates:
                    try:
                        candidates = page.extract_tables(
                            table_settings={
                                "vertical_strategy": "text",
                                "horizontal_strategy": "text",
                                "text_x_tolerance": 3,
                                "text_y_tolerance": 3,
                            }
                        ) or []
                        candidates = [rows for rows in candidates if _quality(_clean_rows(rows))]
                    except Exception:  # noqa: BLE001 - retry with the alternate table strategy.
                        candidates = []

                previous = latest_by_statement.get(statement_type)
                page_tables: list[FinancialTable] = []
                for index, raw_rows in enumerate(candidates, start=1):
                    candidate = _make_table(
                        ref=f"p{page_number}-t{index}",
                        page_number=page_number,
                        statement_type=statement_type,
                        raw_rows=raw_rows,
                        context=context,
                        method=ExtractionMethod.PDF_TEXT,
                        confidence=None,
                        previous=previous if not page_tables else page_tables[-1],
                    )
                    if candidate is not None:
                        page_tables.append(candidate)

                if not page_tables and page_number in ocr_results:
                    ocr = ocr_results[page_number]
                    candidate = _make_table(
                        ref=f"p{page_number}-ocr-t1",
                        page_number=page_number,
                        statement_type=statement_type,
                        raw_rows=ocr.rows,
                        context=context,
                        method=ExtractionMethod.OCR,
                        confidence=ocr.confidence,
                        previous=previous,
                    )
                    if candidate is not None:
                        page_tables.append(candidate)

                for table in page_tables:
                    tables.append(table)
                    latest_by_statement[statement_type] = table
                    if table.warnings:
                        warnings.append(
                            ProcessingWarning(
                                code="inconsistent_table_columns",
                                message="Table rows have inconsistent column counts.",
                                page_number=page_number,
                                table_ref=table.table_ref,
                            )
                        )
                if not page_tables:
                    warnings.append(
                        ProcessingWarning(
                            code="no_financial_table_found",
                            message="No tabular data could be reliably extracted from this statement page.",
                            page_number=page_number,
                        )
                    )
    except Exception:  # noqa: BLE001 - return a safe stage warning for malformed PDFs.
        warnings.append(
            ProcessingWarning(
                code="table_extraction_failed",
                message="Financial tables could not be extracted from the PDF.",
                severity="error",
            )
        )
    return tables, warnings
