.PHONY: help install dev test lint format migrate api worker reaper docker-build docker-up docker-down clean

help:
	@echo "Available commands:"
	@echo "  install      - Install dependencies with Poetry"
	@echo "  dev          - Start development environment"
	@echo "  test         - Run all tests"
	@echo "  test-unit    - Run unit tests only"
	@echo "  test-int     - Run integration tests only"
	@echo "  lint         - Run linters"
	@echo "  format       - Format code"
	@echo "  migrate      - Run database migrations"
	@echo "  api          - Start API server"
	@echo "  worker       - Start worker process"
	@echo "  reaper       - Start reaper process"
	@echo "  docker-build - Build Docker images"
	@echo "  docker-up    - Start Docker Compose stack"
	@echo "  docker-down  - Stop Docker Compose stack"
	@echo "  clean        - Clean up generated files"

# Development
install:
	poetry install

dev: docker-up-db migrate
	@echo "Starting development environment..."
	@echo "Run 'make api', 'make worker', and 'make reaper' in separate terminals"

# Testing
test:
	poetry run pytest tests/ -v --cov=src --cov-report=term-missing

test-unit:
	poetry run pytest tests/unit/ -v

test-int:
	poetry run pytest tests/integration/ -v

# Code quality
lint:
	poetry run ruff check src/ tests/
	poetry run mypy src/

format:
	poetry run ruff check src/ tests/ --fix
	poetry run ruff format src/ tests/

# Database
migrate:
	poetry run alembic upgrade head

migrate-create:
	@read -p "Migration message: " msg; \
	poetry run alembic revision --autogenerate -m "$$msg"

# Services
api:
	poetry run python -m uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000

worker:
	poetry run python -m src.worker.main

reaper:
	poetry run python -m src.reaper.main

# Docker
docker-build:
	docker build -t jobqueue-api:latest -f Dockerfile .
	docker build -t jobqueue-worker:latest -f Dockerfile.worker .

docker-up:
	docker compose up -d

docker-up-db:
	docker compose up -d postgres
	@echo "Waiting for PostgreSQL to be ready..."
	@until docker compose exec -T postgres pg_isready -U postgres > /dev/null 2>&1; do sleep 1; done
	@echo "PostgreSQL is ready!"

docker-up-full:
	docker compose --profile observability --profile dashboard up -d

docker-down:
	docker compose --profile observability --profile dashboard down

docker-logs:
	docker compose logs -f

docker-migrate:
	docker compose --profile migrate up migrate

# Load testing
load-test:
	poetry run locust -f tests/load/locustfile.py --host=http://localhost:8000

load-test-headless:
	poetry run locust -f tests/load/locustfile.py --host=http://localhost:8000 \
		--headless -u 100 -r 10 --run-time 2m

# Dashboard
dashboard-install:
	cd dashboard && npm install

dashboard-dev:
	cd dashboard && npm run dev

dashboard-build:
	cd dashboard && npm run build

# Kubernetes
k8s-apply:
	kubectl apply -k k8s/

k8s-delete:
	kubectl delete -k k8s/

k8s-migrate:
	kubectl apply -f k8s/migration-job.yaml

k8s-logs-api:
	kubectl logs -f -l app.kubernetes.io/name=jobqueue-api -n jobqueue

k8s-logs-worker:
	kubectl logs -f -l app.kubernetes.io/name=jobqueue-worker -n jobqueue

# Cleanup
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .coverage htmlcov/ 2>/dev/null || true
