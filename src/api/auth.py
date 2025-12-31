"""
Authentication and authorization utilities.
"""

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from pydantic import BaseModel

from src.config import get_settings

# Security scheme
security = HTTPBearer()


class TokenData(BaseModel):
    """Data extracted from JWT token."""

    tenant_id: str
    exp: datetime


class AuthenticatedUser(BaseModel):
    """Authenticated user context."""

    tenant_id: str
    api_key: str | None = None


def create_access_token(
    tenant_id: str,
    expires_delta: timedelta | None = None,
) -> str:
    """
    Create a JWT access token.
    
    Args:
        tenant_id: The tenant identifier.
        expires_delta: Optional custom expiration time.
        
    Returns:
        The encoded JWT token.
    """
    settings = get_settings()
    
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.api_access_token_expire_minutes)
    
    expire = datetime.utcnow() + expires_delta
    
    to_encode = {
        "tenant_id": tenant_id,
        "exp": expire,
        "iat": datetime.utcnow(),
    }
    
    encoded_jwt = jwt.encode(
        to_encode,
        settings.api_secret_key,
        algorithm=settings.api_algorithm,
    )
    
    return encoded_jwt


def decode_token(token: str) -> TokenData:
    """
    Decode and validate a JWT token.
    
    Args:
        token: The JWT token to decode.
        
    Returns:
        TokenData extracted from the token.
        
    Raises:
        HTTPException: If token is invalid or expired.
    """
    settings = get_settings()
    
    try:
        payload = jwt.decode(
            token,
            settings.api_secret_key,
            algorithms=[settings.api_algorithm],
        )
        tenant_id: str = payload.get("tenant_id")
        exp: datetime = datetime.fromtimestamp(payload.get("exp"))
        
        if tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing tenant_id",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        return TokenData(tenant_id=tenant_id, exp=exp)
        
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> AuthenticatedUser:
    """
    FastAPI dependency to get the current authenticated user.
    
    Args:
        credentials: The HTTP authorization credentials.
        
    Returns:
        AuthenticatedUser with tenant information.
        
    Raises:
        HTTPException: If authentication fails.
    """
    token_data = decode_token(credentials.credentials)
    
    return AuthenticatedUser(
        tenant_id=token_data.tenant_id,
    )


# Type alias for dependency injection
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


def validate_api_key(api_key: str, tenant_id: str) -> bool:
    """
    Validate an API key for a tenant.
    
    In a production system, this would check against a database or cache.
    For this implementation, we use a simple format check.
    
    Args:
        api_key: The API key to validate.
        tenant_id: The tenant identifier.
        
    Returns:
        True if the API key is valid.
    """
    # In production, validate against stored API keys
    # For demo purposes, accept any non-empty key
    return bool(api_key) and bool(tenant_id)
