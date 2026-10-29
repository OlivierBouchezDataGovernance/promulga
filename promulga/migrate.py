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
from alembic.runtime.migration import MigrationContext
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
    with engine.connect() as connection:
        sqlite = connection.dialect.name == "sqlite"
        if sqlite:
            # SQLite rebuilds tables to alter them ("batch" mode): foreign keys must be
            # off meanwhile, and this pragma only works outside a transaction.
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        with connection.begin():
            tables = set(inspect(connection).get_table_names())
            cfg = _config(connection)
            current = MigrationContext.configure(connection).get_current_revision()
            if "documents" in tables and current is None:     # a 0.1 database (or an interrupted first upgrade)
                command.stamp(cfg, BASELINE)
            command.upgrade(cfg, "head")
        if sqlite:
            problems = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise RuntimeError(f"Foreign key problems after migration: {problems[:5]}")
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


def new_revision(engine, message: str) -> None:
    with engine.begin() as connection:
        command.revision(_config(connection), message=message, autogenerate=True)
