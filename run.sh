#!/bin/bash
set -e

# Install Python dependencies
# poetry install

# # Start PostgreSQL
# docker compose up -d postgres

# # Wait for PostgreSQL to be ready
# echo "Waiting for PostgreSQL to be ready..."
# until docker compose exec -T postgres pg_isready -U postgres > /dev/null 2>&1; do
#   echo "PostgreSQL is unavailable - sleeping"
#   sleep 1
# done
# echo "PostgreSQL is ready!"

# Run migrations
# poetry run alembic upgrade head

# Start services (in separate terminals)
make api      # API server on :8000
make worker   # Worker process
make reaper   # Reaper process

# For dashboard
cd dashboard && npm install && npm run dev