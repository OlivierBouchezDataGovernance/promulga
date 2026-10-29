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


# ---------------------------------------------------------------------------
# Cover page for formats that cannot carry one (PDF, .odt, .md...)
# ---------------------------------------------------------------------------

def cover_pdf(vals: dict[str, str], labels: list[tuple[str, str]], history: list[dict[str, str]],
              logo: tuple[bytes, str] | None = None) -> bytes:
    """A one-page A4 cover with the same content as the Word cover page."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    from .docx_meta import CLASSIFICATION_COLORS, CLASSIFICATION_NOTES

    slate, muted, line = colors.HexColor("#1E293B"), colors.HexColor("#64748B"), colors.HexColor("#CBD5E1")
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=8.5, leading=11)
    buffer = io.BytesIO()
    story = []
    code = vals.get("CLASSIFICATION_CODE") or "internal"
    if vals.get("CLASSIFICATION"):
        fill, ink = CLASSIFICATION_COLORS.get(code, CLASSIFICATION_COLORS["internal"])
        band = Table([[Paragraph(escape(vals["CLASSIFICATION"].upper()), ParagraphStyle(
            "band", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=18, leading=22, alignment=1,
            textColor=colors.HexColor(f"#{ink}")))]], colWidths=[475])
        band.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(f"#{fill}")),
                                  ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
        story += [band, Spacer(1, 18)]
    if logo:
        try:
            width, height = ImageReader(io.BytesIO(logo[0])).getSize()
            scale = min(170 / width, 57 / height)
            image = Image(io.BytesIO(logo[0]), width=width * scale, height=height * scale)
            image.hAlign = "LEFT"
            story += [image, Spacer(1, 22)]
        except Exception:  # a broken logo must not block a publication
            pass
    story += [
        Paragraph(escape(f"{vals.get('REF', '')}  ·  {vals.get('TYPE', '')}".upper()),
                  ParagraphStyle("eyebrow", parent=styles["BodyText"], textColor=muted, fontSize=9)),
        Spacer(1, 6),
        Paragraph(escape(vals.get("TITLE", "")), ParagraphStyle("t", parent=styles["Title"], alignment=0,
                                                                 textColor=slate, fontSize=24, leading=29)),
    ]
    if vals.get("DESCRIPTION"):
        story += [Paragraph(escape(vals["DESCRIPTION"]), styles["BodyText"]), Spacer(1, 10)]
    meta = Table([[Paragraph(f"<b>{escape(label)}</b>", small), Paragraph(escape(vals.get(key, "") or "-"), small)]
                  for key, label in labels], colWidths=[120, 330])
    meta.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, line),
                              ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F1F5F9")),
                              ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    rows = [["Version", "Date", "Author", "Changes", "Approved by"]]
    rows += [[h.get("version", ""), h.get("date", ""), h.get("author", ""), h.get("note", ""),
              h.get("approved_by", "")] for h in history] or [["-", "-", "-", "Not published yet", "-"]]
    hist = Table([[Paragraph(escape(str(c)), small) for c in r] for r in rows], colWidths=[50, 65, 90, 165, 80],
                 repeatRows=1)
    hist.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, line),
                              ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
                              ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [meta, Spacer(1, 16),
              Paragraph("Version history", ParagraphStyle("h", parent=styles["Heading2"], textColor=slate)),
              hist, Spacer(1, 14)]
    if vals.get("COPYRIGHT"):
        story.append(Paragraph(escape(vals["COPYRIGHT"]), ParagraphStyle("c", parent=small, textColor=slate)))
    if vals.get("CLASSIFICATION"):
        story.append(Paragraph(escape(f"{vals['CLASSIFICATION']}: {CLASSIFICATION_NOTES.get(code, '')}"),
                               ParagraphStyle("cn", parent=small, textColor=muted)))
    story.append(Paragraph("Cover page generated by Promulga.", ParagraphStyle("n", parent=small, textColor=muted)))
    SimpleDocTemplate(buffer, pagesize=A4, title=vals.get("TITLE", ""), leftMargin=60, rightMargin=60,
                      topMargin=60, bottomMargin=60).build(story)
    return buffer.getvalue()


def prepend(cover: bytes, document: bytes) -> bytes:
    writer = PdfWriter()
    for part in (cover, document):
        writer.append(PdfReader(io.BytesIO(part)))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Electronic seal (PAdES signature)
# ---------------------------------------------------------------------------

def _signer():
    """The signing identity: SIGNING_CERT_FILE (PKCS#12) or a self-signed certificate kept in the data dir."""
    from pyhanko.sign import signers

    cfg = get_config()
    if cfg.signing_cert_file:
        return signers.SimpleSigner.load_pkcs12(cfg.signing_cert_file,
                                                passphrase=cfg.signing_cert_password.encode() or None)
    folder = cfg.data_dir / "signing"
    folder.mkdir(exist_ok=True)
    p12, pw_file = folder / "promulga-seal.p12", folder / "promulga-seal.password"
    if not p12.exists():
        _create_self_signed(p12, pw_file)
    return signers.SimpleSigner.load_pkcs12(str(p12), passphrase=pw_file.read_bytes().strip())


def _create_self_signed(p12: Path, pw_file: Path) -> None:
    import datetime
    import secrets as pysecrets

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    host = get_config().base_url.split("//")[-1].split("/")[0] or "localhost"
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Promulga document seal ({host})"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Promulga")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]), critical=False)
            .sign(key, hashes.SHA256()))
    password = pysecrets.token_urlsafe(24).encode()
    p12.write_bytes(pkcs12.serialize_key_and_certificates(b"promulga", key, cert, None,
                                                          BestAvailableEncryption(password)))
    pw_file.write_bytes(password)
    for f in (p12, pw_file):
        try:
            f.chmod(0o600)
        except OSError:
            pass


def sign(pdf_bytes: bytes, reason: str) -> bytes:
    """Applies a PAdES signature: any later change to the PDF breaks it visibly in PDF readers."""
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers

    writer = IncrementalPdfFileWriter(io.BytesIO(pdf_bytes))
    meta = signers.PdfSignatureMetadata(field_name="PromulgaSeal", reason=reason[:200], location="Promulga")
    out = signers.sign_pdf(writer, meta, signer=_signer())
    return out.getvalue()
