"""
Lease reaper for recovering expired job leases.

The reaper runs periodically to find jobs with expired leases
and returns them to the queue. This handles worker crashes and
ensures at-least-once delivery.
"""

import asyncio
import logging
import signal

from src.config import get_settings
from src.db import init_db, close_db, get_session_context
from src.db.repository import JobRepository
from src.observability.logging import setup_logging
from src.observability.metrics import get_metrics

logger = logging.getLogger(__name__)


class Reaper:
    """
    Lease reaper that recovers expired job leases.
    
    Runs periodically to:
    1. Find jobs in LEASED status with expired lease_expires_at
    2. Return them to QUEUED status for reprocessing
    3. Record metrics for monitoring
    """

    def __init__(self, interval_seconds: int | None = None):
        """
        Initialize the reaper.
        
        Args:
            interval_seconds: Seconds between reaper runs.
        """
        settings = get_settings()
        self.interval = interval_seconds or settings.reaper_interval_seconds
        self._running = False
        self._metrics = get_metrics()

    async def start(self) -> None:
        """Start the reaper loop."""
        logger.info(f"Reaper starting with interval {self.interval}s")
        self._running = True
        
        while self._running:
            try:
                recovered = await self._recover_expired_leases()
                
                if recovered > 0:
                    logger.info(f"Recovered {recovered} expired leases")
                
            except Exception as e:
                logger.exception(f"Error in reaper loop: {e}")
            
            await asyncio.sleep(self.interval)
        
        logger.info("Reaper stopped")

    async def stop(self) -> None:
        """Stop the reaper."""
        logger.info("Reaper stopping")
        self._running = False

    async def _recover_expired_leases(self) -> int:
        """
        Find and recover jobs with expired leases.
        
        Returns:
            Number of jobs recovered.
        """
        async with get_session_context() as session:
            repo = JobRepository(session)
            
            # Recover expired leases
            count = await repo.recover_expired_leases()
            
            await session.commit()
            
            # Update metrics (would need tenant info for proper metrics)
            if count > 0:
                self._metrics.lease_expired.labels(tenant_id="all").inc(count)
            
            return count

    async def run_once(self) -> int:
        """
        Run the reaper once (for testing or cron-style execution).
        
        Returns:
            Number of jobs recovered.
        """
        return await self._recover_expired_leases()


async def run_async() -> None:
    """Run the reaper asynchronously."""
    setup_logging()
    await init_db()
    
    reaper = Reaper()
    
    # Handle shutdown signals
    loop = asyncio.get_event_loop()
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig,
            lambda: asyncio.create_task(reaper.stop())
        )
    
    try:
        await reaper.start()
    finally:
        await close_db()


def run() -> None:
    """Run the reaper."""
    asyncio.run(run_async())


if __name__ == "__main__":
    run()
