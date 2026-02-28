"""Standalone resolution worker process for debugging and operations."""
import argparse
import asyncio

from src.resolution_worker import ResolutionWorker
from src.utils.logging import get_logger

log = get_logger(__name__)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run Polymarket market-resolution worker")
    parser.add_argument("--db", type=str, default=None, help="Path to SQLite database file (default: paper_trades.db)")
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=300,
        help="Gamma polling interval in seconds (default: 300 for debugging)",
    )
    args = parser.parse_args()

    worker = ResolutionWorker(db_path=args.db, poll_interval_seconds=args.poll_interval)
    await worker.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Resolution worker stopped gracefully")
