"""Download historical bars for the configured universe into Postgres.

Usage:
    python scripts/download_history.py [--years 5]
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

import structlog

from backend.core.config import get_settings, load_yaml_config
from backend.data.alpaca_provider import AlpacaDataProvider
from backend.data import quality

log = structlog.get_logger(__name__)


async def main(years: int) -> None:
    settings = get_settings()
    market = load_yaml_config("market")
    provider = AlpacaDataProvider(settings)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365 * years)

    for symbol in market.get("universe", []):
        for interval in ("1h", "1d"):
            bars = await provider.get_bars(symbol, interval, start, end)
            gaps = quality.count_gaps(bars)
            sane = quality.prices_sane(bars)
            log.info("downloaded", symbol=symbol, interval=interval,
                     bars=len(bars), gaps=gaps, prices_sane=sane)
            # TODO(persistence): bulk-insert into bars hypertable once the
            # SQLAlchemy models land. Non-execution path, safe to defer.


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(main(args.years))
