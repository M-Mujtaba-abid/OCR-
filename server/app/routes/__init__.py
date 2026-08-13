"""v1 API router aggregator.

Every feature router mounts here; `main.py` includes only this one, so adding a
feature never means touching the application factory.
"""

from fastapi import APIRouter

from app.routes.auth_routes import router as auth_router

api_router = APIRouter()
api_router.include_router(auth_router)

__all__ = ["api_router"]
