"""ASGI entrypoint: uvicorn app:app --workers 1."""

from hub import create_app

app = create_app()
