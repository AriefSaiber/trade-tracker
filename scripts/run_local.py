"""One-command local platform: API + embedded paper-trading worker.

    python scripts/run_local.py            # http://127.0.0.1:8000
    python scripts/run_local.py --port 8010

Zero external services required: simulated market data (configs/market.yaml
provider: simulated), SQLite persistence (data/algotrader.db), in-memory
state store shared by the worker and the API. The Next.js dashboard
(`cd frontend && npm run dev`) proxies to this API on port 8000.

Docker Compose remains the deployment path; this script exists so a fresh
clone can paper-trade with nothing but Python installed.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Must be set before backend.core.config is imported anywhere.
os.environ.setdefault("WORKER_EMBEDDED", "true")
os.environ.setdefault("LIVE_TRADING", "false")   # paper is the default, always


def main() -> None:
    parser = argparse.ArgumentParser(description="AlgoTrader local runner")
    parser.add_argument("--host", default="127.0.0.1")   # localhost only
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()

    os.chdir(REPO_ROOT)  # configs/, data/, logs/ resolve relative to repo root
    import uvicorn

    uvicorn.run("backend.app.main:app", host=args.host, port=args.port,
                log_level="info")


if __name__ == "__main__":
    main()
