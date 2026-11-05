"""Bounded, page-by-page PDF inspection using PyMuPDF."""

from __future__ import annotations

import re
from pathlib import Path
from typing import BinaryIO

import pymupdf

from app.config import get_settings
from app.modules.pdf_processing.models import PageText, PDFDocument, PDFProcessingError

_ALPHANUMERIC = re.compile(r"[\w]", flags=re.UNICODE)
_METADATA_FIELDS = ("title", "subject", "creationDate", "modDate")


def _read_source(
    source: bytes | bytearray | memoryview | str | Path | BinaryIO,
    *,
    max_bytes: int | None = None,
) -> bytes:
    max_bytes = get_settings().max_pdf_size_bytes if max_bytes is None else max_bytes
    if max_bytes < 1:
        raise PDFProcessingError("pdf_size_limit_invalid", "PDF size limit must be positive")
    if isinstance(source, (bytes, bytearray, memoryview)):
        data = bytes(source)
    elif isinstance(source, (str, Path)):
        path = Path(source)
        try:
            if path.stat().st_size > max_bytes:
                raise PDFProcessingError("pdf_too_large", "PDF exceeds the configured size limit")
            data = path.read_bytes()
        except PDFProcessingError:
            raise
        except OSError as exc:
            raise PDFProcessingError("pdf_unreadable", "PDF input could not be read") from exc
    else:
        try:
            previous_position = source.tell()
            source.seek(0)
            data = source.read(max_bytes + 1)
            source.seek(previous_position)
        except (OSError, ValueError, AttributeError) as exc:
            raise PDFProcessingError("pdf_unreadable", "PDF input could not be read") from exc

    if not data:
        raise PDFProcessingError("pdf_empty", "PDF input is empty")
    if len(data) > max_bytes:
        raise PDFProcessingError("pdf_too_large", "PDF exceeds the configured size limit")
    if not data.startswith(b"%PDF-"):
        raise PDFProcessingError("pdf_invalid", "Input does not have a valid PDF signature")
    return data


def load_pdf(
    source: bytes | bytearray | memoryview | str | Path | BinaryIO,
    *,
    max_pages: int | None = None,
    min_text_characters: int | None = None,
    max_bytes: int | None = None,
) -> PDFDocument:
    """Inspect the document and preserve page-indexed selectable text."""
    data = _read_source(source, max_bytes=max_bytes)
    settings = get_settings()
    page_limit = settings.pdf_processing_max_pages if max_pages is None else max_pages
    text_limit = settings.pdf_text_min_characters if min_text_characters is None else min_text_characters

    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:
            if document.needs_pass:
                raise PDFProcessingError("pdf_encrypted", "Password-protected PDFs are not supported")
            page_count = document.page_count
            if page_count < 1:
                raise PDFProcessingError("pdf_empty", "PDF contains no pages")
            if page_count > page_limit:
                raise PDFProcessingError("pdf_page_limit", "PDF exceeds the configured page limit")

            metadata = {
                key: str(value).strip()[:500]
                for key in _METADATA_FIELDS
                if (value := (document.metadata or {}).get(key))
            }
            pages: list[PageText] = []
            for page_index in range(page_count):
                page = document.load_page(page_index)
                try:
                    text = page.get_text("text", sort=True) or ""
                    has_images = bool(page.get_images(full=True))
                except Exception as exc:
                    raise PDFProcessingError(
                        "pdf_page_unreadable", "A PDF page could not be read"
                    ) from exc
                character_count = len(_ALPHANUMERIC.findall(text))
                pages.append(
                    PageText(
                        page_number=page_index + 1,
                        text=text.strip(),
                        character_count=character_count,
                        has_images=has_images,
                        insufficient_text=character_count < text_limit,
                    )
                )
            return PDFDocument(pages=pages, metadata=metadata, page_count=page_count)
    except PDFProcessingError:
        raise
    except (pymupdf.FileDataError, ValueError, RuntimeError) as exc:
        raise PDFProcessingError("pdf_corrupt", "PDF is corrupted or unreadable") from exc
