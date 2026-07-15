"""Asset-class helpers.

One convention, used everywhere: crypto symbols are Alpaca-style pairs with a
slash ("BTC/USD", "ETH/USD"); anything else is an equity ticker. Keeping the
test in one place means the provider, risk engine, and brokers can never
disagree about what a symbol is.
"""
from __future__ import annotations


def is_crypto(symbol: str) -> bool:
    """True for slash-delimited pairs like ``BTC/USD``."""
    return "/" in symbol
