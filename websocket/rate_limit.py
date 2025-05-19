"""
Rate limiting module - Handles rate limiting for client connections.
"""
import time
from typing import Dict, Any, Optional
from collections import deque
import threading


class RateLimiter:
    """
    Token bucket rate limiter for WebSocket connections.
    Controls the rate of messages from clients to prevent abuse.
    """
    def __init__(self, client_id: str, max_messages: int, time_window: float) -> None:
        """
        Initialize a rate limiter for a specific client.
        
        Args:
            client_id: The client identifier
            max_messages: Maximum number of messages allowed in time window
            time_window: Time window in seconds
        """
        self.client_id = client_id
        self.max_messages = max_messages
        self.time_window = time_window
        self.message_timestamps: deque = deque(maxlen=max_messages)
        self.lock = threading.RLock()
    
    def allow_message(self) -> bool:
        """
        Check if a new message is allowed based on rate limit.
        
        Returns:
            True if message is allowed, False if rate limit exceeded
        """
        with self.lock:
            current_time = time.time()
            
            # Clean up old timestamps
            while self.message_timestamps and (current_time - self.message_timestamps[0]) > self.time_window:
                self.message_timestamps.popleft()
            
            # Check if we're at the limit
            if len(self.message_timestamps) >= self.max_messages:
                return False
            
            # Add current timestamp
            self.message_timestamps.append(current_time)
            return True


# Global rate limiter registry (for application-wide limits, e.g., per IP address)
class GlobalRateLimiter:
    """
    Application-wide rate limiter that can track multiple identifiers.
    Useful for limiting by IP address or other global identifiers.
    """
    def __init__(self, max_messages: int, time_window: float) -> None:
        """
        Initialize the global rate limiter.
        
        Args:
            max_messages: Maximum number of messages allowed in time window
            time_window: Time window in seconds
        """
        self.max_messages = max_messages
        self.time_window = time_window
        self.limiters: Dict[str, RateLimiter] = {}
        self.lock = threading.RLock()
    
    def get_limiter(self, identifier: str) -> RateLimiter:
        """
        Get or create a rate limiter for the given identifier.
        
        Args:
            identifier: The rate limit identifier (e.g., IP address)
            
        Returns:
            A rate limiter instance for the identifier
        """
        with self.lock:
            if identifier not in self.limiters:
                self.limiters[identifier] = RateLimiter(
                    client_id=identifier,
                    max_messages=self.max_messages,
                    time_window=self.time_window
                )
            return self.limiters[identifier]
    
    def allow_message(self, identifier: str) -> bool:
        """
        Check if a new message is allowed for the given identifier.
        
        Args:
            identifier: The rate limit identifier (e.g., IP address)
            
        Returns:
            True if message is allowed, False if rate limit exceeded
        """
        limiter = self.get_limiter(identifier)
        return limiter.allow_message()
    
    def cleanup(self) -> None:
        """
        Clean up limiters that haven't been used recently.
        Should be called periodically to prevent memory leaks.
        """
        with self.lock:
            current_time = time.time()
            to_remove = []
            
            for identifier, limiter in self.limiters.items():
                # If no timestamps or all timestamps are old, remove the limiter
                if (not limiter.message_timestamps or 
                    (current_time - limiter.message_timestamps[-1]) > self.time_window * 2):
                    to_remove.append(identifier)
            
            for identifier in to_remove:
                del self.limiters[identifier]
