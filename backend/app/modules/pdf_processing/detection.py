"""Context-aware detection of three financial statement families."""

from __future__ import annotations

import re
from collections import defaultdict

from app.modules.pdf_processing.models import (
    PageText,
    StatementDetection,
    StatementType,
)

_RULES: dict[StatementType, tuple[tuple[str, ...], tuple[tuple[str, str], ...]]] = {
    StatementType.INCOME_STATEMENT: (
        (
            r"\bincome statement\b",
            r"\bstatement of (?:profit and loss|profit or loss)\b",
            r"\bprofit and loss account\b",
            r"\bstatement of operations\b",
            r"\bstatement of comprehensive income\b",
        ),
        (
            ("revenue", r"\b(?:revenue|turnover|net sales)\b"),
            ("expenses", r"\b(?:expenses?|cost of sales|operating costs?)\b"),
            ("profit_loss", r"\b(?:profit|loss|earnings)\b"),
            ("period", r"\b(?:year|period) ended\b"),
        ),
    ),
    StatementType.BALANCE_SHEET: (
        (
            r"\bbalance sheet\b",
            r"\bstatement of (?:financial position|assets and liabilities)\b",
            r"\bfinancial position\b",
        ),
        (
            ("assets", r"\bassets?\b"),
            ("liabilities", r"\bliabilit(?:y|ies)\b"),
            ("equity", r"\b(?:shareholders'?|stockholders'? )?equity\b"),
            ("period", r"\b(?:as at|as of|at)\s+\d{1,2}\s+[a-z]+\s+20\d{2}\b"),
        ),
    ),
    StatementType.CASH_FLOW_STATEMENT: (
        (
            r"\bcash flow statement\b",
            r"\bstatement of cash flows?\b",
            r"\bcash flows? from operating activities\b",
        ),
        (
            ("operating", r"\boperating activities\b"),
            ("investing", r"\binvesting activities\b"),
            ("financing", r"\bfinancing activities\b"),
            ("cash", r"\b(?:cash equivalents?|net cash)\b"),
        ),
    ),
}


def _page_score(text: str, statement_type: StatementType) -> tuple[int, list[str], bool]:
    headings, cues = _RULES[statement_type]
    normalized = re.sub(r"\s+", " ", text.casefold())
    heading_matches = [pattern for pattern in headings if re.search(pattern, normalized)]
    cue_matches = [name for name, pattern in cues if re.search(pattern, normalized)]
    score = 5 * bool(heading_matches) + len(cue_matches)
    # A standalone heading is strong evidence; cue-only pages need several related signs.
    accepted = bool(heading_matches) and len(cue_matches) >= 1 or len(cue_matches) >= 3
    evidence = (["statement_heading"] if heading_matches else []) + cue_matches
    return score, evidence, accepted


def detect_financial_statements(pages: list[PageText]) -> list[StatementDetection]:
    """Score page content using headings plus statement-specific contextual cues."""
    found: dict[StatementType, list[tuple[int, int, list[str]]]] = defaultdict(list)
    for page in pages:
        text = page.analysis_text
        if not text:
            continue
        for statement_type in StatementType:
            score, evidence, accepted = _page_score(text, statement_type)
            if accepted:
                found[statement_type].append((page.page_number, score, evidence))

    detections: list[StatementDetection] = []
    for statement_type, page_hits in found.items():
        page_hits.sort(key=lambda hit: hit[0])
        groups: list[list[tuple[int, int, list[str]]]] = []
        for hit in page_hits:
            if groups and hit[0] <= groups[-1][-1][0] + 1:
                groups[-1].append(hit)
            else:
                groups.append([hit])
        for group in groups:
            page_numbers = [hit[0] for hit in group]
            evidence = sorted({item for hit in group for item in hit[2]})
            mean_score = sum(hit[1] for hit in group) / len(group)
            detections.append(
                StatementDetection(
                    statement_type=statement_type,
                    page_numbers=page_numbers,
                    start_page=page_numbers[0],
                    end_page=page_numbers[-1],
                    confidence=min(0.99, round(0.55 + mean_score / 20, 2)),
                    evidence=evidence,
                )
            )
    return sorted(detections, key=lambda item: (item.start_page, item.statement_type.value))
