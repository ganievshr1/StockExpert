from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from openpyxl import load_workbook
from docx import Document
from pypdf import PdfReader
from app.core.config import settings


def validate_container(data):
    try:
        with ZipFile(BytesIO(data)) as z:
            if sum(x.file_size for x in z.infolist()) > 200_000_000:
                raise ValueError("Expanded file exceeds 200 MB")
    except BadZipFile:
        raise ValueError("Invalid Office document")


def sheets(data):
    validate_container(data)
    wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
    try:
        count = 0
        for ws in wb.worksheets:
            rows = []
            for idx, row in enumerate(ws.iter_rows(values_only=True), 1):
                values = {
                    str(i): str(v)
                    for i, v in enumerate(row)
                    if v is not None and str(v).strip()
                }
                if not values:
                    continue
                count += 1
                if count > settings.MAX_ROWS:
                    raise ValueError("Too many Excel rows")
                if len(row) > 256 or sum(map(len, values.values())) > 16000:
                    raise ValueError("Row too wide or long")
                rows.append({"row_index": idx, "cells": values})
            if rows:
                yield ws.title, rows
    finally:
        wb.close()


def document_text(data, name):
    suffix = Path(name).suffix.lower()
    if suffix == ".txt":
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = data.decode("utf-16")
        else:
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = data.decode("cp1251")
        if "\x00" in text:
            raise ValueError("TXT contains binary data; save as UTF-8")
    elif suffix == ".pdf":
        reader = PdfReader(BytesIO(data))
        if len(reader.pages) > 100:
            raise ValueError("Maximum 100 PDF pages")
        text = "\n".join(
            f"[Page {i + 1}] " + (p.extract_text() or "")
            for i, p in enumerate(reader.pages)
        )
        if sum(len(p.extract_text() or "") for p in reader.pages) < 20:
            raise ValueError(
                "OCR_REQUIRED: scanned PDF needs OCR; upload text PDF/DOCX"
            )
    elif suffix == ".docx":
        validate_container(data)
        d = Document(BytesIO(data))
        text = "\n".join(
            [p.text for p in d.paragraphs]
            + [
                " | ".join(c.text for c in row.cells)
                for t in d.tables
                for row in t.rows
            ]
        )
    elif suffix == ".xlsx":
        text = "\n".join(
            f"{s}:{r['row_index']} " + json_row(r["cells"])
            for s, rs in sheets(data)
            for r in rs
        )
    else:
        raise ValueError("Unsupported requirement file")
    if not text.strip():
        raise ValueError("Empty document")
    if len(text) > 12000:
        raise ValueError(
            "Requirement exceeds MVP context limit (12000 characters); split into documents"
        )
    return text


def json_row(cells):
    return " | ".join(f"{k}: {v}" for k, v in cells.items())
