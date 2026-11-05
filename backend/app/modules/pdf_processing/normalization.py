"""Normalize financial table rows without guessing missing units or values."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

import pandas as pd

from app.modules.pdf_processing.models import (
    FinancialTable,
    FinancialValue,
    ProcessingWarning,
    ValidationStatus,
)

_METRIC_ALIASES = {
    "revenue": ("revenue", "total revenue", "net sales", "turnover", "sales revenue"),
    "cost_of_revenue": ("cost of revenue", "cost of sales", "cost of goods sold"),
    "operating_expenses": ("operating expenses", "total operating expenses"),
    "operating_income": ("operating income", "operating profit"),
    "profit_before_tax": ("profit before tax", "income before tax", "earnings before tax"),
    "net_income": ("net income", "net profit", "profit for the year", "profit for the period"),
    "total_assets": ("total assets",),
    "total_liabilities": ("total liabilities",),
    "total_equity": (
        "total equity",
        "total shareholders equity",
        "total stockholders equity",
        "equity attributable to owners",
    ),
    "cash_and_cash_equivalents": (
        "cash and cash equivalents",
        "cash and equivalents",
        "cash equivalents",
    ),
    "net_cash_from_operations": (
        "net cash from operating activities",
        "cash generated from operations",
        "net cash provided by operating activities",
    ),
    "net_cash_from_investing": (
        "net cash from investing activities",
        "net cash used in investing activities",
    ),
    "net_cash_from_financing": (
        "net cash from financing activities",
        "net cash used in financing activities",
    ),
    "ending_cash": ("cash and cash equivalents at end of period", "cash at end of period"),
}
_ALIAS_TO_METRIC = {
    re.sub(r"[^a-z0-9]+", " ", alias.casefold()).strip(): metric
    for metric, aliases in _METRIC_ALIASES.items()
    for alias in aliases
}
_CURRENCY_CODES = ("USD", "INR", "EUR", "GBP", "JPY", "CNY", "CAD", "AUD", "CHF", "SGD")
_PERIOD = re.compile(r"\b(?:FY\s*)?(20\d{2})(?:\s*[-/]\s*(\d{2,4}))?\b", re.IGNORECASE)
_QUARTER = re.compile(r"\b(Q[1-4])\s*[- ]?\s*(20\d{2})\b", re.IGNORECASE)
_VALUE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")
_GROUPED_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+")
_INDIAN_GROUPED_NUMBER = re.compile(r"\d{1,3}(?:,\d{2})+,\d{3}")


def normalize_metric_label(label: str) -> str | None:
    normalized = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized.casefold()).strip()
    return _ALIAS_TO_METRIC.get(normalized)


def parse_reporting_period(label: str | None) -> str | None:
    if not label:
        return None
    quarter = _QUARTER.search(label)
    if quarter:
        return f"{quarter.group(1).upper()}-{quarter.group(2)}"
    match = _PERIOD.search(label)
    if not match:
        return None
    start, end = match.groups()
    if end:
        end_year = f"20{end}" if len(end) == 2 else end
        return f"FY{start}-{end_year}"
    return start


def _currency_and_symbol(context: str) -> tuple[str | None, str | None, list[str]]:
    upper = context.upper()
    for code in _CURRENCY_CODES:
        if re.search(rf"\b{code}\b", upper):
            return code, {"USD": "$", "INR": "₹", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥"}.get(code), []
    if re.search(r"US\$|US DOLLARS?", upper):
        return "USD", "$", []
    if re.search(r"\b(?:INDIAN RUPEES?|RUPEES?)\b|\bRS\.?\s", upper):
        return "INR", "₹", []
    if re.search(r"\bEUROS?\b", upper):
        return "EUR", "€", []
    if re.search(r"\bPOUNDS? STERLING\b", upper):
        return "GBP", "£", []
    symbols = [symbol for symbol in ("₹", "€", "£", "¥", "$", "₩") if symbol in context]
    if symbols:
        symbol = symbols[0]
        if symbol == "₹":
            return "INR", symbol, []
        if symbol == "€":
            return "EUR", symbol, []
        if symbol == "£":
            return "GBP", symbol, []
        return None, symbol, ["ambiguous_currency_symbol"]
    return None, None, ["currency_not_identified"]


def _unit_scale(context: str) -> str | None:
    text = context.casefold()
    if re.search(r"\b(?:in\s+)?crores?\b", text):
        return "crore"
    if re.search(r"\b(?:in\s+)?lakhs?\b", text):
        return "lakh"
    if re.search(r"\b(?:in\s+)?billions?\b", text):
        return "billion"
    if re.search(r"\b(?:in\s+)?millions?\b", text):
        return "million"
    if re.search(r"\b(?:in\s+)?thousands?\b", text):
        return "thousand"
    if re.search(r"\b(?:in\s+)?hundreds?\b", text):
        return "hundred"
    return None


def _parse_number(raw: str | None) -> tuple[Decimal | None, ValidationStatus, list[str]]:
    if raw is None or not raw.strip() or raw.strip().casefold() in {"-", "—", "–", "n/a", "na", "nil"}:
        return None, ValidationStatus.MISSING, ["missing_value"]
    text = raw.strip().replace("−", "-").replace("–", "-").replace("—", "-")
    negative_parentheses = text.startswith("(") and text.endswith(")")
    if negative_parentheses:
        text = text[1:-1].strip()
    text = re.sub(r"\b(?:USD|INR|EUR|GBP|JPY|CNY|CAD|AUD|CHF|SGD)\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[$₹€£¥₩%\s]", "", text)
    warnings: list[str] = []
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        if text.count(",") == 1 and re.fullmatch(r"0,\d{3}", text):
            text = text.replace(",", ".")
            warnings.append("decimal_comma_format")
        elif _GROUPED_NUMBER.fullmatch(text) or _INDIAN_GROUPED_NUMBER.fullmatch(text):
            text = text.replace(",", "")
        elif text.count(",") == 1 and re.fullmatch(r"\d+,\d{1,2}", text):
            text = text.replace(",", ".")
            warnings.append("decimal_comma_format")
        else:
            return None, ValidationStatus.INVALID, ["ambiguous_numeric_separator"]
    match = _VALUE.fullmatch(text)
    if not match:
        return None, ValidationStatus.INVALID, ["invalid_numeric_value"]
    try:
        value = Decimal(match.group())
    except InvalidOperation:
        return None, ValidationStatus.INVALID, ["invalid_numeric_value"]
    if negative_parentheses:
        value = -abs(value)
    if value < 0:
        warnings.append("negative_value_review")
    return value, ValidationStatus.WARNING if warnings else ValidationStatus.VALID, warnings


def normalize_financial_tables(
    tables: list[FinancialTable],
) -> tuple[list[FinancialValue], list[ProcessingWarning]]:
    values: list[FinancialValue] = []
    warnings: list[ProcessingWarning] = []
    for table in tables:
        context = " ".join([table.context, *[header or "" for header in table.headers]])
        currency, currency_symbol, currency_warnings = _currency_and_symbol(context)
        scale = _unit_scale(context)
        try:
            frame = pd.DataFrame(table.rows, columns=table.headers)
        except (ValueError, TypeError):
            warnings.append(
                ProcessingWarning(
                    code="table_shape_invalid",
                    message="The table rows do not match the detected columns.",
                    page_number=table.page_number,
                    table_ref=table.table_ref,
                )
            )
            continue

        for row_index, row in frame.iterrows():
            cells = [None if pd.isna(value) else str(value).strip() for value in row.tolist()]
            if not cells or not cells[0] or re.search(r"\b(?:particulars|description|notes?)\b", cells[0], re.IGNORECASE):
                continue
            label = cells[0]
            metric_id = normalize_metric_label(label)
            if metric_id is None:
                warnings.append(
                    ProcessingWarning(
                        code="unmapped_financial_label",
                        message="A financial row label could not be mapped confidently.",
                        page_number=table.page_number,
                        table_ref=table.table_ref,
                    )
                )
            for column_index, raw_value in enumerate(cells[1:], start=1):
                period_label = table.headers[column_index] if column_index < len(table.headers) else None
                period = parse_reporting_period(period_label)
                if raw_value is None and period is None:
                    continue
                parsed, validation_status, value_warnings = _parse_number(raw_value)
                value_warnings.extend(currency_warnings)
                if not period:
                    value_warnings.append("reporting_period_not_identified")
                    if validation_status is ValidationStatus.VALID:
                        validation_status = ValidationStatus.WARNING
                if scale is None:
                    value_warnings.append("unit_scale_not_identified")
                    if validation_status is ValidationStatus.VALID:
                        validation_status = ValidationStatus.WARNING
                if metric_id is None:
                    value_warnings.append("metric_not_mapped")
                    if validation_status is ValidationStatus.VALID:
                        validation_status = ValidationStatus.WARNING
                if table.extraction_method.value == "ocr":
                    if table.confidence is None or table.confidence < 60:
                        value_warnings.append("low_ocr_confidence")
                    if validation_status is ValidationStatus.VALID:
                        validation_status = ValidationStatus.WARNING
                values.append(
                    FinancialValue(
                        statement_type=table.statement_type,
                        metric_label=label,
                        normalized_metric_id=metric_id,
                        value=parsed,
                        reporting_period=period,
                        period_label=period_label,
                        currency=currency,
                        currency_symbol=currency_symbol,
                        unit_scale=scale,
                        source_page=table.page_number,
                        source_table_ref=table.table_ref,
                        extraction_method=table.extraction_method,
                        validation_status=validation_status,
                        confidence=table.confidence,
                        unit="percent" if raw_value and raw_value.strip().endswith("%") else None,
                        warnings=sorted(set(value_warnings)),
                    )
                )
    return values, warnings
