"""
Queues module - Manages RabbitMQ connections, publishers, and consumers.
"""
import json
import asyncio
import time
from typing import Dict, Any, Callable, Awaitable, Optional
from functools import wraps

import aio_pika
from aio_pika import Message, DeliveryMode, ExchangeType
from aio_pika.abc import AbstractRobustConnection
from aio_pika.pool import Pool

from websocket.logger import get_logger
from websocket.config import settings
from websocket.metrics import (
    QUEUE_PUBLISH_ATTEMPTS,
    QUEUE_PUBLISH_FAILURES,
    QUEUE_CONSUME_ERRORS,
    QUEUE_MESSAGE_PROCESSING_TIME
)


logger = get_logger(__name__)


class QueueConnectionError(Exception):
    """Exception raised when connection to RabbitMQ fails."""
    pass


def with_retry(max_retries: int = 3, initial_backoff: float = 0.1):
    """
    Decorator for retrying async operations with exponential backoff.
    
    Args:
        max_retries: Maximum number of retry attempts
        initial_backoff: Initial backoff time in seconds
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            retries = 0
            backoff = initial_backoff
            
            while True:
                try:
                    return await func(*args, **kwargs)
                except (aio_pika.AMQPConnectionError, aio_pika.AMQPChannelError) as e:
                    retries += 1
                    if retries > max_retries:
                        logger.error(
                            f"Max retries reached for {func.__name__}",
                            retries=retries,
                            error=str(e)
                        )
                        raise
                    
                    wait_time = backoff * (2 ** (retries - 1))  # Exponential backoff
                    logger.warning(
                        f"Retrying {func.__name__} after error",
                        retry=retries,
                        wait_time=wait_time,
                        error=str(e)
                    )
                    await asyncio.sleep(wait_time)
        return wrapper
    return decorator


class QueueManager:
    """
    Manages RabbitMQ connections, channels, and message handling.
    Implements connection pooling, retry logic, and robust error handling.
    """
    def __init__(self) -> None:
        """Initialize the queue manager."""
        self.connection_pool: Optional[Pool] = None
        self.channel_pool: Optional[Pool] = None
        self._closing = False
    
    async def connect(self) -> None:
        """
        Establish connection to RabbitMQ server with connection pooling.
        
        Raises:
            QueueConnectionError: If connection fails
        """
        try:
            # Create a connection factory for the pool
            connection_factory = self._get_connection_factory()
            
            # Create connection pool
            self.connection_pool = Pool(
                connection_factory,
                max_size=settings.RABBITMQ_CONNECTION_POOL_SIZE
            )
            
            # Create channel factory that gets a connection from the pool
            async def channel_factory():
                async with self.connection_pool.acquire() as connection:
                    return await connection.channel()
            
            # Create channel pool
            self.channel_pool = Pool(
                channel_factory,
                max_size=settings.RABBITMQ_CHANNEL_POOL_SIZE
            )
            
            # Test the connection
            async with self.connection_pool.acquire() as connection:
                logger.info("Connected to RabbitMQ", 
                           server=settings.RABBITMQ_HOST,
                           port=settings.RABBITMQ_PORT)
                
            # Declare exchanges and queues
            await self._setup_exchanges_and_queues()
            
        except aio_pika.AMQPException as e:
            logger.error("Failed to connect to RabbitMQ", error=str(e))
            raise QueueConnectionError(f"Failed to connect to RabbitMQ: {str(e)}")
        except Exception as e:
            logger.error("Unexpected error connecting to RabbitMQ", error=str(e))
            raise QueueConnectionError(f"Unexpected error: {str(e)}")
    
    def _get_connection_factory(self):
        """
        Create a connection factory function for the connection pool.
        
        Returns:
            Async function that creates a new RabbitMQ connection
        """
        async def connection_factory() -> AbstractRobustConnection:
            # Build connection string with credentials if provided
            connection_string = f"amqp://"
            
            if settings.RABBITMQ_USERNAME and settings.RABBITMQ_PASSWORD:
                connection_string += f"{settings.RABBITMQ_USERNAME}:{settings.RABBITMQ_PASSWORD}@"
            
            connection_string += f"{settings.RABBITMQ_HOST}:{settings.RABBITMQ_PORT}/"
            
            if settings.RABBITMQ_VHOST:
                connection_string += settings.RABBITMQ_VHOST
            
            # Create robust connection that will auto-reconnect
            return await aio_pika.connect_robust(
                connection_string,
                timeout=settings.RABBITMQ_CONNECTION_TIMEOUT,
                client_properties={
                    "connection_name": "websocket_server",
                    "product": "websocket_server",
                    "version": "1.0.0",
                }
            )
        
        return connection_factory
    
    @with_retry(max_retries=3, initial_backoff=0.5)
    async def _setup_exchanges_and_queues(self) -> None:
        """
        Set up exchanges and queues with appropriate configurations.
        Uses a retry decorator for resilience.
        """
        async with self.channel_pool.acquire() as channel:
            # Set QoS for fair dispatching of messages
            await channel.set_qos(prefetch_count=settings.RABBITMQ_PREFETCH_COUNT)
            
            # Create automation exchange
            automation_exchange = await channel.declare_exchange(
                settings.AUTOMATION_EXCHANGE,
                ExchangeType.TOPIC,
                durable=True
            )
            
            # Create automation jobs queue
            jobs_queue = await channel.declare_queue(
                settings.AUTOMATION_JOBS_QUEUE,
                durable=True,
                arguments={
                    "x-message-ttl": settings.QUEUE_MESSAGE_TTL,
                    "x-dead-letter-exchange": settings.DEAD_LETTER_EXCHANGE,
                    "x-dead-letter-routing-key": settings.AUTOMATION_JOBS_QUEUE
                }
            )
            await jobs_queue.bind(automation_exchange, routing_key="jobs.#")
            
            # Create automation results queue
            results_queue = await channel.declare_queue(
                settings.AUTOMATION_RESULTS_QUEUE,
                durable=True,
                arguments={
                    "x-message-ttl": settings.QUEUE_MESSAGE_TTL,
                    "x-dead-letter-exchange": settings.DEAD_LETTER_EXCHANGE,
                    "x-dead-letter-routing-key": settings.AUTOMATION_RESULTS_QUEUE
                }
            )
            await results_queue.bind(automation_exchange, routing_key="results.#")
            
            # Create dead letter exchange
            dead_letter_exchange = await channel.declare_exchange(
                settings.DEAD_LETTER_EXCHANGE,
                ExchangeType.TOPIC,
                durable=True
            )
            
            # Create dead letter queue
            dead_letter_queue = await channel.declare_queue(
                settings.DEAD_LETTER_QUEUE,
                durable=True
            )
            await dead_letter_queue.bind(dead_letter_exchange, routing_key="#")
            
            logger.info("Exchanges and queues set up successfully")
    
    @with_retry(max_retries=3, initial_backoff=0.5)
    async def publish_automation_job(self, message: Dict[str, Any]) -> None:
        """
        Publish an automation job message to RabbitMQ.
        
        Args:
            message: The automation job message to publish
            
        Raises:
            Exception: If publishing fails after retries
        """
        QUEUE_PUBLISH_ATTEMPTS.labels(queue=settings.AUTOMATION_JOBS_QUEUE).inc()
        
        try:
            async with self.channel_pool.acquire() as channel:
                exchange = await channel.get_exchange(settings.AUTOMATION_EXCHANGE)
                
                # Prepare message with properties
                request_id = message.get("request_id", "unknown")
                headers = {
                    "request_id": request_id,
                    "user_id": message.get("user_id", "unknown"),
                    "timestamp": int(time.time())
                }
                
                # Convert message to JSON and create AMQP message
                message_body = json.dumps(message).encode()
                amqp_message = Message(
                    body=message_body,
                    content_type="application/json",
                    delivery_mode=DeliveryMode.PERSISTENT,
                    message_id=request_id,
                    timestamp=int(time.time()),
                    headers=headers
                )
                
                # Publish message
                await exchange.publish(
                    amqp_message,
                    routing_key=f"jobs.{message.get('user_id', 'unknown')}"
                )
                
                logger.info("Published automation job", 
                           request_id=request_id,
                           user_id=message.get("user_id", "unknown"))
                
        except Exception as e:
            QUEUE_PUBLISH_FAILURES.labels(queue=settings.AUTOMATION_JOBS_QUEUE).inc()
            logger.error("Failed to publish automation job", 
                        error=str(e),
                        request_id=message.get("request_id", "unknown"))
            raise
    
    async def consume_automation_results(
        self, callback: Callable[[Dict[str, Any]], Awaitable[None]]
    ) -> None:
        """
        Consume messages from the automation results queue.
        
        Args:
            callback: Async function to call with each message
            
        Raises:
            Exception: If consuming fails
        """
        async with self.channel_pool.acquire() as channel:
            # Set QoS for fair dispatching
            await channel.set_qos(prefetch_count=settings.RABBITMQ_PREFETCH_COUNT)
            
            # Get the queue
            queue = await channel.get_queue(settings.AUTOMATION_RESULTS_QUEUE)
            
            # Define message handler
            async def message_handler(message: aio_pika.IncomingMessage) -> None:
                async with message.process():
                    start_time = time.time()
                    
                    try:
                        # Parse message body
                        body = message.body.decode()
                        data = json.loads(body)
                        
                        # Process message
                        await callback(data)
                        
                    except json.JSONDecodeError as e:
                        QUEUE_CONSUME_ERRORS.labels(queue=settings.AUTOMATION_RESULTS_QUEUE).inc()
                        logger.error("Invalid JSON in message", 
                                    error=str(e),
                                    message_id=message.message_id)
                        # Reject message
                        await message.reject(requeue=False)
                        
                    except Exception as e:
                        QUEUE_CONSUME_ERRORS.labels(queue=settings.AUTOMATION_RESULTS_QUEUE).inc()
                        logger.error("Error processing result message", 
                                    error=str(e),
                                    message_id=message.message_id)
                        # Reject and requeue message if it's not been redelivered too many times
                        redelivered_count = message.headers.get("x-redelivered-count", 0)
                        if redelivered_count < settings.MAX_REDELIVERY_COUNT:
                            # Update redelivery count and requeue
                            message.headers["x-redelivered-count"] = redelivered_count + 1
                            await message.reject(requeue=True)
                        else:
                            # Max redeliveries reached, dead-letter the message
                            logger.warning("Message exceeded max redeliveries", 
                                         message_id=message.message_id)
                            await message.reject(requeue=False)
                    finally:
                        # Record message processing time
                        processing_time = time.time() - start_time
                        QUEUE_MESSAGE_PROCESSING_TIME.observe(processing_time)
            
            # Set up consumer
            logger.info("Starting automation results consumer")
            await queue.consume(message_handler)
    
    async def close(self) -> None:
        """
        Close all connections and channels gracefully.
        """
        self._closing = True
        
        if self.channel_pool:
            logger.info("Closing channel pool")
            await self.channel_pool.close()
            self.channel_pool = None
        
        if self.connection_pool:
            logger.info("Closing connection pool")
            await self.connection_pool.close()
            self.connection_pool = None
        
        logger.info("RabbitMQ connections closed")
