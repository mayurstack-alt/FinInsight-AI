"""Small, synthetic PDF fixtures for deterministic extraction tests."""

from __future__ import annotations

from io import BytesIO

import pymupdf
from PIL import Image, ImageDraw, ImageFont


def minimal_pdf() -> bytes:
    """Return a small, structurally valid one-page PDF for upload tests."""
    document = pymupdf.open()
    document.new_page()
    result = document.tobytes()
    document.close()
    return result


def draw_table(page, rows: list[list[str]], y_start: int = 150) -> None:
    columns = [45, 345, 455, 555]
    row_height = 24
    bottom = y_start + len(rows) * row_height
    for x in columns:
        page.draw_line((x, y_start), (x, bottom), width=0.7)
    for row_index in range(len(rows) + 1):
        y = y_start + row_index * row_height
        page.draw_line((columns[0], y), (columns[-1], y), width=0.7)
    for row_index, row in enumerate(rows):
        y = y_start + row_index * row_height + 16
        for col_index, value in enumerate(row):
            if value:
                page.insert_text((columns[col_index] + 5, y), value, fontsize=9)


def financial_report_pdf() -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((45, 60), "Statement of Profit and Loss", fontsize=17)
    page.insert_text((45, 85), "USD in thousands | Year ended 31 March", fontsize=10)
    draw_table(
        page,
        [
            ["Particulars", "FY2025", "FY2024"],
            ["Revenue", "1,200", "1,100"],
            ["Operating expenses", "(600)", "(500)"],
            ["Profit before tax", "200", "180"],
        ],
    )

    page = document.new_page()
    page.insert_text((45, 60), "Statement of Profit and Loss (continued)", fontsize=16)
    page.insert_text((45, 85), "USD in thousands", fontsize=10)
    draw_table(page, [["Net income", "150", "125"], ["Earnings per share", "3.00", "2.50"]])

    page = document.new_page()
    page.insert_text((45, 60), "Statement of Financial Position", fontsize=17)
    page.insert_text((45, 85), "USD in thousands | As at 31 March", fontsize=10)
    draw_table(
        page,
        [
            ["Particulars", "FY2025", "FY2024"],
            ["Total assets", "1,000", "900"],
            ["Total liabilities", "600", "500"],
            ["Total equity", "400", "400"],
        ],
    )

    page = document.new_page()
    page.insert_text((45, 60), "Consolidated Cash Flow Statement", fontsize=17)
    page.insert_text((45, 85), "INR in crores | Year ended 31 March", fontsize=10)
    draw_table(
        page,
        [
            ["Particulars", "FY2025", "FY2024"],
            ["Net cash from operating activities", "100", "110"],
            ["Cash at end of period", "90", "80"],
        ],
    )
    result = document.tobytes()
    document.close()
    return result


def scanned_report_pdf() -> bytes:
    image = Image.new("RGB", (1224, 1584), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=42)
    draw.text((100, 120), "Statement of Profit and Loss", fill="black", font=font)
    draw.text((100, 230), "INR in crores", fill="black", font=font)
    draw.text((100, 420), "Particulars       FY2025       FY2024", fill="black", font=font)
    draw.text((100, 540), "Revenue             1,200         1,100", fill="black", font=font)
    draw.text((100, 660), "Net income            150           125", fill="black", font=font)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    page.insert_image(page.rect, stream=buffer.getvalue())
    result = document.tobytes()
    document.close()
    return result


def mixed_text_and_scan_pdf() -> bytes:
    report = pymupdf.open()
    text_page = report.new_page()
    text_page.insert_text((50, 60), "Annual report narrative text is selectable and searchable." * 2)
    scanned = pymupdf.open(stream=scanned_report_pdf(), filetype="pdf")
    report.insert_pdf(scanned)
    scanned.close()
    result = report.tobytes()
    report.close()
    return result
