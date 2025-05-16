"""
Distributed tracing module - OpenTelemetry setup and instrumentation.
"""
from typing import Optional
import contextlib

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.instrumentation.aio_pika import AioPikaInstrumentor
from opentelemetry.instrumentation.aiohttp_client import AioHttpClientInstrumentor
from opentelemetry.instrumentation.asyncio import AsyncioInstrumentor

from websocket.config import settings
from websocket.logger import get_logger


logger = get_logger(__name__)


class TracingManager:
    """
    Manages OpenTelemetry tracing configuration and span creation.
    """
    def __init__(self):
        """Initialize the tracing manager."""
        self.enabled = settings.ENABLE_TRACING
        self.tracer = None
        
        if not self.enabled:
            logger.info("Distributed tracing is disabled")
            return
        
        try:
            # Configure the tracer
            resource = Resource(attributes={
                SERVICE_NAME: settings.OTEL_SERVICE_NAME
            })
            
            provider = TracerProvider(resource=resource)
            
            # Set up OTLP exporter if endpoint is configured
            if settings.OTEL_EXPORTER_OTLP_ENDPOINT:
                otlp_exporter = OTLPSpanExporter(
                    endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT
                )
                span_processor = BatchSpanProcessor(otlp_exporter)
                provider.add_span_processor(span_processor)
            
            # Set global tracer provider
            trace.set_tracer_provider(provider)
            
            # Get a tracer instance
            self.tracer = trace.get_tracer(settings.OTEL_SERVICE_NAME)
            
            # Instrument libraries
            self._instrument_libraries()
            
            logger.info("Distributed tracing initialized", 
                       service_name=settings.OTEL_SERVICE_NAME)
        
        except Exception as e:
            logger.error("Failed to initialize tracing", error=str(e))
            self.enabled = False
    
    def _instrument_libraries(self):
        """
        Apply auto-instrumentation to relevant libraries.
        """
        # Instrument asyncio
        AsyncioInstrumentor().instrument()
        
        # Instrument aio_pika (RabbitMQ client)
        AioPikaInstrumentor().instrument()
        
        # Instrument aiohttp client
        AioHttpClientInstrumentor().instrument()
        
        logger.debug("Libraries instrumented for tracing")
    
    @contextlib.contextmanager
    def start_span(self, name, attributes=None, context=None, kind=None):
        """
        Start a new span or use a no-op context if tracing is disabled.
        
        Args:
            name: Name of the span
            attributes: Optional span attributes
            context: Optional parent context
            kind: Optional span kind
            
        Returns:
            A span context manager
        """
        if not self.enabled or not self.tracer:
            # Return no-op context manager if tracing is disabled
            @contextlib.contextmanager
            def noop_span():
                yield None
            return noop_span()
        
        # Start a real span
        return self.tracer.start_as_current_span(
            name, 
            attributes=attributes or {}, 
            context=context,
            kind=kind
        )
    
    def inject_context_into_headers(self, headers):
        """
        Inject the current trace context into request headers.
        
        Args:
            headers: Dict-like headers object to inject context into
            
        Returns:
            The headers with injected context
        """
        if not self.enabled:
            return headers
        
        TraceContextTextMapPropagator().inject(headers)
        return headers
    
    def extract_context_from_headers(self, headers):
        """
        Extract trace context from headers.
        
        Args:
            headers: Dict-like headers object containing context
            
        Returns:
            The extracted context
        """
        if not self.enabled:
            return None
        
        return TraceContextTextMapPropagator().extract(carrier=headers)


# Create a global tracing manager instance
tracing = TracingManager()
