"""PDF production for publications.

1. Conversion of office files to PDF, with (in this order):
   - Gotenberg (``GOTENBERG_URL``), the LibreOffice-in-a-container service
     started by docker-compose;
   - a local ``soffice`` (LibreOffice) if it is on the PATH.
   PDF files are used as they are.
2. Stamping: every page gets a footer "REF - Title - Version x.y - Published
   yyyy-mm-dd - page n/N", so a printed or downloaded copy always says which
   official version it is.
3. Text extraction, for the portal search.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import Color
from reportlab.pdfgen import canvas

from .config import get_config
from .storage import extension

OFFICE_EXTENSIONS = {".docx", ".doc", ".odt", ".rtf", ".xlsx", ".pptx", ".txt", ".md"}


class ConversionError(RuntimeError):
    pass


def to_pdf(filename: str, data: bytes) -> bytes:
    ext = extension(filename)
    if ext == ".pdf":
        return data
    if ext not in OFFICE_EXTENSIONS:
        raise ConversionError(f"Cannot convert {ext or 'this file'} to PDF.")
    cfg = get_config()
    if cfg.gotenberg_url:
        try:
            response = httpx.post(
                f"{cfg.gotenberg_url}/forms/libreoffice/convert",
                files={"files": (f"document{ext}", data)},
                timeout=120,
            )
        except httpx.HTTPError as exc:
            raise ConversionError(f"The PDF conversion service is not reachable ({exc}).") from exc
        if response.status_code != 200:
            raise ConversionError(f"PDF conversion failed ({response.status_code}): {response.text[:200]}")
        return response.content
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / f"document{ext}"
            src.write_bytes(data)
            subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, str(src)],
                           check=True, capture_output=True, timeout=180)
            return (Path(tmp) / "document.pdf").read_bytes()
    raise ConversionError("No PDF converter configured: set GOTENBERG_URL (see docker-compose.yml) "
                          "or upload the final version as a PDF.")


def stamp(pdf_bytes: bytes, label: str) -> bytes:
    """Adds `label` and the page number at the bottom of every page."""
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes)))
    total = len(writer.pages)
    for index, page in enumerate(writer.pages, start=1):
        width = float(page.mediabox.width)
        overlay_buffer = io.BytesIO()
        c = canvas.Canvas(overlay_buffer, pagesize=(width, float(page.mediabox.height)))
        c.setFont("Helvetica", 7.5)
        c.setFillColor(Color(0.35, 0.4, 0.5))
        c.drawString(28, 14, label[:150])
        c.drawRightString(width - 28, 14, f"page {index}/{total}")
        c.save()
        overlay_buffer.seek(0)
        page.merge_page(PdfReader(overlay_buffer).pages[0])
    writer.add_metadata({"/Title": label, "/Producer": "Promulga"})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def extract_text(pdf_bytes: bytes, max_chars: int = 200_000) -> str:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
            if sum(len(p) for p in parts) > max_chars:
                break
        return " ".join(" ".join(parts).split())[:max_chars]
    except Exception:  # extraction is best effort; publication must not fail on it
        return ""
