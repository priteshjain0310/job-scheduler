"""
API module.
Contains FastAPI application, routes, and middleware.
"""

from src.api.main import create_app, run

__all__ = ["create_app", "run"]
