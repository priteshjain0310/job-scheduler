"""
Authentication routes.
"""

from fastapi import APIRouter, HTTPException, status

from src.api.auth import create_access_token, validate_api_key
from src.config import get_settings
from src.types.api import AuthRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="Get access token",
    description="Exchange API key for a JWT access token.",
)
async def get_token(request: AuthRequest) -> TokenResponse:
    """
    Get an access token using API key authentication.
    
    Args:
        request: Authentication request with API key and tenant ID.
        
    Returns:
        TokenResponse with JWT access token.
        
    Raises:
        HTTPException: If authentication fails.
    """
    # Validate API key
    if not validate_api_key(request.api_key, request.tenant_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    
    # Create access token
    settings = get_settings()
    access_token = create_access_token(tenant_id=request.tenant_id)
    
    return TokenResponse(
        access_token=access_token,
        expires_in=settings.api_access_token_expire_minutes * 60,
    )
