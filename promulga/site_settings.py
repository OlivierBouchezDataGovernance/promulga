"""Settings the administrator edits in the web interface (stored as JSON in the DB).

Look and feel (name, colours, logo, banner, footer), portal access, document
types, and the deadlines and alerts of the workflow. The workflow itself is
fixed; only its timing is configurable.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from .models import APPROVAL, DRAFT, REVIEW, REVISION, Setting

DEFAULTS: dict[str, Any] = {
    # Look and feel ---------------------------------------------------------
    "site_name": "Promulga",
    "tagline": "Governance documents: policies, standards and procedures",
    "color_primary": "#1e293b",     # slate 800, "the seal": headings, banner
    "color_primary_dark": "#0f172a",  # slate 900: navigation bar, footer
    "color_accent": "#2563eb",      # blue 600: buttons, links (5.2:1 on white)
    "color_success": "#047857",     # emerald 700: approved, published (5.5:1 on white)
    "color_text": "#0f172a",
    "color_background": "#f8fafc",
    "logo_asset": "",               # file name in DATA_DIR/assets, '' = text logo
    "banner_asset": "",             # optional background image of the portal banner
    "banner_html": "<h1>Governance documents</h1>\n<p>Find the official, approved version of our policies, "
                   "standards and procedures.</p>",
    "footer_html": "<p>Published with Promulga - governance document workflow.</p>",
    # Portal ---------------------------------------------------------------
    "portal_access": "public",      # public | login
    "portal_show_owner": True,
    # Documents --------------------------------------------------------------
    "doc_types": [
        {"name": "Policy", "prefix": "POL", "icon": "shield-check"},
        {"name": "Standard", "prefix": "STD", "icon": "ruler"},
        {"name": "Guideline", "prefix": "GDL", "icon": "compass"},
        {"name": "Procedure", "prefix": "PRC", "icon": "list-ordered"},
        {"name": "Work instruction", "prefix": "WIN", "icon": "wrench"},
        {"name": "Tutorial", "prefix": "TUT", "icon": "graduation-cap"},
        {"name": "Reference", "prefix": "REF", "icon": "library"},
    ],
    "template_asset": "",           # optional .docx copied as first revision of new documents
    # Deadlines and alerts ----------------------------------------------------
    "stage_days": {DRAFT: 28, REVIEW: 14, REVISION: 14, APPROVAL: 14},
    "reminder_before_days": 3,      # 0 = no "due soon" reminder
    "overdue_repeat_days": 1,       # resend overdue reminders every N days; 0 = once
    "reminder_hour": 8,             # local server hour at which reminders run
    "manager_digest": True,         # daily overdue summary to Document Managers
    "notify_on_publish": True,      # e-mail all participants when a version is published
    "review_months": 12,            # periodic review of published documents; 0 = none (per-document override)
    "review_reminder_days": 30,     # warn the owner N days before the periodic review is due
    # Integrations -------------------------------------------------------------
    "webhook_urls": [],             # https URLs receiving a JSON message at each workflow event
    "export_auto": False,           # push the public package after every publication
    # Internal bookkeeping (not shown in forms) -----------------------------
    "last_reminder_run": "",
    "export_pending": False,
    "last_export": "",
    "last_backup": "",
}

COLOR_KEYS = ["color_primary", "color_primary_dark", "color_accent", "color_success",
              "color_text", "color_background"]
THEME_KEYS = COLOR_KEYS + ["color_on_accent"]


def get_all(db: Session) -> dict[str, Any]:
    values = deepcopy(DEFAULTS)
    for row in db.query(Setting).all():
        try:
            values[row.key] = json.loads(row.value)
        except ValueError:
            continue
    # Older databases may miss a stage in stage_days.
    values["stage_days"] = {**DEFAULTS["stage_days"], **(values.get("stage_days") or {})}
    # Derived, never stored: a readable text colour on top of the accent colour.
    values["color_on_accent"] = readable_on(values["color_accent"])
    return values


def _luminance(hex_color: str) -> float:
    """Relative luminance (WCAG 2.x) of a #rrggbb colour."""
    channels = []
    for i in (1, 3, 5):
        c = int(hex_color[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def readable_on(background: str) -> str:
    """White or near-black, whichever contrasts most with `background`."""
    try:
        return "#ffffff" if contrast(background, "#ffffff") >= contrast(background, "#0f172a") else "#0f172a"
    except (ValueError, IndexError):
        return "#ffffff"


def doc_type_icon(settings: dict[str, Any], doc_type: str) -> str:
    for t in settings["doc_types"]:
        if t["name"] == doc_type:
            return t.get("icon") or "file-text"
    return "file-text"


def get(db: Session, key: str) -> Any:
    row = db.get(Setting, key)
    if row is None:
        return deepcopy(DEFAULTS.get(key))
    return json.loads(row.value)


def set_many(db: Session, values: dict[str, Any]) -> None:
    for key, value in values.items():
        if key not in DEFAULTS:
            raise KeyError(f"Unknown setting {key}")
        row = db.get(Setting, key)
        encoded = json.dumps(value)
        if row is None:
            db.add(Setting(key=key, value=encoded))
        else:
            row.value = encoded


def doc_type_prefix(settings: dict[str, Any], doc_type: str) -> str:
    for t in settings["doc_types"]:
        if t["name"] == doc_type:
            return t["prefix"]
    raise ValueError(f"Unknown document type: {doc_type}")
