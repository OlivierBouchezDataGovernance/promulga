"""ASGI entry point: ``uvicorn promulga.main:app``."""

from .web.app import create_app

app = create_app()
