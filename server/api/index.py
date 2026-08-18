"""Vercel serverless entrypoint.

The Python runtime imports this module and looks for an ASGI callable named
`app`, so this file exists only to expose one. Every route, middleware and
exception handler is still assembled in `app.main` — nothing about the
application is defined here, which keeps `uvicorn app.main:app` and the
deployed function running the identical object.

`vercel.json` rewrites every path to this function, so FastAPI still sees the
original request path (`/api/v1/...`) and its own router does the matching.
"""

from app.main import app

__all__ = ["app"]
