"""
Configuration module - Loads and validates environment variables.
"""
import os
import typing
from typing import Dict, Any, Optional, List, Set
from dataclasses import dataclass

from pydantic import Field, validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables with validation.
    Uses Pydantic for validation and provides defaults where appropriate.
    """
    # Server settings
    HOST: str = "127.0.0.1"
    PORT: int = 8765
    
    # WebSocket settings
    PING_INTERVAL: int = 30
    PING_TIMEOUT: int = 10
    HEARTBEAT_INTERVAL: int = 60
    MAX_MESSAGE_SIZE: int = 1024 * 1024  # 1MB
    MAX_QUEUE_SIZE: int = 32
    
    # Rate limiting
    RATE_LIMIT_MAX_MESSAGES: int = 10
    RATE_LIMIT_WINDOW_SECONDS: float = 1.0
    
    # Authentication
    JWT_PUBLIC_KEYS: Dict[str, str] = Field(default_factory=dict)
    JWT_ISSUER: Optional[str] = None
    JWT_AUDIENCE: Optional[str] = None
    JWT_CLOCK_SKEW_SECONDS: int = 30
    REQUIRED_SCOPE: Optional[str] = None
    
    # RabbitMQ settings
    RABBITMQ_HOST: str = "localhost"
    RABBITMQ_PORT: int = 5672
    RABBITMQ_VHOST: str = "/"
    RABBITMQ_USERNAME: Optional[str] = None
    RABBITMQ_PASSWORD: Optional[str] = None
    RABBITMQ_CONNECTION_TIMEOUT: int = 5
    RABBITMQ_CONNECTION_POOL_SIZE: int = 2
    RABBITMQ_CHANNEL_POOL_SIZE: int = 10
    RABBITMQ_PREFETCH_COUNT: int = 10
    MAX_REDELIVERY_COUNT: int = 3
    
    # Queue settings
    AUTOMATION_EXCHANGE: str = "automation"
    AUTOMATION_JOBS_QUEUE: str = "automation_jobs"
    AUTOMATION_RESULTS_QUEUE: str = "automation_results"
    DEAD_LETTER_EXCHANGE: str = "dead_letter"
    DEAD_LETTER_QUEUE: str = "dead_letter_queue"
    QUEUE_MESSAGE_TTL: int = 1000 * 60 * 60 * 24  # 24 hours
    
    # Metrics
    METRICS_HOST: str = "0.0.0.0"
    METRICS_PORT: int = 8000
    
    # Logging
    LOG_LEVEL: str = "INFO"
    
    # OpenTelemetry (optional)
    ENABLE_TRACING: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: Optional[str] = None
    OTEL_SERVICE_NAME: str = "websocket_server"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"
    
    @validator("JWT_PUBLIC_KEYS", pre=True)
    def build_jwt_keys(cls, v):
        """
        Build JWT public keys dictionary from environment variables or JSON string.
        Supports:
        - Dict (already parsed)
        - JSON string
        - JWT_PUBLIC_KEY_<kid> environment variables
        """
        import json

        if isinstance(v, dict):
            return v

        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                raise ValueError("JWT_PUBLIC_KEYS must be a JSON-encoded string or key-prefixed env vars.")

        # Fall back to JWT_PUBLIC_KEY_ prefixed env vars
        result = {}
        for key, value in os.environ.items():
            if key.startswith("JWT_PUBLIC_KEY_"):
                kid = key[len("JWT_PUBLIC_KEY_"):]
                result[kid] = value

        if not result:
            raise ValueError("No JWT public keys configured")
        return result

    
    @validator("LOG_LEVEL")
    def validate_log_level(cls, v):
        """Validate the log level is one of the accepted values."""
        allowed_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v not in allowed_levels:
            raise ValueError(f"LOG_LEVEL must be one of {allowed_levels}")
        return v
    
    def get_rabbitmq_url(self) -> str:
        """Generate RabbitMQ connection URL from settings."""
        credentials = ""
        if self.RABBITMQ_USERNAME and self.RABBITMQ_PASSWORD:
            credentials = f"{self.RABBITMQ_USERNAME}:{self.RABBITMQ_PASSWORD}@"
        
        vhost = "/" if not self.RABBITMQ_VHOST or self.RABBITMQ_VHOST == "/" else f"/{self.RABBITMQ_VHOST}"
        
        return f"amqp://{credentials}{self.RABBITMQ_HOST}:{self.RABBITMQ_PORT}{vhost}"


# Initialize settings once
settings = Settings()
