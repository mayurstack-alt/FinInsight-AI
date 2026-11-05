from io import BytesIO

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.core.errors import ApplicationError
from app.modules.reports.validation import sanitize_pdf_filename, validate_pdf_upload
from tests.pdf_samples import minimal_pdf


def upload(name: str = "annual-report.pdf", content: bytes | None = None, mime: str = "application/pdf") -> UploadFile:
    if content is None:
        content = minimal_pdf()
    return UploadFile(
        filename=name,
        file=BytesIO(content),
        headers=Headers({"content-type": mime}),
    )


def test_valid_pdf_is_bounded_and_rewound() -> None:
    content = minimal_pdf()
    candidate = upload(content=content)
    result = validate_pdf_upload(candidate, 1024)
    assert result.filename == "annual-report.pdf"
    assert result.size == len(content)
    assert candidate.file.read(5) == b"%PDF-"


@pytest.mark.parametrize("filename", ["report.txt", "../report.txt", ""])
def test_rejects_invalid_extension_or_empty_name(filename: str) -> None:
    with pytest.raises(ApplicationError):
        validate_pdf_upload(upload(name=filename), 1024)


def test_rejects_non_pdf_declared_mime() -> None:
    with pytest.raises(ApplicationError) as error:
        validate_pdf_upload(upload(mime="application/octet-stream"), 1024)
    assert error.value.status_code == 422


def test_rejects_invalid_magic_bytes_and_empty_file() -> None:
    for content in (b"not a PDF", b""):
        with pytest.raises(ApplicationError) as error:
            validate_pdf_upload(upload(content=content), 1024)
        assert error.value.status_code == 422


def test_rejects_pdf_signature_with_invalid_structure() -> None:
    with pytest.raises(ApplicationError) as error:
        validate_pdf_upload(upload(content=b"%PDF-1.7\nnot a parseable PDF"), 1024)
    assert error.value.status_code == 422


def test_rejects_oversized_file_without_reading_past_limit() -> None:
    candidate = upload(content=b"%PDF-" + b"x" * 1024)
    with pytest.raises(ApplicationError) as error:
        validate_pdf_upload(candidate, 64)
    assert error.value.status_code == 413


def test_sanitizes_traversal_and_unsafe_filename_characters() -> None:
    assert sanitize_pdf_filename(r"..\folder\annual report!.pdf") == "annual_report_.pdf"


def test_rejects_null_bytes_in_filename() -> None:
    with pytest.raises(ApplicationError) as err:
        sanitize_pdf_filename("report\x00malicious.pdf")
    assert err.value.status_code == 422


def test_truncates_oversized_filename_safely() -> None:
    long_name = ("a" * 300) + ".pdf"
    sanitized = sanitize_pdf_filename(long_name)
    assert len(sanitized) <= 255
    assert sanitized.endswith(".pdf")


def test_dot_underscore_fallback_to_report_pdf() -> None:
    dots_name = ("_" * 260) + ".pdf"
    sanitized = sanitize_pdf_filename(dots_name)
    assert sanitized == "report.pdf"


def test_truncation_falls_back_when_truncated_stem_contains_only_dots_and_underscores() -> None:
    filename = ("_" * 251) + "meaningful-after-limit.pdf"
    assert sanitize_pdf_filename(filename) == "report.pdf"

