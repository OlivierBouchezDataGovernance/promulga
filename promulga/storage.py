"""File storage on the local disk (a Docker volume in production).

Files are stored under DATA_DIR/files/<document id>/ with a random name; the
original file name is kept in the database only. Paths in the database are
relative to DATA_DIR so the data folder can be moved.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from pathlib import Path

from .config import get_config

# Accepted working files (check-in). PDFs are accepted as-is; office files are
# converted to PDF at publication.
ALLOWED_EXTENSIONS = {".docx", ".doc", ".odt", ".rtf", ".pdf", ".xlsx", ".pptx", ".md", ".txt"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}


def safe_filename(name: str) -> str:
    name = Path(name or "file").name
    name = re.sub(r"[^\w.\- ()]+", "_", name).strip() or "file"
    return name[:200]


def extension(name: str) -> str:
    return Path(name or "").suffix.lower()


def save_bytes(subdir: str, original_name: str, data: bytes) -> tuple[str, str]:
    """Stores bytes; returns (relative path, sha256)."""
    cfg = get_config()
    folder = cfg.files_dir / subdir
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{secrets.token_hex(8)}{extension(original_name)}"
    target.write_bytes(data)
    return target.relative_to(cfg.data_dir).as_posix(), hashlib.sha256(data).hexdigest()


def absolute(relative_path: str) -> Path:
    cfg = get_config()
    path = (cfg.data_dir / relative_path).resolve()
    if cfg.data_dir not in path.parents:
        raise ValueError("Path outside the data directory")
    return path


def read_bytes(relative_path: str) -> bytes:
    return absolute(relative_path).read_bytes()


def save_asset(original_name: str, data: bytes) -> str:
    """Stores a site image (logo, banner, template); returns its file name."""
    ext = extension(original_name)
    name = f"{secrets.token_hex(6)}{ext}"
    (get_config().assets_dir / name).write_bytes(data)
    return name


def asset_path(name: str) -> Path | None:
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    path = get_config().assets_dir / name
    return path if path.is_file() else None
