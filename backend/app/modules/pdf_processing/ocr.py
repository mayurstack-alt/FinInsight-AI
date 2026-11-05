"""Targeted Tesseract OCR fallback for scanned or low-text PDF pages."""

from __future__ import annotations

import logging
import statistics
import threading
from collections import defaultdict
from io import BytesIO

import pymupdf
import pytesseract
from PIL import Image
from pytesseract import Output

from app.config import get_settings
from app.modules.pdf_processing.models import OCRPageResult, PDFProcessingError

logger = logging.getLogger(__name__)
_TESSERACT_COMMAND_LOCK = threading.RLock()
_MAX_OCR_PIXELS = 20_000_000


def _group_ocr_data(data: dict[str, list[object]]) -> tuple[list[str], list[list[str | None]], list[float]]:
    grouped: dict[tuple[int, int, int], list[tuple[int, int, int, int, str, float]]] = defaultdict(list)
    count = len(data.get("text", []))
    for index in range(count):
        word = str(data["text"][index]).strip()
        try:
            confidence = float(data["conf"][index])
            left = int(data["left"][index])
            top = int(data["top"][index])
            height = int(data["height"][index])
            width = int(data.get("width", [0] * count)[index])
            block = int(data["block_num"][index])
            paragraph = int(data["par_num"][index])
            line = int(data["line_num"][index])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if word and confidence >= 0:
            grouped[(block, paragraph, line)].append((left, top, height, width, word, confidence))

    line_words: list[list[tuple[int, int, int, int, str, float]]] = []
    for words in grouped.values():
        words.sort(key=lambda item: item[0])
        line_words.append(words)
    line_words.sort(key=lambda items: (items[0][1], items[0][0]))
    lines = [" ".join(word[4] for word in words) for words in line_words]

    heights = [word[2] for words in line_words for word in words if word[2] > 0]
    column_gap = max(20, int(statistics.median(heights) * 1.8)) if heights else 20
    rows: list[list[str | None]] = []
    confidences: list[float] = []
    for words in line_words:
        cells: list[list[str]] = []
        previous_right: int | None = None
        for left, _top, _height, word_width, word, confidence in words:
            if previous_right is not None and left - previous_right > column_gap:
                cells.append([])
            if not cells:
                cells.append([])
            cells[-1].append(word)
            previous_right = left + max(1, word_width or len(word) * 8)
            confidences.append(confidence)
        rows.append([" ".join(cell) for cell in cells])
    return lines, rows, confidences


def run_ocr(pdf_bytes: bytes, page_number: int) -> OCRPageResult:
    """Render one 1-based page and OCR it, preserving line/cell coordinates."""
    settings = get_settings()
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
            if page_number < 1 or page_number > document.page_count:
                raise PDFProcessingError("ocr_page_invalid", "OCR page number is outside the document")
            page = document.load_page(page_number - 1)
            estimated_pixels = (
                page.rect.width * settings.pdf_ocr_dpi / 72
            ) * (page.rect.height * settings.pdf_ocr_dpi / 72)
            if estimated_pixels > _MAX_OCR_PIXELS:
                raise PDFProcessingError("ocr_page_too_large", "The page is too large to render safely for OCR")
            pixmap = page.get_pixmap(
                dpi=settings.pdf_ocr_dpi,
                alpha=False,
            )
        image = Image.open(BytesIO(pixmap.tobytes("png")))
        with _TESSERACT_COMMAND_LOCK:
            old_command = pytesseract.pytesseract.tesseract_cmd
            if settings.tesseract_cmd:
                pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
            try:
                data = pytesseract.image_to_data(
                    image,
                    output_type=Output.DICT,
                    config="--psm 6",
                    timeout=settings.pdf_ocr_timeout_seconds,
                )
            finally:
                pytesseract.pytesseract.tesseract_cmd = old_command
    except PDFProcessingError:
        raise
    except pytesseract.TesseractNotFoundError as exc:
        raise PDFProcessingError("ocr_unavailable", "Tesseract OCR is not installed or not on PATH") from exc
    except pytesseract.TesseractError as exc:
        logger.warning("Tesseract OCR failed", extra={"page_number": page_number})
        raise PDFProcessingError("ocr_failed", "Tesseract could not process this page") from exc
    except Exception as exc:
        logger.warning("PDF page rendering for OCR failed", extra={"page_number": page_number})
        raise PDFProcessingError("ocr_failed", "The page could not be prepared for OCR") from exc

    lines, rows, confidences = _group_ocr_data(data)
    confidence = round(sum(confidences) / len(confidences), 2) if confidences else None
    return OCRPageResult(
        page_number=page_number,
        text="\n".join(lines).strip(),
        lines=lines,
        rows=rows,
        confidence=confidence,
    )
