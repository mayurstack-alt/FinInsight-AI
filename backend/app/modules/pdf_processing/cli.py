"""Local development runner for a controlled PDF file; not a production API."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.modules.pdf_processing.models import PDFProcessingError
from app.modules.pdf_processing.pipeline import process_pdf


def main() -> int:
    parser = argparse.ArgumentParser(description="Run FinInsight PDF extraction locally")
    parser.add_argument("pdf", type=Path, help="Path to a PDF file for local testing")
    arguments = parser.parse_args()
    try:
        result = process_pdf(arguments.pdf)
    except PDFProcessingError as exc:
        print(json.dumps({"error": exc.code, "detail": exc.public_message}), file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
