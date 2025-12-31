#!/bin/bash
set -e

echo "=== Setting up test database ==="

# Create test database if it doesn't exist
docker exec jobqueue-postgres psql -U postgres -tc "SELECT 1 FROM pg_database WHERE datname = 'jobqueue_test'" | grep -q 1 || \
    docker exec jobqueue-postgres createdb -U postgres jobqueue_test

echo "✓ Test database ready"

# Run migrations on test database
echo ""
echo "=== Running migrations on test database ==="
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/jobqueue_test poetry run alembic upgrade head

echo ""
echo "=== Running tests ==="
# Set test database URL before running tests
export DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/jobqueue_test
poetry run pytest tests/ -v --tb=short

echo ""
echo "=== Test run complete ==="
