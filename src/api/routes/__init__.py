"""
API routes module.
"""

from src.api.routes.auth import router as auth_router
from src.api.routes.health import router as health_router
from src.api.routes.jobs import router as jobs_router

__all__ = ["jobs_router", "auth_router", "health_router"]
