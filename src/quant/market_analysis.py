from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from quant.analysis import analyze_market_history
from quant.market_data import MARKET_DATA_SCHEMA, get_sp500_constituents
from quant.quotes import resolve_market_history
from quant.storage import DEFAULT_MARKET_DATA_PATH, load_market_data


DEFAULT_MARKET_ANALYSIS_PATH = Path("data/market_analysis.parquet")


def analyze_symbols(
    symbols: Iterable[str],
    windows: list[int],
    price_column: str,
    index_cache_path: Path = DEFAULT_MARKET_DATA_PATH,
    analysis_cache_path: Path = DEFAULT_MARKET_ANALYSIS_PATH,
    refresh: bool = False,
) -> pl.DataFrame:
    symbols = [
        symbol.strip().upper()
        for symbol in dict.fromkeys(symbols)
        if symbol.strip()
    ]
    if not symbols:
        raise ValueError("At least one symbol is required")
    if not windows or any(window <= 0 for window in windows):
        raise ValueError("Analysis windows must be positive integers")
    if price_column not in {"Close", "Adjusted Close"}:
        raise ValueError("Price column must be Close or Adjusted Close")

    minimum_sessions = max(max(windows), 2)
    start = date.today() - timedelta(days=minimum_sessions * 2 + 30)
    market_history = resolve_market_history(
        symbols,
        [index_cache_path, analysis_cache_path],
        analysis_cache_path,
        list(MARKET_DATA_SCHEMA),
        minimum_sessions=minimum_sessions,
        refresh=refresh,
        start=start,
    )
    return analyze_market_history(market_history, windows, price_column)


def get_index_symbols(
    index_cache_path: Path = DEFAULT_MARKET_DATA_PATH,
) -> list[str]:
    if index_cache_path.exists():
        cached = load_market_data(index_cache_path)
        if "Symbol" in cached.columns:
            return cached.get_column("Symbol").unique(maintain_order=True).to_list()
    return get_sp500_constituents().get_column("Symbol").to_list()
