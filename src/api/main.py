"""
FastAPI application entry point.
"""

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Query, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.rate_limit import create_rate_limit_middleware
from src.api.routes import auth_router, health_router, jobs_router
from src.api.websocket import websocket_handler
from src.config import get_settings
from src.db import close_db, init_db
from src.observability.logging import setup_logging
from src.observability.metrics import setup_metrics
from src.observability.tracing import instrument_fastapi, setup_tracing

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    
    Handles startup and shutdown events.
    """
    # Startup
    setup_logging()
    setup_metrics()
    setup_tracing()
    await init_db()

    logger.info("Application started")

    yield

    # Shutdown
    await close_db()
    logger.info("Application shutdown")


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.
    
    Returns:
        FastAPI: The configured application instance.
    """
    settings = get_settings()

    app = FastAPI(
        title="Job Scheduler API",
        description="Production-minded distributed job queue with PostgreSQL",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Add rate limiting middleware
    app.add_middleware(
        BaseHTTPMiddleware,
        dispatch=create_rate_limit_middleware(app),
    )

    # Include routers
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(jobs_router)

    # WebSocket endpoint
    @app.websocket("/ws/jobs")
    async def jobs_websocket(
        websocket: WebSocket,
        tenant_id: str = Query(...),
    ):
        """
        WebSocket endpoint for real-time job updates.
        
        Clients should provide their tenant_id as a query parameter.
        After connecting, clients can subscribe to specific jobs.
        """
        await websocket_handler(websocket, tenant_id)

    # Instrument with OpenTelemetry
    instrument_fastapi(app)

    return app


def run() -> None:
    """Run the API server."""
    settings = get_settings()
    app = create_app()

    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


# Create app instance for uvicorn
app = create_app()


if __name__ == "__main__":
    run()
