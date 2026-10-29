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
    "color_primary": "#132749",     # navy: header, footer, headings
    "color_primary_dark": "#0d1b33",
    "color_accent": "#d4af5c",      # gold: highlights, primary buttons
    "color_success": "#2e8b57",     # green: approve, published
    "color_text": "#16213e",
    "color_background": "#ffffff",
    "logo_asset": "",               # file name in DATA_DIR/assets, '' = text logo
    "banner_asset": "",             # optional background image of the portal banner
    "banner_html": "<h1>Governance documents</h1>\n<p>Find the official, approved version of our policies, "
                   "standards and procedures.</p>",
    "footer_html": "<p>Published with Promulga - open-source governance document workflow.</p>",
    # Portal ---------------------------------------------------------------
    "portal_access": "public",      # public | login
    "portal_show_owner": True,
    # Documents --------------------------------------------------------------
    "doc_types": [
        {"name": "Policy", "prefix": "POL"},
        {"name": "Standard", "prefix": "STD"},
        {"name": "Procedure", "prefix": "PRC"},
        {"name": "Guideline", "prefix": "GDL"},
    ],
    "template_asset": "",           # optional .docx copied as first revision of new documents
    # Deadlines and alerts ----------------------------------------------------
    "stage_days": {DRAFT: 28, REVIEW: 14, REVISION: 14, APPROVAL: 14},
    "reminder_before_days": 3,      # 0 = no "due soon" reminder
    "overdue_repeat_days": 1,       # resend overdue reminders every N days; 0 = once
    "reminder_hour": 8,             # local server hour at which reminders run
    "manager_digest": True,         # daily overdue summary to Document Managers
    "notify_on_publish": True,      # e-mail all participants when a version is published
    # Internal bookkeeping (not shown in forms) -----------------------------
    "last_reminder_run": "",
}

COLOR_KEYS = ["color_primary", "color_primary_dark", "color_accent", "color_success",
              "color_text", "color_background"]


def get_all(db: Session) -> dict[str, Any]:
    values = deepcopy(DEFAULTS)
    for row in db.query(Setting).all():
        try:
            values[row.key] = json.loads(row.value)
        except ValueError:
            continue
    # Older databases may miss a stage in stage_days.
    values["stage_days"] = {**DEFAULTS["stage_days"], **(values.get("stage_days") or {})}
    return values


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
