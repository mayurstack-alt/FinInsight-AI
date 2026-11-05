"""Bounded validation for uploaded annual-report PDFs."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import BinaryIO

import pymupdf
from fastapi import UploadFile

from app.core.errors import ApplicationError

_CHUNK_SIZE = 64 * 1024
_ALLOWED_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class ValidatedPdf:
    filename: str
    size: int


def sanitize_pdf_filename(filename: str | None) -> str:
    if not filename:
        raise ApplicationError(422, "invalid_filename", "A valid PDF filename is required")
    if "\x00" in filename:
        raise ApplicationError(422, "invalid_filename", "Filename contains forbidden characters")
    normalized = unicodedata.normalize("NFKC", filename).replace("\\", "/")
    basename = PurePosixPath(normalized).name
    if not basename or basename in {".", ".."} or not basename.lower().endswith(".pdf"):
        raise ApplicationError(422, "invalid_pdf", "Only PDF files are accepted")
    stem = basename[:-4]
    safe_stem = _ALLOWED_FILENAME.sub("_", stem)
    if not safe_stem.strip("._"):
        safe_stem = "report"
    elif len(safe_stem) > 251:
        safe_stem = safe_stem[:251].rstrip("._")
        if not safe_stem:
            safe_stem = "report"
    return f"{safe_stem}.pdf"


def validate_pdf_upload(upload: UploadFile, max_size_bytes: int) -> ValidatedPdf:
    filename = sanitize_pdf_filename(upload.filename)
    if upload.content_type != "application/pdf":
        raise ApplicationError(422, "invalid_pdf", "Only PDF files are accepted")
    if max_size_bytes <= 0:
        raise ApplicationError(500, "upload_configuration_error", "Upload is temporarily unavailable")

    stream: BinaryIO = upload.file
    try:
        stream.seek(0)
    except (OSError, ValueError) as exc:
        raise ApplicationError(422, "invalid_pdf", "The uploaded file could not be read") from exc

    size = 0
    header = b""
    while True:
        chunk = stream.read(min(_CHUNK_SIZE, max_size_bytes + 1 - size))
        if not chunk:
            break
        if not header:
            header = chunk[:5]
        size += len(chunk)
        if size > max_size_bytes:
            raise ApplicationError(413, "file_too_large", "The PDF exceeds the maximum allowed size")

    if size == 0 or not header.startswith(b"%PDF-"):
        raise ApplicationError(422, "invalid_pdf", "The uploaded file is not a valid PDF")

    # A signature alone is easy to spoof. The upload has already been size-bounded,
    # so parse its complete contents before accepting it for Storage. PyMuPDF needs
    # the full PDF structure (including cross-reference data), not just the header.
    try:
        stream.seek(0)
        payload = stream.read(max_size_bytes + 1)
        if len(payload) != size:
            raise ApplicationError(422, "invalid_pdf", "The uploaded file could not be read")
        with pymupdf.open(stream=payload, filetype="pdf") as document:
            if document.needs_pass or document.page_count < 1:
                raise ApplicationError(422, "invalid_pdf", "The uploaded file is not a readable PDF")
    except ApplicationError:
        raise
    except Exception as exc:  # Invalid and damaged user PDFs can raise parser-specific exceptions.
        raise ApplicationError(422, "invalid_pdf", "The uploaded file is not a readable PDF") from exc
    stream.seek(0)
    return ValidatedPdf(filename=filename, size=size)
