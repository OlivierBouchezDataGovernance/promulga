"""Word (.docx) metadata: variables and the automatic cover page.

Variables are Word *content controls* (Developer > Content controls) whose tag
is ``promulga:<TAG>``, e.g. ``promulga:TITLE``. Promulga rewrites their text at
every check-in, download and publication, so they always show the current
values, also in the published PDF.

Template authors do not need the Developer tab: writing ``{{TITLE}}``,
``{{VERSION}}``... anywhere in the text is enough; the first time the file is
checked in, each placeholder becomes a content control.

Placeholders and controls also work in page headers and footers.

Two *block* variables are replaced by a table when they stand alone in a
paragraph: ``{{HISTORY}}`` (version history) and ``{{APPROVALS}}`` (who
approved the version, and when).

The cover page is one locked content control (``promulga:cover``) inserted at
the very beginning of the document, followed by a page break. It holds the
classification band, the logo, the title, the description, a metadata table,
the version history and the copyright line, and is regenerated each time:
edits made to it in Word are overwritten.

Only .docx supports this. Other formats get a "limited capability" warning;
their published PDF still receives a generated cover page (see cover_pdf).
"""

from __future__ import annotations

import io
import re
import zipfile

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_]+)\s*\}\}")
TAG_PREFIX = "promulga:"
COVER_TAG = "promulga:cover"
PRIMARY = "1E293B"
MUTED = "64748B"
BLOCK_TAGS = ("HISTORY", "APPROVALS")
PART_RE = re.compile(r"word/(document|header\d*|footer\d*)\.xml$")
LOGO_REL_ID = "rIdPromulgaLogo"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
# Classification band of the cover page: (background, text) colours.
CLASSIFICATION_COLORS = {
    "external": ("047857", "FFFFFF"),      # green: may circulate freely
    "commercial": ("1D4ED8", "FFFFFF"),    # blue: customers and partners
    "internal": ("475569", "FFFFFF"),      # slate: staff only
    "limited": ("B45309", "FFFFFF"),       # amber: named recipients only
    "confidential": ("B91C1C", "FFFFFF"),  # red: strictly controlled
}
# What each classification allows, printed on the cover page.
CLASSIFICATION_NOTES = {
    "external": "may be shared outside the organisation without restriction.",
    "commercial": "may be shared with customers and partners in a commercial relationship.",
    "internal": "for staff and authorised contractors only; do not share outside the organisation.",
    "limited": "for the named recipients only; do not forward.",
    "confidential": "strictly controlled; disclosure may harm the organisation. Do not copy or forward.",
}


def supports_metadata(filename: str) -> bool:
    return filename.lower().endswith(".docx")


def capability_warning(filename: str) -> str:
    if supports_metadata(filename):
        return ""
    return ("Limited capability: this format cannot carry Promulga's variables nor the automatic cover page in the "
            "working file. Use a Word .docx file to get them. The published PDF will still get a generated cover page.")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def update_docx(data: bytes, vals: dict[str, str], labels: list[tuple[str, str]],
                history: list[dict[str, str]], cover: bool = True, approvals: list[dict[str, str]] | None = None,
                logo: tuple[bytes, str] | None = None) -> bytes:
    """Returns the .docx with placeholders converted, variables refreshed and the cover page (re)built.

    `logo` is (image bytes, extension) of a PNG or JPEG shown on the cover page."""
    zin = zipfile.ZipFile(io.BytesIO(data))
    if "word/document.xml" not in zin.namelist():
        raise ValueError("Not a Word document body.")
    tables = {"HISTORY": lambda: _history_table(history), "APPROVALS": lambda: _approvals_table(approvals or [])}
    if not (cover and logo and logo[1].lower().lstrip(".") in ("png", "jpg", "jpeg")):
        logo = None
    parts: dict[str, bytes] = {}
    for name in zin.namelist():
        if not PART_RE.match(name):
            continue
        root = etree.fromstring(zin.read(name))
        is_body = name == "word/document.xml"
        if is_body:
            if root.find(f"{W}body") is None:
                raise ValueError("Not a Word document body.")
            _convert_block_placeholders(root)
        for paragraph in list(root.iter(f"{W}p")):
            _convert_placeholders(paragraph, vals)
        for sdt in list(root.iter(f"{W}sdt")):
            tag = _sdt_tag(sdt)
            if not tag or not tag.startswith(TAG_PREFIX) or tag == COVER_TAG:
                continue
            key = tag[len(TAG_PREFIX):].upper()
            if key in BLOCK_TAGS:
                _set_sdt_block(sdt, tables[key]())
            else:
                _set_sdt_text(sdt, vals.get(key, ""))
        if is_body and cover:
            _rebuild_cover(root.find(f"{W}body"), vals, labels, history, logo)
        parts[name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    extra: dict[str, bytes] = {}
    if logo:
        ext = "jpeg" if logo[1].lower().lstrip(".") in ("jpg", "jpeg") else "png"
        extra[f"word/media/promulga-logo.{ext}"] = logo[0]
        extra["word/_rels/document.xml.rels"] = _with_logo_rel(zin, ext)
        extra["[Content_Types].xml"] = _with_content_type(zin.read("[Content_Types].xml"), ext)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if item.filename.startswith("word/media/promulga-logo.") or item.filename in extra:
                continue
            zout.writestr(item, parts.get(item.filename, zin.read(item.filename)))
        for name, content in extra.items():
            zout.writestr(name, content)
    return out.getvalue()


def has_cover(data: bytes) -> bool:
    try:
        root = etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    except (KeyError, zipfile.BadZipFile, etree.XMLSyntaxError):
        return False
    return any(_sdt_tag(s) == COVER_TAG for s in root.iter(f"{W}sdt"))


def docx_paragraphs(data: bytes) -> list[str]:
    """Text of the document paragraphs, without the cover page (for comparisons)."""
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    body = root.find(f"{W}body")
    out = []
    for child in body:
        if child.tag == f"{W}sdt" and _sdt_tag(child) == COVER_TAG:
            continue
        for p in child.iter(f"{W}p"):
            text = "".join(t.text or "" for t in p.iter(f"{W}t")).strip()
            if text:
                out.append(text)
    return out


def new_template(vals: dict[str, str], labels: list[tuple[str, str]],
                 logo: tuple[bytes, str] | None = None) -> bytes:
    """A minimal .docx (cover page + section headings), used when no template is configured."""
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
                     'officedocument.wordprocessingml.document.main+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" Target="word/document.xml"/></Relationships>')
    sections = ["1. Purpose", "2. Scope", "3. Definitions", "4. Statements", "5. Roles and responsibilities",
                "6. Exceptions and variances", "7. References"]
    paras = "".join(
        f'<w:p><w:r><w:rPr><w:b/><w:color w:val="{PRIMARY}"/><w:sz w:val="28"/></w:rPr><w:t>{h}</w:t></w:r></w:p>'
        f'<w:p><w:r><w:t>Write here.</w:t></w:r></w:p>' for h in sections)
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{W_NS}"><w:body>'
                f'{paras}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                f'<w:pgMar w:top="1417" w:right="1417" w:bottom="1417" w:left="1417" w:header="708" w:footer="708" '
                f'w:gutter="0"/></w:sectPr></w:body></w:document>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    return update_docx(out.getvalue(), vals, labels, [], cover=True, logo=logo)


# ---------------------------------------------------------------------------
# Placeholders -> content controls
# ---------------------------------------------------------------------------

def _run_text(run) -> str | None:
    """Text of a simple run (rPr + w:t only), else None (tabs, drawings... are left alone)."""
    parts = []
    for child in run:
        if child.tag == f"{W}rPr":
            continue
        if child.tag != f"{W}t":
            return None
        parts.append(child.text or "")
    return "".join(parts)


def _set_run_text(run, text: str) -> None:
    for child in list(run):
        if child.tag != f"{W}rPr":
            run.remove(child)
    t = etree.SubElement(run, f"{W}t")
    t.text = text
    t.set(XML_SPACE, "preserve")


def _convert_placeholders(paragraph, vals: dict[str, str]) -> None:
    runs = [r for r in paragraph if r.tag == f"{W}r"]
    texts = [_run_text(r) for r in runs]
    if not any(t and "{" in t for t in texts):
        return
    # 1. Merge placeholders split over several runs into their first run.
    changed = True
    while changed:
        changed = False
        runs = [r for r in paragraph if r.tag == f"{W}r" and _run_text(r) is not None]
        texts = [_run_text(r) for r in runs]
        full = "".join(texts)
        offsets, pos = [], 0
        for t in texts:
            offsets.append(pos)
            pos += len(t)
        for m in PLACEHOLDER.finditer(full):
            first = max(i for i, o in enumerate(offsets) if o <= m.start())
            last = max(i for i, o in enumerate(offsets) if o < m.end())
            if first != last:
                head = texts[first][: m.start() - offsets[first]]
                tail = texts[last][m.end() - offsets[last]:]
                _set_run_text(runs[first], head + m.group(0) + tail)
                for r in runs[first + 1:last + 1]:
                    paragraph.remove(r)
                changed = True
                break
    # 2. Split each run around its placeholders: text run / content control / text run.
    for run in [r for r in paragraph if r.tag == f"{W}r"]:
        text = _run_text(run)
        if not text or not PLACEHOLDER.search(text):
            continue
        rpr = run.find(f"{W}rPr")
        anchor = run
        pieces = PLACEHOLDER.split(text)        # [text, KEY, text, KEY, text...]
        for i, piece in enumerate(pieces):
            if i % 2 == 0:
                if piece:
                    new = _make_run(piece, rpr)
                    anchor.addnext(new)
                    anchor = new
            else:
                key = piece.upper()
                sdt = _make_inline_sdt(key, vals.get(key, ""), rpr)
                anchor.addnext(sdt)
                anchor = sdt
        paragraph.remove(run)


def _make_run(text: str, rpr=None):
    run = etree.Element(f"{W}r")
    if rpr is not None:
        run.append(_copy(rpr))
    t = etree.SubElement(run, f"{W}t")
    t.text = text
    t.set(XML_SPACE, "preserve")
    return run


def _copy(el):
    return etree.fromstring(etree.tostring(el))


def _make_inline_sdt(key: str, text: str, rpr=None):
    sdt = etree.Element(f"{W}sdt")
    pr = etree.SubElement(sdt, f"{W}sdtPr")
    etree.SubElement(pr, f"{W}alias").set(f"{W}val", key.title().replace("_", " "))
    etree.SubElement(pr, f"{W}tag").set(f"{W}val", TAG_PREFIX + key)
    etree.SubElement(pr, f"{W}lock").set(f"{W}val", "contentLocked")
    content = etree.SubElement(sdt, f"{W}sdtContent")
    content.append(_make_run(text, rpr))
    return sdt


def _convert_block_placeholders(root) -> None:
    """A paragraph holding only {{HISTORY}} or {{APPROVALS}} becomes a block content control."""
    for p in list(root.iter(f"{W}p")):
        text = "".join(t.text or "" for t in p.iter(f"{W}t")).strip()
        m = PLACEHOLDER.fullmatch(text)
        if not m or m.group(1).upper() not in BLOCK_TAGS or p.getparent() is None:
            continue
        key = m.group(1).upper()
        sdt = etree.Element(f"{W}sdt")
        pr = etree.SubElement(sdt, f"{W}sdtPr")
        etree.SubElement(pr, f"{W}alias").set(f"{W}val", key.title())
        etree.SubElement(pr, f"{W}tag").set(f"{W}val", TAG_PREFIX + key)
        etree.SubElement(pr, f"{W}lock").set(f"{W}val", "sdtContentLocked")
        etree.SubElement(sdt, f"{W}sdtContent").append(_p(""))
        p.addprevious(sdt)
        p.getparent().remove(p)


def _set_sdt_block(sdt, table) -> None:
    """Replaces the content of a block control with `table` (inline controls are left alone)."""
    parent = sdt.getparent()
    if parent is None or parent.tag in (f"{W}p", f"{W}r"):
        return
    content = sdt.find(f"{W}sdtContent")
    if content is None:
        content = etree.SubElement(sdt, f"{W}sdtContent")
    for child in list(content):
        content.remove(child)
    content.append(table)
    content.append(_p("", space_after=0))            # Word wants a paragraph after a table


def _history_table(history: list[dict[str, str]]):
    rows = [["Version", "Date", "Author", "Changes", "Approved by"]]
    rows += [[h.get("version", ""), h.get("date", ""), h.get("author", ""), h.get("note", ""),
              h.get("approved_by", "")] for h in history] or [["-", "-", "-", "Not published yet", "-"]]
    return _table(rows, header=True, widths=[900, 1300, 1800, 3400, 1600])


def _approvals_table(approvals: list[dict[str, str]]):
    rows = [["Role", "Name", "Decision", "Date"]]
    rows += [[a.get("role", ""), a.get("name", ""), a.get("decision", ""), a.get("date", "")]
             for a in approvals] or [["Approver", "-", "Pending", "-"]]
    return _table(rows, header=True, widths=[2200, 3200, 1800, 1800])


# ---------------------------------------------------------------------------
# Refreshing content controls
# ---------------------------------------------------------------------------

def _sdt_tag(sdt) -> str:
    pr = sdt.find(f"{W}sdtPr")
    tag = pr.find(f"{W}tag") if pr is not None else None
    return tag.get(f"{W}val", "") if tag is not None else ""


def _set_sdt_text(sdt, text: str) -> None:
    content = sdt.find(f"{W}sdtContent")
    if content is None:
        content = etree.SubElement(sdt, f"{W}sdtContent")
    paragraphs = content.findall(f"{W}p")
    if paragraphs:                                   # block-level control
        p = paragraphs[0]
        rpr = next((r.find(f"{W}rPr") for r in p.iter(f"{W}r") if r.find(f"{W}rPr") is not None), None)
        for child in list(p):
            if child.tag != f"{W}pPr":
                p.remove(child)
        p.append(_make_run(text, rpr))
        for extra in paragraphs[1:]:
            content.remove(extra)
        return
    rpr = next((r.find(f"{W}rPr") for r in content.iter(f"{W}r") if r.find(f"{W}rPr") is not None), None)
    for child in list(content):
        content.remove(child)
    content.append(_make_run(text, rpr))
    # Placeholder text styling ("Click here to enter text") would hide the value.
    pr = sdt.find(f"{W}sdtPr")
    if pr is not None:
        for flag in pr.findall(f"{W}showingPlcHdr"):
            pr.remove(flag)


# ---------------------------------------------------------------------------
# Cover page
# ---------------------------------------------------------------------------

def _p(text: str = "", bold: bool = False, size: int | None = None, color: str | None = None,
       space_after: int | None = None, page_break: bool = False):
    p = etree.Element(f"{W}p")
    ppr = etree.SubElement(p, f"{W}pPr")
    if space_after is not None:
        sp = etree.SubElement(ppr, f"{W}spacing")
        sp.set(f"{W}after", str(space_after))
    if page_break:
        r = etree.SubElement(p, f"{W}r")
        etree.SubElement(r, f"{W}br").set(f"{W}type", "page")
        return p
    if text:
        rpr = etree.Element(f"{W}rPr")
        if bold:
            etree.SubElement(rpr, f"{W}b")
        if color:
            etree.SubElement(rpr, f"{W}color").set(f"{W}val", color)
        if size:
            etree.SubElement(rpr, f"{W}sz").set(f"{W}val", str(size))
        p.append(_make_run(text, rpr))
    return p


def _table(rows: list[list[str]], header: bool, widths: list[int]):
    tbl = etree.Element(f"{W}tbl")
    pr = etree.SubElement(tbl, f"{W}tblPr")
    etree.SubElement(pr, f"{W}tblW").attrib.update({f"{W}w": str(sum(widths)), f"{W}type": "dxa"})
    borders = etree.SubElement(pr, f"{W}tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        etree.SubElement(borders, f"{W}{side}").attrib.update(
            {f"{W}val": "single", f"{W}sz": "4", f"{W}space": "0", f"{W}color": "CBD5E1"})
    grid = etree.SubElement(tbl, f"{W}tblGrid")
    for w in widths:
        etree.SubElement(grid, f"{W}gridCol").set(f"{W}w", str(w))
    for i, row in enumerate(rows):
        tr = etree.SubElement(tbl, f"{W}tr")
        for j, cell in enumerate(row):
            tc = etree.SubElement(tr, f"{W}tc")
            tcpr = etree.SubElement(tc, f"{W}tcPr")
            etree.SubElement(tcpr, f"{W}tcW").attrib.update({f"{W}w": str(widths[j]), f"{W}type": "dxa"})
            label_cell = (header and i == 0) or (not header and j == 0)
            if label_cell:
                etree.SubElement(tcpr, f"{W}shd").attrib.update(
                    {f"{W}val": "clear", f"{W}color": "auto", f"{W}fill": "F1F5F9"})
            tc.append(_p(cell or "-", bold=label_cell, size=18, space_after=0))
    return tbl


def _band(text: str, fill: str, color: str):
    """A full-width shaded paragraph: the classification band."""
    p = etree.Element(f"{W}p")
    ppr = etree.SubElement(p, f"{W}pPr")
    etree.SubElement(ppr, f"{W}shd").attrib.update({f"{W}val": "clear", f"{W}color": "auto", f"{W}fill": fill})
    etree.SubElement(ppr, f"{W}spacing").attrib.update({f"{W}before": "120", f"{W}after": "360"})
    etree.SubElement(ppr, f"{W}jc").set(f"{W}val", "center")
    rpr = etree.Element(f"{W}rPr")
    etree.SubElement(rpr, f"{W}b")
    etree.SubElement(rpr, f"{W}color").set(f"{W}val", color)
    etree.SubElement(rpr, f"{W}spacing").set(f"{W}val", "40")
    etree.SubElement(rpr, f"{W}sz").set(f"{W}val", "40")
    p.append(_make_run(text.upper(), rpr))
    return p


def _logo_paragraph(width_emu: int, height_emu: int):
    wp = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    pic = "http://schemas.openxmlformats.org/drawingml/2006/picture"
    xml = (f'<w:p xmlns:w="{W_NS}" xmlns:wp="{wp}" xmlns:a="{a}" xmlns:pic="{pic}" xmlns:r="{REL_NS}">'
           f'<w:pPr><w:spacing w:after="480"/></w:pPr><w:r><w:drawing>'
           f'<wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{width_emu}" cy="{height_emu}"/>'
           f'<wp:docPr id="9001" name="Logo"/><a:graphic><a:graphicData '
           f'uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>'
           f'<pic:nvPicPr><pic:cNvPr id="9001" name="Logo"/><pic:cNvPicPr/></pic:nvPicPr>'
           f'<pic:blipFill><a:blip r:embed="{LOGO_REL_ID}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
           f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{width_emu}" cy="{height_emu}"/></a:xfrm>'
           f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic>'
           f'</wp:inline></w:drawing></w:r></w:p>')
    return etree.fromstring(xml)


def logo_size_emu(data: bytes, max_width_cm: float = 6.0, max_height_cm: float = 2.0) -> tuple[int, int]:
    """Display size of the logo, keeping its proportions (EMU: 360000 per cm)."""
    try:
        from PIL import Image
        w, h = Image.open(io.BytesIO(data)).size
    except Exception:
        w, h = 3, 1
    scale = min(max_width_cm / w, max_height_cm / h)
    return int(w * scale * 360000), int(h * scale * 360000)


def _with_logo_rel(zin: zipfile.ZipFile, ext: str) -> bytes:
    name = "word/_rels/document.xml.rels"
    pkg = "http://schemas.openxmlformats.org/package/2006/relationships"
    root = (etree.fromstring(zin.read(name)) if name in zin.namelist()
            else etree.Element(f"{{{pkg}}}Relationships", nsmap={None: pkg}))
    for rel in list(root):
        if rel.get("Id") == LOGO_REL_ID:
            root.remove(rel)
    rel = etree.SubElement(root, f"{{{pkg}}}Relationship")
    rel.attrib.update({"Id": LOGO_REL_ID, "Type": f"{REL_NS}/image", "Target": f"media/promulga-logo.{ext}"})
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _with_content_type(data: bytes, ext: str) -> bytes:
    ct = "http://schemas.openxmlformats.org/package/2006/content-types"
    root = etree.fromstring(data)
    if not any(d.get("Extension", "").lower() == ext for d in root.findall(f"{{{ct}}}Default")):
        d = etree.Element(f"{{{ct}}}Default")
        d.attrib.update({"Extension": ext, "ContentType": f"image/{ext}"})
        root.insert(0, d)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _rebuild_cover(body, vals: dict[str, str], labels: list[tuple[str, str]], history: list[dict[str, str]],
                   logo: tuple[bytes, str] | None = None):
    for sdt in [c for c in body if c.tag == f"{W}sdt" and _sdt_tag(c) == COVER_TAG]:
        body.remove(sdt)
    sdt = etree.Element(f"{W}sdt")
    pr = etree.SubElement(sdt, f"{W}sdtPr")
    etree.SubElement(pr, f"{W}alias").set(f"{W}val", "Promulga cover page")
    etree.SubElement(pr, f"{W}tag").set(f"{W}val", COVER_TAG)
    etree.SubElement(pr, f"{W}lock").set(f"{W}val", "sdtContentLocked")
    content = etree.SubElement(sdt, f"{W}sdtContent")
    code = vals.get("CLASSIFICATION_CODE") or "internal"
    fill, ink = CLASSIFICATION_COLORS.get(code, CLASSIFICATION_COLORS["internal"])
    if vals.get("CLASSIFICATION"):
        content.append(_band(vals["CLASSIFICATION"], fill, ink))
    if logo:
        content.append(_logo_paragraph(*logo_size_emu(logo[0])))
    content.append(_p(f"{vals.get('REF', '')}  ·  {vals.get('TYPE', '')}".upper(), bold=True, size=18,
                      color=MUTED, space_after=120))
    content.append(_p(vals.get("TITLE", ""), bold=True, size=48, color=PRIMARY, space_after=200))
    if vals.get("DESCRIPTION"):
        content.append(_p(vals["DESCRIPTION"], size=22, space_after=240))
    content.append(_table([[label, vals.get(key, "")] for key, label in labels], header=False,
                          widths=[2600, 6400]))
    content.append(_p("", space_after=240))
    content.append(_p("Version history", bold=True, size=26, color=PRIMARY, space_after=120))
    content.append(_history_table(history))
    content.append(_p("", space_after=240))
    if vals.get("COPYRIGHT"):
        content.append(_p(vals["COPYRIGHT"], size=18, color=PRIMARY, space_after=60))
    if vals.get("CLASSIFICATION"):
        content.append(_p(f"{vals['CLASSIFICATION']}: {CLASSIFICATION_NOTES.get(code, '')}", size=16,
                          color=MUTED, space_after=60))
    content.append(_p("This page is generated by Promulga and refreshed automatically; changes made to it are "
                      "overwritten.", size=16, color=MUTED, space_after=0))
    content.append(_p(page_break=True))
    body.insert(0, sdt)
