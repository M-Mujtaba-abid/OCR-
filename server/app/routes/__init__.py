"""v1 API router aggregator.

Every feature router mounts here; `main.py` includes only this one, so adding a
feature never means touching the application factory.
"""

from fastapi import APIRouter

from app.routes.approval_routes import router as approval_router
from app.routes.auth_routes import router as auth_router
from app.routes.company_routes import router as company_router
from app.routes.config_routes import router as config_router
from app.routes.invoice_routes import router as invoice_router
from app.routes.notification_routes import router as notification_router
from app.routes.odoo_routes import router as odoo_router
from app.routes.platform_routes import router as platform_router
from app.routes.user_routes import router as user_router

api_router = APIRouter()
api_router.include_router(config_router)
api_router.include_router(auth_router)
api_router.include_router(user_router)
api_router.include_router(company_router)
api_router.include_router(invoice_router)
api_router.include_router(approval_router)
api_router.include_router(notification_router)
api_router.include_router(odoo_router)
api_router.include_router(platform_router)

__all__ = ["api_router"]
