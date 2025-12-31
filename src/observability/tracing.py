"""
OpenTelemetry tracing setup.
"""

from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import Tracer

from src.config import get_settings

# Global tracer instance
_tracer: Tracer | None = None


def setup_tracing(enable_console_export: bool = False) -> Tracer:
    """
    Set up OpenTelemetry tracing.
    
    Args:
        enable_console_export: If True, also export spans to console.
        
    Returns:
        Tracer: The tracer instance.
    """
    global _tracer

    settings = get_settings()

    # Create resource with service info
    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": "1.0.0",
        }
    )

    # Create tracer provider
    provider = TracerProvider(resource=resource)

    # Add OTLP exporter
    try:
        otlp_exporter = OTLPSpanExporter(
            endpoint=settings.otel_exporter_otlp_endpoint,
            insecure=True,
        )
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    except Exception:
        # OTLP exporter not available, skip
        pass

    # Optionally add console exporter for debugging
    if enable_console_export:
        provider.add_span_processor(
            BatchSpanProcessor(ConsoleSpanExporter())
        )

    # Set the global tracer provider
    trace.set_tracer_provider(provider)

    # Get tracer
    _tracer = trace.get_tracer(settings.otel_service_name)

    return _tracer


def instrument_fastapi(app: Any) -> None:
    """
    Instrument FastAPI application with OpenTelemetry.
    
    Args:
        app: The FastAPI application instance.
    """
    FastAPIInstrumentor.instrument_app(app)


def instrument_sqlalchemy(engine: Any) -> None:
    """
    Instrument SQLAlchemy engine with OpenTelemetry.
    
    Args:
        engine: The SQLAlchemy engine instance.
    """
    SQLAlchemyInstrumentor().instrument(engine=engine)


def get_tracer() -> Tracer:
    """
    Get the tracer instance.
    
    Returns:
        Tracer: The tracer instance.
        
    Raises:
        RuntimeError: If tracing is not set up.
    """
    global _tracer
    if _tracer is None:
        _tracer = setup_tracing()
    return _tracer


def create_span(name: str, **attributes: Any) -> Any:
    """
    Create a new span with the given name and attributes.
    
    Args:
        name: Span name.
        **attributes: Span attributes.
        
    Returns:
        A context manager for the span.
    """
    tracer = get_tracer()
    span = tracer.start_as_current_span(name)

    # Set attributes if provided
    current_span = trace.get_current_span()
    for key, value in attributes.items():
        if value is not None:
            current_span.set_attribute(key, str(value))

    return span
