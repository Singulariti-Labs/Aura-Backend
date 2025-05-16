"""
Authentication module - Handles JWT validation and user authentication.
"""
import jwt
from typing import Dict, Any
import time
from async_lru import alru_cache
import pathlib
from websocket.logger import get_logger
from websocket.config import settings
from websocket.metrics import AUTH_FAILURES, AUTH_SUCCESSES


logger = get_logger(__name__)


class JWTAuthenticationError(Exception):
    """Exception raised for JWT authentication errors."""
    pass


@alru_cache(maxsize=100)
async def get_public_key(key_id: str) -> str:
    """
    Retrieve public key contents for JWT validation (cached).

    Args:
        key_id: The key ID to retrieve

    Returns:
        The public key PEM content as a string

    Raises:
        JWTAuthenticationError: If the key ID is not found or file cannot be read
    """
    if key_id in settings.JWT_PUBLIC_KEYS:
        pem_path = pathlib.Path(settings.JWT_PUBLIC_KEYS[key_id])
        if pem_path.is_file():
            try:
                pem_content = pem_path.read_text()
                return pem_content
            except Exception as e:
                raise JWTAuthenticationError(f"Failed to read public key file '{pem_path}': {e}")
        else:
            raise JWTAuthenticationError(f"Public key file not found: {pem_path}")
    
    raise JWTAuthenticationError(f"Unknown key ID: {key_id}")


async def authenticate_client(token: str) -> Dict[str, Any]:
    """
    Authenticate a client using a JWT token.
    
    Args:
        token: The JWT token to validate
        
    Returns:
        Dict containing user data from the token
        
    Raises:
        JWTAuthenticationError: If authentication fails
    """
    try:
        # First, decode the token header without verification to get the key ID
        unverified_header = jwt.get_unverified_header(token)
        key_id = unverified_header.get("kid")
        
        if not key_id:
            AUTH_FAILURES.labels(reason="missing_kid").inc()
            logger.warning("JWT missing key ID")
            raise JWTAuthenticationError("Invalid token format: missing key ID")
        
        # Get the public key for verification
        public_key = await get_public_key(key_id)
        
        # Verify and decode the token
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iat": True,
                "verify_aud": False,
                "verify_iss": False, #settings.JWT_ISSUER is not None,
                "require": ["exp", "iat", "user_id"]
            },
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER
        )
        
        # Check required claims
        if "user_id" not in payload:
            AUTH_FAILURES.labels(reason="missing_user_id").inc()
            logger.warning("JWT missing user_id claim")
            raise JWTAuthenticationError("Invalid token: missing user_id claim")
        
        # Add client_id for internal tracking
        payload["client_id"] = payload.get("client_id", f"user-{payload['user_id']}")
        
        # Additional security checks
        now = int(time.time())
        if payload.get("iat", 0) > now + settings.JWT_CLOCK_SKEW_SECONDS:
            AUTH_FAILURES.labels(reason="future_token").inc()
            logger.warning("JWT issued in the future", user_id=payload["user_id"])
            raise JWTAuthenticationError("Token issued in the future")
        
        # Check permissions if they exist in the token
        if settings.REQUIRED_SCOPE and "scope" in payload:
            scopes = payload["scope"].split(" ") if isinstance(payload["scope"], str) else payload["scope"]
            if settings.REQUIRED_SCOPE not in scopes:
                AUTH_FAILURES.labels(reason="insufficient_scope").inc()
                logger.warning(
                    "Insufficient permissions", 
                    user_id=payload["user_id"],
                    required=settings.REQUIRED_SCOPE, 
                    provided=scopes
                )
                raise JWTAuthenticationError(f"Insufficient permissions: {settings.REQUIRED_SCOPE} required")
        
        AUTH_SUCCESSES.inc()
        logger.info("Authentication successful", user_id=payload["user_id"])
        return payload
        
    except jwt.ExpiredSignatureError:
        AUTH_FAILURES.labels(reason="expired").inc()
        logger.warning("JWT expired")
        raise JWTAuthenticationError("Token expired")
    
    except jwt.InvalidTokenError as e:
        AUTH_FAILURES.labels(reason="invalid").inc()
        logger.warning(f"Invalid JWT: {str(e)}")
        raise JWTAuthenticationError(f"Invalid token: {str(e)}")
    
    except Exception as e:
        AUTH_FAILURES.labels(reason="other").inc()
        logger.error(f"Authentication error: {str(e)}")
        raise JWTAuthenticationError(f"Authentication failed: {str(e)}")
