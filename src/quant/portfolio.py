from collections.abc import Iterable
from pathlib import Path

import polars as pl

from quant.analysis import analyze_portfolio
from quant.quotes import resolve_market_history
from quant.storage import DEFAULT_MARKET_DATA_PATH


DEFAULT_PORTFOLIO_MARKET_DATA_PATH = Path("data/portfolio_market_data.parquet")
MARKET_DATA_COLUMNS = ["Date", "Symbol", "Last Price", "Volume"]


def load_portfolio_market_data(
    symbols: Iterable[str],
    market_cache_path: Path = DEFAULT_MARKET_DATA_PATH,
    portfolio_cache_path: Path = DEFAULT_PORTFOLIO_MARKET_DATA_PATH,
    refresh: bool = False,
) -> pl.DataFrame:
    return resolve_market_history(
        symbols,
        [market_cache_path, portfolio_cache_path],
        portfolio_cache_path,
        MARKET_DATA_COLUMNS,
        refresh=refresh,
    )


def analyze_positions(
    positions: pl.DataFrame,
    market_cache_path: Path = DEFAULT_MARKET_DATA_PATH,
    portfolio_cache_path: Path = DEFAULT_PORTFOLIO_MARKET_DATA_PATH,
    refresh: bool = False,
) -> pl.DataFrame:
    market_data = load_portfolio_market_data(
        positions.get_column("Symbol").unique(maintain_order=True).to_list(),
        market_cache_path,
        portfolio_cache_path,
        refresh,
    )
    return analyze_portfolio(positions, market_data)
