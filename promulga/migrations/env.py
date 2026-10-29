"""Alembic environment, driven programmatically by :mod:`promulga.migrate` (no alembic.ini)."""

from alembic import context

from promulga import models  # noqa: F401  (registers the tables)
from promulga.db import Base

config = context.config
connection = config.attributes["connection"]

context.configure(
    connection=connection,
    target_metadata=Base.metadata,
    render_as_batch=True,          # SQLite cannot ALTER most columns: use batch mode
    compare_type=True,
)

with context.begin_transaction():
    context.run_migrations()
