"""Initial schema with jobs table

Revision ID: 001
Revises: 
Create Date: 2024-01-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enums using raw SQL with IF NOT EXISTS
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE job_status AS ENUM ('queued', 'leased', 'running', 'succeeded', 'failed', 'dlq');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE job_priority AS ENUM ('low', 'normal', 'high', 'critical');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    
    # Create jobs table
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "status",
            postgresql.ENUM("queued", "leased", "running", "succeeded", "failed", "dlq", name="job_status", create_type=False),
            nullable=False,
            server_default="queued",
        ),
        sa.Column(
            "priority",
            postgresql.ENUM("low", "normal", "high", "critical", name="job_priority", create_type=False),
            nullable=False,
            server_default="normal",
        ),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
        sa.Column("lease_owner", sa.String(255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("result", postgresql.JSONB, nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    
    # Create indexes
    op.create_index("ix_jobs_tenant_id", "jobs", ["tenant_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_lease_owner", "jobs", ["lease_owner"])
    op.create_index("ix_jobs_lease_expires_at", "jobs", ["lease_expires_at"])
    op.create_index("ix_jobs_scheduled_at", "jobs", ["scheduled_at"])
    
    # Create unique constraint for idempotency
    op.create_unique_constraint(
        "uq_tenant_idempotency",
        "jobs",
        ["tenant_id", "idempotency_key"],
    )
    
    # Create partial index for queue polling
    op.execute("""
        CREATE INDEX ix_jobs_queue_poll 
        ON jobs (status, scheduled_at, priority) 
        WHERE status = 'queued'
    """)
    
    # Create partial index for tenant active jobs
    op.execute("""
        CREATE INDEX ix_jobs_tenant_active 
        ON jobs (tenant_id, status) 
        WHERE status IN ('leased', 'running')
    """)
    
    # Create partial index for lease expiry
    op.execute("""
        CREATE INDEX ix_jobs_lease_expiry 
        ON jobs (lease_expires_at) 
        WHERE status = 'leased'
    """)


def downgrade() -> None:
    # Drop indexes
    op.execute("DROP INDEX IF EXISTS ix_jobs_lease_expiry")
    op.execute("DROP INDEX IF EXISTS ix_jobs_tenant_active")
    op.execute("DROP INDEX IF EXISTS ix_jobs_queue_poll")
    op.drop_index("ix_jobs_scheduled_at")
    op.drop_index("ix_jobs_lease_expires_at")
    op.drop_index("ix_jobs_lease_owner")
    op.drop_index("ix_jobs_status")
    op.drop_index("ix_jobs_tenant_id")
    op.drop_constraint("uq_tenant_idempotency", "jobs")
    
    # Drop table
    op.drop_table("jobs")
    
    # Drop enums
    op.execute("DROP TYPE IF EXISTS job_status")
    op.execute("DROP TYPE IF EXISTS job_priority")
