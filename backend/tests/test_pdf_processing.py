import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from shutil import which
from uuid import uuid4

import pymupdf
import pytest

from app.config import get_settings
from app.database import supabase_auth_users
from app.modules.auth.models import Profile
from app.modules.pdf_processing.cli import main as run_pdf_cli
from app.modules.pdf_processing.detection import detect_financial_statements
from app.modules.pdf_processing.loader import load_pdf
from app.modules.pdf_processing.models import (
    ExtractionMethod,
    FinancialTable,
    FinancialValue,
    OCRPageResult,
    PageText,
    PDFProcessingError,
    StatementType,
    ValidationStatus,
)
from app.modules.pdf_processing.normalization import (
    normalize_financial_tables,
    parse_reporting_period,
)
from app.modules.pdf_processing.ocr import _group_ocr_data
from app.modules.pdf_processing.pipeline import process_pdf, process_report
from app.modules.pdf_processing.tables import extract_financial_tables
from app.modules.pdf_processing.validation import validate_financial_values
from app.modules.reports.models import Report, ReportStatus
from tests.pdf_samples import (
    financial_report_pdf,
    mixed_text_and_scan_pdf,
    scanned_report_pdf,
)


def _tesseract_available() -> bool:
    configured = get_settings().tesseract_cmd
    return which("tesseract") is not None or bool(configured and Path(configured).is_file())


class MemoryStorage:
    def __init__(self, pdf: bytes) -> None:
        self.pdf = pdf
        self.paths: list[str] = []

    def download(self, path: str) -> bytes:
        self.paths.append(path)
        return self.pdf


def test_loader_extracts_page_indexed_text_metadata_and_blank_pages() -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((40, 50), "Annual financial statements")
    document.new_page()
    page = document.new_page()
    page.insert_text((40, 50), "Cash flow statement")
    document.set_metadata({"title": "Synthetic annual report", "author": "Test fixture"})
    payload = document.tobytes()
    document.close()

    loaded = load_pdf(payload)
    assert loaded.page_count == 3
    assert loaded.pages[0].page_number == 1
    assert "Annual financial" in loaded.pages[0].text
    assert loaded.pages[1].text == ""
    assert loaded.pages[1].insufficient_text
    assert loaded.metadata["title"] == "Synthetic annual report"
    assert "author" not in loaded.metadata


def test_loader_rejects_corrupt_encrypted_and_oversized_inputs() -> None:
    with pytest.raises(PDFProcessingError, match="pdf_corrupt"):
        load_pdf(b"%PDF-not-a-valid-file")

    encrypted = pymupdf.open()
    encrypted.new_page()
    encrypted_bytes = encrypted.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    encrypted.close()
    with pytest.raises(PDFProcessingError, match="pdf_encrypted"):
        load_pdf(encrypted_bytes)

    with pytest.raises(PDFProcessingError, match="pdf_too_large"):
        load_pdf(b"x" * 32, max_bytes=16)


def test_page_limit_is_enforced() -> None:
    document = pymupdf.open()
    document.new_page()
    document.new_page()
    payload = document.tobytes()
    document.close()
    with pytest.raises(PDFProcessingError, match="pdf_page_limit"):
        load_pdf(payload, max_pages=1)


def test_detection_uses_statement_headings_and_related_context() -> None:
    pages = [
        PageText(1, "Management discussion mentions revenue only.", 36, False, False),
        PageText(2, "Statement of Profit and Loss Revenue Expenses Profit", 43, False, False),
        PageText(3, "Statement of Financial Position Assets Liabilities Equity", 52, False, False),
        PageText(4, "Statement of Cash Flows Operating Activities Investing Activities Financing Activities", 75, False, False),
    ]
    detections = detect_financial_statements(pages)
    assert {item.statement_type for item in detections} == set(StatementType)
    assert all(item.confidence >= 0.8 for item in detections)


def test_native_table_extraction_preserves_pages_and_continuation() -> None:
    payload = financial_report_pdf()
    loaded = load_pdf(payload)
    from app.modules.pdf_processing.detection import detect_financial_statements

    detections = detect_financial_statements(loaded.pages)
    tables, warnings = extract_financial_tables(payload, loaded.pages, detections)
    assert {table.statement_type for table in tables} == set(StatementType)
    income = [table for table in tables if table.statement_type is StatementType.INCOME_STATEMENT]
    assert len(income) == 2
    assert income[0].table_ref == "p1-t1"
    assert income[1].continued_from == income[0].table_ref
    assert income[1].rows[0][0] == "Net income"
    assert not warnings


def test_cleaning_handles_negative_formats_periods_currency_and_missing_values() -> None:
    table = FinancialTable(
        table_ref="p2-t1",
        page_number=2,
        statement_type=StatementType.INCOME_STATEMENT,
        headers=["Particulars", "FY2025", "FY2024"],
        rows=[
            ["Revenue", "1,200", "1,100"],
            ["Operating expenses", "(600)", "—"],
            ["Net profit", "$500", "broken"],
        ],
        context="USD in thousands",
    )
    values, _warnings = normalize_financial_tables([table])
    revenue = next(value for value in values if value.metric_label == "Revenue" and value.reporting_period == "2025")
    expense = next(value for value in values if value.metric_label == "Operating expenses" and value.reporting_period == "2025")
    missing = next(value for value in values if value.metric_label == "Operating expenses" and value.reporting_period == "2024")
    invalid = next(value for value in values if value.metric_label == "Net profit" and value.reporting_period == "2024")
    assert revenue.value == Decimal(1200)
    assert (revenue.currency, revenue.unit_scale) == ("USD", "thousand")
    assert expense.value == Decimal(-600)
    assert expense.validation_status is ValidationStatus.WARNING
    assert missing.value is None and missing.validation_status is ValidationStatus.MISSING
    assert invalid.value is None and invalid.validation_status is ValidationStatus.INVALID
    assert parse_reporting_period("Year ended 31 March 2025") == "2025"
    assert parse_reporting_period("Q1 2025") == "Q1-2025"


def test_currency_symbols_are_not_guessed_when_ambiguous() -> None:
    table = FinancialTable(
        table_ref="p1-t1",
        page_number=1,
        statement_type=StatementType.INCOME_STATEMENT,
        headers=["Particulars", "FY2025"],
        rows=[["Revenue", "$1,000"]],
        context="Amounts shown in $ millions",
    )
    values, _ = normalize_financial_tables([table])
    assert values[0].currency is None
    assert values[0].currency_symbol == "$"
    assert "ambiguous_currency_symbol" in values[0].warnings


def test_normalizes_european_decimal_comma_without_losing_currency_or_scale() -> None:
    table = FinancialTable(
        table_ref="p1-t1",
        page_number=1,
        statement_type=StatementType.INCOME_STATEMENT,
        headers=["Particulars", "FY2025"],
        rows=[["Revenue", "€ 1.234,56"]],
        context="EUR in millions",
    )
    values, _warnings = normalize_financial_tables([table])
    assert values[0].value == Decimal("1234.56")
    assert values[0].currency == "EUR"
    assert values[0].unit_scale == "million"


def test_numeric_units_keep_percent_and_decimal_comma_semantics() -> None:
    table = FinancialTable(
        table_ref="p1-t1",
        page_number=1,
        statement_type=StatementType.INCOME_STATEMENT,
        headers=["Particulars", "FY2025"],
        rows=[["Margin", "12,5%"], ["Ratio", "0,125"]],
        context="EUR",
    )
    values, _warnings = normalize_financial_tables([table])
    assert values[0].value == Decimal("12.5") and values[0].unit == "percent"
    assert values[1].value == Decimal("0.125")
    assert "decimal_comma_format" in values[1].warnings


def test_validation_flags_duplicates_and_balance_sheet_mismatch() -> None:
    def value(metric: str, amount: str, ref: str = "p3-t1") -> FinancialValue:
        return FinancialValue(
            statement_type=StatementType.BALANCE_SHEET,
            metric_label=metric.replace("_", " "),
            normalized_metric_id=metric,
            value=Decimal(amount),
            reporting_period="2025",
            period_label="FY2025",
            currency="USD",
            currency_symbol="$",
            unit_scale="thousand",
            source_page=3,
            source_table_ref=ref,
            extraction_method=ExtractionMethod.PDF_TEXT,
        )

    values = [
        value("total_assets", "1000"),
        value("total_liabilities", "600"),
        value("total_equity", "300"),
        FinancialValue(
            statement_type=StatementType.INCOME_STATEMENT,
            metric_label="Revenue",
            normalized_metric_id="revenue",
            value=Decimal(100),
            reporting_period="2025",
            period_label="FY2025",
            currency="USD",
            currency_symbol="$",
            unit_scale="thousand",
            source_page=1,
            source_table_ref="p1-t1",
            extraction_method=ExtractionMethod.PDF_TEXT,
        ),
        FinancialValue(
            statement_type=StatementType.INCOME_STATEMENT,
            metric_label="Revenue",
            normalized_metric_id="revenue",
            value=Decimal(100),
            reporting_period="2025",
            period_label="FY2025",
            currency="USD",
            currency_symbol="$",
            unit_scale="thousand",
            source_page=2,
            source_table_ref="p2-t1",
            extraction_method=ExtractionMethod.PDF_TEXT,
        ),
    ]
    warnings = validate_financial_values(values, [])
    assert any(item.code == "balance_sheet_equation_mismatch" for item in warnings)
    assert any(item.code == "duplicate_metric_period" for item in warnings)
    assert any("duplicate_metric_period" in item.warnings for item in values)


def test_ocr_fallback_only_runs_for_low_text_image_pages_and_keeps_provenance() -> None:
    payload = mixed_text_and_scan_pdf()
    calls: list[int] = []

    def fake_ocr(_pdf: bytes, page_number: int) -> OCRPageResult:
        calls.append(page_number)
        return OCRPageResult(
            page_number=page_number,
            text="Statement of Profit and Loss Revenue 1,200 Net income 150",
            lines=["Statement of Profit and Loss", "Revenue 1,200", "Net income 150"],
            rows=[
                ["Particulars", "FY2025", "FY2024"],
                ["Revenue", "1,200", "1,100"],
                ["Net income", "150", "125"],
            ],
            confidence=87.0,
        )

    result = process_pdf(payload, ocr_runner=fake_ocr)
    assert calls == [2]
    assert result.page_texts[1].ocr_text.startswith("Statement of Profit")
    assert any(table.extraction_method is ExtractionMethod.OCR for table in result.tables)
    ocr_values = [value for value in result.values if value.extraction_method is ExtractionMethod.OCR]
    assert ocr_values and all(value.confidence == 87.0 for value in ocr_values)
    assert "Annual report narrative" not in str(result.to_dict())


def test_ocr_word_grouping_preserves_columns_and_confidence() -> None:
    data = {
        "text": ["Revenue", "1,200", "1,100"],
        "conf": ["90", "88", "85"],
        "left": [20, 300, 450],
        "top": [10, 10, 10],
        "height": [18, 18, 18],
        "block_num": [1, 1, 1],
        "par_num": [1, 1, 1],
        "line_num": [1, 1, 1],
    }
    lines, rows, confidences = _group_ocr_data(data)
    assert lines == ["Revenue 1,200 1,100"]
    assert rows == [["Revenue", "1,200", "1,100"]]
    assert sum(confidences) / len(confidences) == pytest.approx(87.67, abs=0.01)


def test_ocr_failure_becomes_a_safe_warning_without_leaking_error_text() -> None:
    def fail_ocr(_pdf: bytes, page_number: int) -> OCRPageResult:
        raise PDFProcessingError("ocr_unavailable", "sensitive low-level failure")

    result = process_pdf(mixed_text_and_scan_pdf(), ocr_runner=fail_ocr)
    warning = next(item for item in result.warnings if item.code == "ocr_unavailable")
    assert warning.severity == "error"
    assert warning.message == "Tesseract OCR is not installed or not on PATH."
    assert "sensitive low-level failure" not in str(result.to_dict())


@pytest.mark.skipif(not _tesseract_available(), reason="Tesseract executable is not installed or configured")
def test_real_tesseract_ocr_reads_a_scanned_page() -> None:
    result = process_pdf(scanned_report_pdf())
    assert result.page_texts[0].ocr_attempted
    assert result.page_texts[0].ocr_text


def test_process_report_downloads_only_path_from_active_report_row(db_session) -> None:
    user_id = uuid4()
    db_session.execute(supabase_auth_users.insert().values(id=user_id))
    db_session.add(Profile(id=user_id))
    report = Report(
        user_id=user_id,
        company_name="Synthetic Company",
        original_filename="annual-report.pdf",
        reporting_period="FY2025",
        storage_path=f"{user_id}/server-generated/report.pdf",
        file_size=len(financial_report_pdf()),
        file_type="application/pdf",
        status=ReportStatus.UPLOADED,
    )
    db_session.add(report)
    db_session.commit()
    storage_client = MemoryStorage(financial_report_pdf())

    result = process_report(
        report.id,
        db_session,
        authorized_user_id=user_id,
        report_storage=storage_client,
        ocr_runner=lambda _p, n: OCRPageResult(n, "", [], [], None),
    )
    assert result.source_report_id == str(report.id)
    assert storage_client.paths == [report.storage_path]
    assert any(value.normalized_metric_id == "total_assets" for value in result.values)
    assert db_session.get(Report, report.id).status is ReportStatus.UPLOADED


def test_process_report_rejects_missing_or_soft_deleted_report(db_session) -> None:
    user_id = uuid4()
    with pytest.raises(PDFProcessingError, match="report_not_found"):
        process_report(
            uuid4(),
            db_session,
            authorized_user_id=user_id,
            report_storage=MemoryStorage(b""),
        )

    user_id = uuid4()
    db_session.execute(supabase_auth_users.insert().values(id=user_id))
    db_session.add(Profile(id=user_id))
    deleted_report = Report(
        user_id=user_id,
        company_name="Deleted Company",
        original_filename="annual-report.pdf",
        reporting_period="FY2025",
        storage_path=f"{user_id}/deleted/file.pdf",
        file_size=100,
        file_type="application/pdf",
        status=ReportStatus.UPLOADED,
        deleted_at=datetime.now(UTC),
    )
    db_session.add(deleted_report)
    db_session.commit()
    storage_client = MemoryStorage(b"")
    with pytest.raises(PDFProcessingError, match="report_not_found"):
        process_report(
            deleted_report.id,
            db_session,
            authorized_user_id=user_id,
            report_storage=storage_client,
        )
    assert storage_client.paths == []


def test_process_report_requires_owner_identity(db_session) -> None:
    owner_id = uuid4()
    db_session.execute(supabase_auth_users.insert().values(id=owner_id))
    db_session.add(Profile(id=owner_id))
    report = Report(
        user_id=owner_id,
        company_name="Synthetic Company",
        original_filename="annual-report.pdf",
        reporting_period="FY2025",
        storage_path=f"{owner_id}/report/file.pdf",
        file_size=100,
        file_type="application/pdf",
        status=ReportStatus.UPLOADED,
    )
    db_session.add(report)
    db_session.commit()
    storage_client = MemoryStorage(b"must not be retrieved")

    with pytest.raises(PDFProcessingError, match="report_not_found"):
        process_report(
            report.id,
            db_session,
            authorized_user_id=uuid4(),
            report_storage=storage_client,
        )
    assert storage_client.paths == []


def test_local_cli_prints_structured_json_without_raw_page_text(tmp_path, monkeypatch, capsys) -> None:
    pdf_path = tmp_path / "synthetic-annual-report.pdf"
    pdf_path.write_bytes(financial_report_pdf())
    monkeypatch.setattr(sys, "argv", ["pdf-processing", str(pdf_path)])

    assert run_pdf_cli() == 0
    output = capsys.readouterr().out
    assert '"statement_type": "income_statement"' in output
    assert '"normalized_metric_id": "revenue"' in output
    assert "Statement of Profit and Loss" not in output
