"""
Metrics module - Prometheus metrics configuration and registration.
"""
import asyncio
import time
from typing import Dict, Optional
from aiohttp import web
from prometheus_client import (
    Counter, 
    Gauge, 
    Histogram,
    Summary,
    REGISTRY,
    generate_latest,
    CONTENT_TYPE_LATEST
)

from websocket.logger import get_logger
from websocket.config import settings


logger = get_logger(__name__)


# Connection metrics
ACTIVE_CONNECTIONS = Gauge(
    "ws_active_connections", 
    "Number of active WebSocket connections"
)

CONNECTION_ERRORS = Counter(
    "ws_connection_errors_total", 
    "Total count of WebSocket connection errors",
    ["type"]
)

# Message metrics
MESSAGE_COUNT = Counter(
    "ws_messages_total", 
    "Total count of WebSocket messages received"
)

MESSAGE_BYTES = Counter(
    "ws_message_bytes_total", 
    "Total bytes of WebSocket messages received"
)

MESSAGE_ROUTING_ERROR = Counter(
    "ws_message_routing_errors_total", 
    "Total count of message routing errors",
    ["error"]
)

# Performance metrics
LATENCY_HISTOGRAM = Histogram(
    "ws_message_processing_seconds", 
    "Time spent processing WebSocket messages",
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0]
)

# Queue metrics
QUEUE_PUBLISH_ATTEMPTS = Counter(
    "ws_queue_publish_attempts_total", 
    "Total number of attempted message publications to queues",
    ["queue"]
)

QUEUE_PUBLISH_FAILURES = Counter(
    "ws_queue_publish_failures_total", 
    "Total number of failed message publications to queues",
    ["queue"]
)

QUEUE_CONSUME_ERRORS = Counter(
    "ws_queue_consume_errors_total", 
    "Total number of errors while consuming from queues",
    ["queue"]
)

QUEUE_MESSAGE_PROCESSING_TIME = Summary(
    "ws_queue_message_processing_seconds", 
    "Time spent processing queue messages"
)

# Authentication metrics
AUTH_FAILURES = Counter(
    "ws_auth_failures_total", 
    "Total count of authentication failures",
    ["reason"]
)

AUTH_SUCCESSES = Counter(
    "ws_auth_successes_total", 
    "Total count of successful authentications"
)


async def health_handler(request):
    """HTTP handler for health check endpoint."""
    # In a real application, you might check dependencies like RabbitMQ
    return web.json_response({"status": "ok", "timestamp": time.time()})

async def metrics_handler(request):
    """
    Metrics handler for aiohttp web server.
    
    Args:
        request: The aiohttp request object
        
    Returns:
        aiohttp Response with Prometheus metrics
    """
    data = generate_latest()
    return web.Response(body=data, headers={'Content-Type': CONTENT_TYPE_LATEST})


async def register_metrics_endpoint() -> None:
    """
    Start the metrics HTTP endpoint using aiohttp.
    """
    app = web.Application()
    app.router.add_get("/metrics", metrics_handler)
    app.router.add_get("/health", health_handler)

    try:
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, settings.METRICS_HOST, settings.METRICS_PORT)
        await site.start()
        
        logger.info("Metrics endpoint started", 
                   host=settings.METRICS_HOST, 
                   port=settings.METRICS_PORT)
        
        # Keep the server running
        while True:
            await asyncio.sleep(3600)  # Sleep for a long time
    except Exception as e:
        logger.error("Error starting metrics endpoint", error=str(e))
        raise