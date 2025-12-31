"""
Integration tests for the API endpoints.
"""

from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient

from src.api.auth import create_access_token
from src.constants import JobStatus


class TestJobAPI:
    """Integration tests for job API endpoints."""

    @pytest.fixture
    def tenant_id(self) -> str:
        return f"test-tenant-{uuid4().hex[:8]}"

    @pytest.fixture
    def auth_headers(self, tenant_id: str) -> dict[str, str]:
        token = create_access_token(tenant_id=tenant_id)
        return {"Authorization": f"Bearer {token}"}

    @pytest_asyncio.fixture
    async def created_job(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> dict:
        """Create a job for testing."""
        response = await client.post(
            "/v1/jobs",
            json={
                "payload": {"job_type": "echo", "data": {"test": True}},
                "max_attempts": 3,
            },
            headers={
                **auth_headers,
                "Idempotency-Key": f"test-{uuid4().hex}",
            },
        )
        return response.json()

    @pytest.mark.asyncio
    async def test_create_job_success(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ):
        """Test successful job creation."""
        idempotency_key = f"test-{uuid4().hex}"

        response = await client.post(
            "/v1/jobs",
            json={
                "payload": {"job_type": "echo", "data": {"message": "hello"}},
                "max_attempts": 3,
            },
            headers={
                **auth_headers,
                "Idempotency-Key": idempotency_key,
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["idempotency_key"] == idempotency_key
        assert data["status"] == JobStatus.QUEUED

    @pytest.mark.asyncio
    async def test_create_job_idempotency(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ):
        """Test idempotent job submission."""
        idempotency_key = f"test-{uuid4().hex}"
        headers = {**auth_headers, "Idempotency-Key": idempotency_key}

        # First request
        response1 = await client.post(
            "/v1/jobs",
            json={"payload": {"job_type": "echo"}},
            headers=headers,
        )

        # Second request with same key
        response2 = await client.post(
            "/v1/jobs",
            json={"payload": {"job_type": "different"}},
            headers=headers,
        )

        assert response1.status_code == 201
        assert response2.status_code == 201
        assert response1.json()["id"] == response2.json()["id"]

    @pytest.mark.asyncio
    async def test_create_job_missing_idempotency_key(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ):
        """Test job creation fails without idempotency key."""
        response = await client.post(
            "/v1/jobs",
            json={"payload": {"job_type": "echo"}},
            headers=auth_headers,
        )

        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_create_job_unauthorized(self, client: AsyncClient):
        """Test job creation fails without auth."""
        response = await client.post(
            "/v1/jobs",
            json={"payload": {"job_type": "echo"}},
            headers={"Idempotency-Key": "test"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_get_job_success(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        created_job: dict,
    ):
        """Test getting a job by ID."""
        job_id = created_job["id"]

        response = await client.get(
            f"/v1/jobs/{job_id}",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == job_id

    @pytest.mark.asyncio
    async def test_get_job_not_found(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ):
        """Test getting a non-existent job."""
        response = await client.get(
            f"/v1/jobs/{uuid4()}",
            headers=auth_headers,
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_jobs(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ):
        """Test listing jobs."""
        # Create some jobs first
        for i in range(3):
            await client.post(
                "/v1/jobs",
                json={"payload": {"job_type": "echo", "index": i}},
                headers={
                    **auth_headers,
                    "Idempotency-Key": f"list-test-{uuid4().hex}",
                },
            )

        response = await client.get(
            "/v1/jobs",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "jobs" in data
        assert "total" in data
        assert len(data["jobs"]) >= 3

    @pytest.mark.asyncio
    async def test_list_jobs_with_status_filter(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ):
        """Test listing jobs with status filter."""
        response = await client.get(
            "/v1/jobs?status=queued",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        for job in data["jobs"]:
            assert job["status"] == JobStatus.QUEUED

    @pytest.mark.asyncio
    async def test_get_job_stats(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ):
        """Test getting job statistics."""
        response = await client.get(
            "/v1/jobs/stats/summary",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "stats" in data
        assert "queue_depth" in data


class TestHealthEndpoints:
    """Integration tests for health endpoints."""

    @pytest.mark.asyncio
    async def test_health_check(self, client: AsyncClient):
        """Test health check endpoint."""
        response = await client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["healthy", "degraded"]

    @pytest.mark.asyncio
    async def test_liveness(self, client: AsyncClient):
        """Test liveness probe."""
        response = await client.get("/live")

        assert response.status_code == 200
        data = response.json()
        assert data["alive"] is True

    @pytest.mark.asyncio
    async def test_readiness(self, client: AsyncClient):
        """Test readiness probe."""
        response = await client.get("/ready")

        assert response.status_code == 200
        data = response.json()
        assert "ready" in data

    @pytest.mark.asyncio
    async def test_metrics(self, client: AsyncClient):
        """Test metrics endpoint."""
        response = await client.get("/metrics")

        assert response.status_code == 200
        assert "text/plain" in response.headers.get("content-type", "")


class TestAuthEndpoints:
    """Integration tests for auth endpoints."""

    @pytest.mark.asyncio
    async def test_get_token(self, client: AsyncClient):
        """Test getting an access token."""
        response = await client.post(
            "/auth/token",
            json={
                "api_key": "test-api-key",
                "tenant_id": "test-tenant",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_get_token_invalid_key(self, client: AsyncClient):
        """Test token request with invalid credentials."""
        response = await client.post(
            "/auth/token",
            json={
                "api_key": "",
                "tenant_id": "test-tenant",
            },
        )

        assert response.status_code == 401
