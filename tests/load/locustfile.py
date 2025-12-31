"""
Locust load testing for the job scheduler API.

Run with:
    locust -f tests/load/locustfile.py --host=http://localhost:8000

Or headless:
    locust -f tests/load/locustfile.py --host=http://localhost:8000 \
        --headless -u 100 -r 10 --run-time 5m
"""

import random
import uuid
from typing import Any

from locust import HttpUser, between, task

# Test tenant configuration
TEST_TENANTS = [f"load-test-tenant-{i}" for i in range(5)]


class JobSchedulerUser(HttpUser):
    """
    Simulated user for load testing the job scheduler.
    
    Simulates realistic traffic patterns:
    - Job submissions (most common)
    - Job status checks
    - Job listing
    - Stats queries
    """

    wait_time = between(0.5, 2)  # Wait 0.5-2 seconds between requests

    def on_start(self):
        """Called when a user starts."""
        self.tenant_id = random.choice(TEST_TENANTS)
        self.token = self._get_token()
        self.created_job_ids: list[str] = []

    def _get_token(self) -> str:
        """Get an auth token for this user."""
        response = self.client.post(
            "/auth/token",
            json={
                "api_key": f"test-key-{self.tenant_id}",
                "tenant_id": self.tenant_id,
            },
        )
        if response.status_code == 200:
            return response.json()["access_token"]
        return ""

    def _headers(self) -> dict[str, str]:
        """Get request headers with auth."""
        return {
            "Authorization": f"Bearer {self.token}",
        }

    @task(10)  # Weight: most common operation
    def submit_job(self):
        """Submit a new job."""
        idempotency_key = f"load-test-{uuid.uuid4().hex}"

        job_types = ["echo", "sleep", "http_request"]
        job_type = random.choice(job_types)

        payload: dict[str, Any] = {"job_type": job_type}

        if job_type == "echo":
            payload["data"] = {"message": f"Load test at {uuid.uuid4().hex[:8]}"}
        elif job_type == "sleep":
            payload["data"] = {"duration_seconds": random.uniform(0.1, 1.0)}
        elif job_type == "http_request":
            payload["data"] = {
                "url": "https://httpbin.org/get",
                "method": "GET",
            }

        response = self.client.post(
            "/v1/jobs",
            json={
                "payload": payload,
                "max_attempts": 3,
                "priority": random.choice(["low", "normal", "high"]),
            },
            headers={
                **self._headers(),
                "Idempotency-Key": idempotency_key,
            },
            name="/v1/jobs [POST]",
        )

        if response.status_code == 201:
            job_id = response.json().get("id")
            if job_id:
                self.created_job_ids.append(job_id)
                # Keep only recent job IDs
                if len(self.created_job_ids) > 100:
                    self.created_job_ids = self.created_job_ids[-100:]

    @task(5)
    def get_job_status(self):
        """Check status of a previously created job."""
        if not self.created_job_ids:
            return

        job_id = random.choice(self.created_job_ids)
        self.client.get(
            f"/v1/jobs/{job_id}",
            headers=self._headers(),
            name="/v1/jobs/{job_id} [GET]",
        )

    @task(3)
    def list_jobs(self):
        """List jobs for the tenant."""
        status_filter = random.choice([None, "queued", "running", "succeeded", "dlq"])
        params = {"page": 1, "page_size": 20}

        if status_filter:
            params["status"] = status_filter

        self.client.get(
            "/v1/jobs",
            params=params,
            headers=self._headers(),
            name="/v1/jobs [GET]",
        )

    @task(2)
    def get_stats(self):
        """Get job statistics."""
        self.client.get(
            "/v1/jobs/stats/summary",
            headers=self._headers(),
            name="/v1/jobs/stats/summary [GET]",
        )

    @task(1)
    def health_check(self):
        """Check API health."""
        self.client.get("/health", name="/health [GET]")


class BurstSubmissionUser(HttpUser):
    """
    User that submits jobs in bursts to test rate limiting and queue handling.
    """

    wait_time = between(5, 10)  # Wait between bursts

    def on_start(self):
        """Called when a user starts."""
        self.tenant_id = f"burst-tenant-{random.randint(1, 3)}"
        self.token = self._get_token()

    def _get_token(self) -> str:
        """Get an auth token for this user."""
        response = self.client.post(
            "/auth/token",
            json={
                "api_key": f"test-key-{self.tenant_id}",
                "tenant_id": self.tenant_id,
            },
        )
        if response.status_code == 200:
            return response.json()["access_token"]
        return ""

    def _headers(self) -> dict[str, str]:
        """Get request headers with auth."""
        return {
            "Authorization": f"Bearer {self.token}",
        }

    @task
    def burst_submit(self):
        """Submit a burst of jobs."""
        burst_size = random.randint(10, 50)

        for _ in range(burst_size):
            idempotency_key = f"burst-{uuid.uuid4().hex}"

            self.client.post(
                "/v1/jobs",
                json={
                    "payload": {"job_type": "echo", "data": {"burst": True}},
                    "max_attempts": 3,
                },
                headers={
                    **self._headers(),
                    "Idempotency-Key": idempotency_key,
                },
                name="/v1/jobs [POST] (burst)",
            )


class IdempotencyTestUser(HttpUser):
    """
    User that tests idempotency by submitting the same job multiple times.
    """

    wait_time = between(1, 3)

    def on_start(self):
        """Called when a user starts."""
        self.tenant_id = "idempotency-test-tenant"
        self.token = self._get_token()
        self.idempotency_keys: list[str] = []

    def _get_token(self) -> str:
        """Get an auth token for this user."""
        response = self.client.post(
            "/auth/token",
            json={
                "api_key": f"test-key-{self.tenant_id}",
                "tenant_id": self.tenant_id,
            },
        )
        if response.status_code == 200:
            return response.json()["access_token"]
        return ""

    def _headers(self) -> dict[str, str]:
        """Get request headers with auth."""
        return {
            "Authorization": f"Bearer {self.token}",
        }

    @task(3)
    def submit_new_job(self):
        """Submit a new job and save the key."""
        idempotency_key = f"idem-{uuid.uuid4().hex}"

        response = self.client.post(
            "/v1/jobs",
            json={
                "payload": {"job_type": "echo"},
                "max_attempts": 3,
            },
            headers={
                **self._headers(),
                "Idempotency-Key": idempotency_key,
            },
            name="/v1/jobs [POST] (new)",
        )

        if response.status_code == 201:
            self.idempotency_keys.append(idempotency_key)
            if len(self.idempotency_keys) > 50:
                self.idempotency_keys = self.idempotency_keys[-50:]

    @task(7)
    def submit_duplicate_job(self):
        """Submit a job with an existing idempotency key."""
        if not self.idempotency_keys:
            return

        idempotency_key = random.choice(self.idempotency_keys)

        response = self.client.post(
            "/v1/jobs",
            json={
                "payload": {"job_type": "different_payload"},
                "max_attempts": 5,
            },
            headers={
                **self._headers(),
                "Idempotency-Key": idempotency_key,
            },
            name="/v1/jobs [POST] (duplicate)",
        )

        # Should return the existing job, not create a new one
        if response.status_code == 201:
            data = response.json()
            if "already exists" not in data.get("message", "").lower():
                # This would be unexpected - duplicate created
                response.failure("Duplicate job created!")
