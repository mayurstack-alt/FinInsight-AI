"""Financial-value validation that reports uncertainty instead of correcting it."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from app.modules.pdf_processing.models import (
    FinancialTable,
    FinancialValue,
    ProcessingWarning,
    StatementType,
    ValidationStatus,
)


def _warn_value(value: FinancialValue, code: str) -> None:
    if code not in value.warnings:
        value.warnings.append(code)
    if value.validation_status in {ValidationStatus.VALID, ValidationStatus.MISSING}:
        value.validation_status = ValidationStatus.WARNING


def validate_financial_values(
    values: list[FinancialValue], tables: list[FinancialTable]
) -> list[ProcessingWarning]:
    warnings: list[ProcessingWarning] = []
    for table in tables:
        if table.warnings:
            warnings.append(
                ProcessingWarning(
                    code="inconsistent_statement_columns",
                    message="One or more rows do not align with the detected statement columns.",
                    page_number=table.page_number,
                    table_ref=table.table_ref,
                )
            )

    by_metric_period: dict[tuple[object, ...], list[FinancialValue]] = defaultdict(list)
    for value in values:
        metric_key = value.normalized_metric_id or " ".join(value.metric_label.casefold().split())
        key = (value.statement_type, metric_key, value.reporting_period, value.currency, value.unit_scale)
        by_metric_period[key].append(value)
        if value.validation_status is ValidationStatus.INVALID:
            warnings.append(
                ProcessingWarning(
                    code="invalid_numeric_value",
                    message="A table cell did not contain a valid financial number.",
                    page_number=value.source_page,
                    table_ref=value.source_table_ref,
                    severity="error",
                )
            )
        elif value.validation_status is ValidationStatus.MISSING:
            warnings.append(
                ProcessingWarning(
                    code="missing_financial_value",
                    message="A financial table cell is blank or marked as unavailable.",
                    page_number=value.source_page,
                    table_ref=value.source_table_ref,
                )
            )
        for code in value.warnings:
            if code == "negative_value_review":
                warnings.append(
                    ProcessingWarning(
                        code=code,
                        message="A negative amount was retained and should be reviewed in its statement context.",
                        page_number=value.source_page,
                        table_ref=value.source_table_ref,
                    )
                )

    for same_key in by_metric_period.values():
        if len(same_key) > 1:
            for value in same_key:
                _warn_value(value, "duplicate_metric_period")
            first = same_key[0]
            warnings.append(
                ProcessingWarning(
                    code="duplicate_metric_period",
                    message="More than one value was extracted for the same metric and period; none was selected automatically.",
                    page_number=first.source_page,
                    table_ref=first.source_table_ref,
                )
            )

    # Check the accounting equation only where all operands have matching period,
    # currency, and scale; never compare numbers with unknown or mixed units.
    balance_groups: dict[tuple[str | None, str | None, str | None], dict[str, list[FinancialValue]]] = defaultdict(
        lambda: defaultdict(list)
    )
    required = {"total_assets", "total_liabilities", "total_equity"}
    for value in values:
        if value.statement_type is StatementType.BALANCE_SHEET and value.normalized_metric_id in required:
            group_key = (value.reporting_period, value.currency, value.unit_scale)
            balance_groups[group_key][value.normalized_metric_id].append(value)

    for (period, _currency, _scale), metrics in balance_groups.items():
        if any(len(metrics.get(metric, [])) != 1 for metric in required):
            continue
        assets = metrics["total_assets"][0]
        liabilities = metrics["total_liabilities"][0]
        equity = metrics["total_equity"][0]
        if len({assets.source_table_ref, liabilities.source_table_ref, equity.source_table_ref}) != 1:
            continue
        if any(item.value is None for item in (assets, liabilities, equity)):
            continue
        if assets.value is None or liabilities.value is None or equity.value is None:
            continue
        difference = abs(assets.value - liabilities.value - equity.value)
        tolerance = max(Decimal("0.01"), abs(assets.value) * Decimal("0.005"))
        if difference > tolerance:
            for value in (assets, liabilities, equity):
                _warn_value(value, "balance_sheet_equation_mismatch")
            warnings.append(
                ProcessingWarning(
                    code="balance_sheet_equation_mismatch",
                    message=f"Balance Sheet totals do not reconcile for reporting period {period or 'unknown'}.",
                    page_number=assets.source_page,
                    table_ref=assets.source_table_ref,
                )
            )

    # Closing cash in the Cash Flow statement should broadly match period-end
    # cash on the Balance Sheet when labels and units were confidently aligned.
    cash_groups: dict[tuple[str | None, str | None, str | None], dict[str, list[FinancialValue]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for value in values:
        if value.normalized_metric_id == "cash_and_cash_equivalents" and value.statement_type is StatementType.BALANCE_SHEET:
            metric = "balance_sheet_cash"
        elif value.normalized_metric_id == "ending_cash" and value.statement_type is StatementType.CASH_FLOW_STATEMENT:
            metric = "cash_flow_ending_cash"
        else:
            continue
        cash_groups[(value.reporting_period, value.currency, value.unit_scale)][metric].append(value)

    for (period, _currency, _scale), metrics in cash_groups.items():
        if len(metrics.get("balance_sheet_cash", [])) != 1 or len(metrics.get("cash_flow_ending_cash", [])) != 1:
            continue
        balance_cash = metrics["balance_sheet_cash"][0]
        cash_flow = metrics["cash_flow_ending_cash"][0]
        if (
            balance_cash.value is None
            or cash_flow.value is None
            or balance_cash.currency != cash_flow.currency
            or balance_cash.unit_scale != cash_flow.unit_scale
        ):
            continue
        tolerance = max(Decimal("0.01"), abs(balance_cash.value) * Decimal("0.005"))
        if abs(balance_cash.value - cash_flow.value) > tolerance:
            _warn_value(balance_cash, "cross_statement_cash_mismatch")
            _warn_value(cash_flow, "cross_statement_cash_mismatch")
            warnings.append(
                ProcessingWarning(
                    code="cross_statement_cash_mismatch",
                    message=f"Closing cash values differ across statements for reporting period {period or 'unknown'}.",
                    page_number=balance_cash.source_page,
                    table_ref=balance_cash.source_table_ref,
                )
            )

    # Report when one detected statement has multiple, incompatible period sets.
    statement_periods: dict[StatementType, set[str]] = defaultdict(set)
    for value in values:
        if value.reporting_period:
            statement_periods[value.statement_type].add(value.reporting_period)
    for statement_type, periods in statement_periods.items():
        if len(periods) > 8:
            warnings.append(
                ProcessingWarning(
                    code="too_many_reporting_periods",
                    message=f"{statement_type.value} contains an unusually large number of reporting periods.",
                )
            )

    return warnings
