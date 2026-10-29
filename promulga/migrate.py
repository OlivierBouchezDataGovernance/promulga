"""Database schema migrations (Alembic), applied automatically at start-up.

- A new, empty database is created by running every migration.
- A database created by Promulga 0.1 (tables but no ``alembic_version``) is
  first marked as being at the baseline revision, then upgraded.
- Developers create a new migration after changing :mod:`promulga.models` with
  ``promulga db-revision "what changed"`` and review the generated file.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import inspect

BASELINE = "0001_baseline"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _config(connection) -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.attributes["connection"] = connection
    return cfg


def upgrade(engine) -> None:
    logging.getLogger("alembic").setLevel(logging.WARNING)
    with engine.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        cfg = _config(connection)
        if "documents" in tables and "alembic_version" not in tables:
            command.stamp(cfg, BASELINE)
        command.upgrade(cfg, "head")


def new_revision(engine, message: str) -> None:
    with engine.begin() as connection:
        command.revision(_config(connection), message=message, autogenerate=True)
