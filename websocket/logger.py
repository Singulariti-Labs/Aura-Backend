"""
Logger module - Sets up structured logging with structlog.
"""
import sys
import time
import logging
import os
from typing import Dict, Any, Optional

import structlog


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Get a configured structured logger instance.
    
    Args:
        name: The logger name (typically __name__)
        
    Returns:
        A configured structlog logger instance
    """
    # Configure standard logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )

    # Set up structlog processors
    processors = [
        # Add context from structlog.threadlocal
        structlog.contextvars.merge_contextvars,
        # Add the logger name
        structlog.stdlib.add_logger_name,
        # Add log level
        structlog.stdlib.add_log_level,
        # Add timestamp
        structlog.processors.TimeStamper(fmt="iso"),
        # Format the exception
        structlog.processors.ExceptionPrettyPrinter(),
        # Process any key-value pairs from kwargs
        structlog.processors.StackInfoRenderer(),
        # Format as JSON
        structlog.processors.JSONRenderer(),
    ]

    # Set log level from environment variable
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    logging.getLogger().setLevel(getattr(logging, log_level))

    # Configure structlog
    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Create and return the logger
    return structlog.get_logger(name)


class RequestLogger:
    """
    Context manager for logging requests with timing information.
    """
    def __init__(self, logger: structlog.stdlib.BoundLogger, **kwargs) -> None:
        """
        Initialize the request logger.
        
        Args:
            logger: The structlog logger to use
            **kwargs: Additional context fields to log
        """
        self.logger = logger
        self.context = kwargs
        self.start_time = None
    
    def __enter__(self) -> 'RequestLogger':
        """Enter the context manager and log the start of the request."""
        self.start_time = time.time()
        self.logger.info("Request started", **self.context)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """
        Exit the context manager and log the completion of the request.
        
        Args:
            exc_type: Exception type if an exception occurred
            exc_val: Exception value if an exception occurred
            exc_tb: Exception traceback if an exception occurred
        """
        duration = (time.time() - self.start_time) * 1000  # ms
        
        if exc_type:
            # Log exception details
            self.logger.error(
                "Request failed",
                duration_ms=duration,
                error=str(exc_val),
                exception_type=exc_type.__name__,
                **self.context,
            )
        else:
            # Log successful completion
            self.logger.info(
                "Request completed",
                duration_ms=duration,
                **self.context,
            )


# Global request ID context manager
class RequestIdContext:
    """
    Context manager for setting a request ID in structlog's context vars.
    """
    def __init__(self, request_id: str) -> None:
        """
        Initialize with a request ID.
        
        Args:
            request_id: The request identifier
        """
        self.request_id = request_id
        self._token = None
    
    def __enter__(self) -> 'RequestIdContext':
        """
        Enter the context and bind the request ID to the log context.
        
        Returns:
            Self for context manager interface
        """
        self._token = structlog.contextvars.bind_contextvars(request_id=self.request_id)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """
        Exit the context and reset the log context.
        
        Args:
            exc_type: Exception type if an exception occurred
            exc_val: Exception value if an exception occurred
            exc_tb: Exception traceback if an exception occurred
        """
        structlog.contextvars.reset_contextvars(self._token)
