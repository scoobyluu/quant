from collections.abc import Iterable
from pathlib import Path

import polars as pl

from quant.analysis import analyze_portfolio
from quant.quotes import resolve_market_history
from quant.storage import DEFAULT_MARKET_DATA_PATH


DEFAULT_PORTFOLIO_PATH = Path("portfolio.csv")
DEFAULT_PORTFOLIO_MARKET_DATA_PATH = Path("data/portfolio_market_data.parquet")
MARKET_DATA_COLUMNS = ["Date", "Symbol", "Last Price", "Volume"]
OPTIONAL_PORTFOLIO_COLUMNS = {
    "account": ("Account", pl.String),
    "asset_class": ("Asset Class", pl.String),
    "sector": ("Sector", pl.String),
}


def load_portfolio_lots(path: Path = DEFAULT_PORTFOLIO_PATH) -> pl.DataFrame:
    raw = pl.read_csv(path)
    required = {"ticker", "quantity", "cost"}
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError(f"Missing portfolio columns: {', '.join(sorted(missing))}")

    optional = [
        (
            pl.col(source).cast(dtype, strict=False).alias(target)
            if source in raw.columns
            else pl.lit(None, dtype=dtype).alias(target)
        )
        for source, (target, dtype) in OPTIONAL_PORTFOLIO_COLUMNS.items()
    ]
    acquired = (
        pl.col("acquired")
        .cast(pl.String)
        .str.to_date(strict=False)
        .alias("Acquired")
        if "acquired" in raw.columns
        else pl.lit(None, dtype=pl.Date).alias("Acquired")
    )
    positions = raw.select(
        pl.col("ticker")
        .cast(pl.String)
        .str.strip_chars()
        .str.to_uppercase()
        .alias("Symbol"),
        pl.col("quantity").cast(pl.Float64, strict=False).alias("Quantity"),
        pl.col("cost").cast(pl.Float64, strict=False).alias("Average Cost"),
        *optional,
        acquired,
    )
    if positions.is_empty():
        raise ValueError("Portfolio is empty")
    if positions.filter(
        pl.col("Symbol").is_null()
        | (pl.col("Symbol") == "")
        | pl.col("Quantity").is_null()
        | (pl.col("Quantity") <= 0)
        | pl.col("Average Cost").is_null()
        | (pl.col("Average Cost") < 0)
    ).height:
        raise ValueError("Portfolio contains an invalid ticker, quantity, or cost")

    return positions


def load_portfolio(path: Path = DEFAULT_PORTFOLIO_PATH) -> pl.DataFrame:
    positions = load_portfolio_lots(path)
    return (
        positions.with_columns(
            (pl.col("Quantity") * pl.col("Average Cost")).alias("Cost Basis")
        )
        .group_by("Symbol", maintain_order=True)
        .agg(
            pl.col("Quantity").sum(),
            pl.col("Cost Basis").sum(),
        )
        .with_columns(
            (pl.col("Cost Basis") / pl.col("Quantity")).alias("Average Cost")
        )
        .select("Symbol", "Quantity", "Average Cost")
    )


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


def analyze_portfolio_file(
    portfolio_path: Path = DEFAULT_PORTFOLIO_PATH,
    market_cache_path: Path = DEFAULT_MARKET_DATA_PATH,
    portfolio_cache_path: Path = DEFAULT_PORTFOLIO_MARKET_DATA_PATH,
    refresh: bool = False,
) -> pl.DataFrame:
    positions = load_portfolio(portfolio_path)
    market_data = load_portfolio_market_data(
        positions.get_column("Symbol").to_list(),
        market_cache_path,
        portfolio_cache_path,
        refresh,
    )
    return analyze_portfolio(positions, market_data)
