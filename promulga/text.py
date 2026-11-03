"""Clean text of a published file, for search and for the chatbots that index it.

Markdown and plain text are read as they are; Word keeps its headings (as Markdown '#') and its tables (cells
separated by ' | ') so that an indexer can cut the text into passages; PDF keeps its line breaks. Other formats are
converted to PDF first. Extraction is best effort: it never makes a publication fail.
"""

from __future__ import annotations

import io
import re
import zipfile

from lxml import etree

from . import pdf
from .storage import extension

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_CHARS = 500_000


def clean_text(filename: str, data: bytes) -> str:
    ext = extension(filename)
    try:
        if ext in (".md", ".txt"):
            text = _decode(data)
        elif ext == ".docx":
            text = _docx_text(data)
        elif ext == ".pdf":
            text = _pdf_text(data)
        else:
            text = _pdf_text(pdf.to_pdf(filename, data))
    except Exception:  # damaged file, no converter: the publication goes on without text
        return ""
    return _tidy(text)[:MAX_CHARS]


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _heading_level(p) -> int:
    style = p.find(f"{W}pPr/{W}pStyle")
    name = (style.get(f"{W}val") if style is not None else "") or ""
    if name.lower() == "title":
        return 1
    match = re.match(r"(?:heading|titre|kop)\s*(\d)", name, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _para_text(p) -> str:
    out = []
    for node in p.iter(f"{W}t", f"{W}tab", f"{W}br"):
        out.append(node.text or "" if node.tag == f"{W}t" else (" " if node.tag == f"{W}tab" else "\n"))
    return "".join(out).strip()


def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    body = root.find(f"{W}body")
    lines: list[str] = []
    for el in body if body is not None else []:
        if el.tag == f"{W}p":
            text = _para_text(el)
            if text:
                level = _heading_level(el)
                lines.append(("#" * level + " " + text) if level else text)
        elif el.tag == f"{W}tbl":
            for row in el.iter(f"{W}tr"):
                cells = [" ".join(_para_text(p) for p in tc.iter(f"{W}p")).strip() for tc in row.iter(f"{W}tc")]
                if any(cells):
                    lines.append(" | ".join(cells))
    return "\n\n".join(lines)


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages)


def _tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
