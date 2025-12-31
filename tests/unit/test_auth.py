"""
Unit tests for authentication.
"""

from datetime import timedelta

import pytest
from fastapi import HTTPException

from src.api.auth import (
    create_access_token,
    decode_token,
    validate_api_key,
)


class TestAuth:
    """Tests for authentication utilities."""

    def test_create_access_token(self):
        """Test JWT token creation."""
        tenant_id = "test-tenant"
        token = create_access_token(tenant_id=tenant_id)

        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 0

    def test_decode_valid_token(self):
        """Test decoding a valid token."""
        tenant_id = "test-tenant"
        token = create_access_token(tenant_id=tenant_id)

        token_data = decode_token(token)

        assert token_data.tenant_id == tenant_id
        assert token_data.exp is not None

    def test_decode_expired_token(self):
        """Test decoding an expired token raises error."""
        tenant_id = "test-tenant"
        # Create token that expired 1 hour ago
        token = create_access_token(
            tenant_id=tenant_id,
            expires_delta=timedelta(hours=-1),
        )

        with pytest.raises(HTTPException) as exc_info:
            decode_token(token)

        assert exc_info.value.status_code == 401

    def test_decode_invalid_token(self):
        """Test decoding an invalid token raises error."""
        with pytest.raises(HTTPException) as exc_info:
            decode_token("invalid-token")

        assert exc_info.value.status_code == 401

    def test_validate_api_key_valid(self):
        """Test API key validation with valid key."""
        result = validate_api_key("valid-key", "tenant-123")
        assert result is True

    def test_validate_api_key_empty(self):
        """Test API key validation with empty values."""
        assert validate_api_key("", "tenant") is False
        assert validate_api_key("key", "") is False
        assert validate_api_key("", "") is False
