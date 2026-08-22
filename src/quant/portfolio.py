from collections.abc import Iterable
from pathlib import Path

import polars as pl

from quant.analysis import analyze_portfolio
from quant.market_data import get_market_history
from quant.storage import DEFAULT_MARKET_DATA_PATH, load_market_data, save_market_data


DEFAULT_PORTFOLIO_PATH = Path("portfolio.csv")
DEFAULT_PORTFOLIO_MARKET_DATA_PATH = Path("data/portfolio_market_data.parquet")
MARKET_DATA_COLUMNS = ["Date", "Symbol", "Last Price", "Volume"]


def load_portfolio(path: Path = DEFAULT_PORTFOLIO_PATH) -> pl.DataFrame:
    raw = pl.read_csv(path)
    required = {"ticker", "quantity", "cost"}
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError(f"Missing portfolio columns: {', '.join(sorted(missing))}")

    positions = raw.select(
        pl.col("ticker")
        .cast(pl.String)
        .str.strip_chars()
        .str.to_uppercase()
        .alias("Symbol"),
        pl.col("quantity").cast(pl.Float64, strict=False).alias("Quantity"),
        pl.col("cost").cast(pl.Float64, strict=False).alias("Average Cost"),
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
    symbols = list(dict.fromkeys(symbols))
    cached = []
    if market_cache_path.exists():
        cached.append(load_market_data(market_cache_path).select(MARKET_DATA_COLUMNS))
    if portfolio_cache_path.exists():
        cached.append(load_market_data(portfolio_cache_path).select(MARKET_DATA_COLUMNS))

    market_data = _combine_market_data(cached)
    available = (
        set(
            market_data.filter(pl.col("Last Price").is_not_null())
            .get_column("Symbol")
            .unique()
            .to_list()
        )
        if market_data.height
        else set()
    )
    symbols_to_download = symbols if refresh else [s for s in symbols if s not in available]
    if symbols_to_download:
        downloaded = get_market_history(symbols_to_download).select(MARKET_DATA_COLUMNS)
        portfolio_data = _combine_market_data(
            [
                load_market_data(portfolio_cache_path).select(MARKET_DATA_COLUMNS)
                if portfolio_cache_path.exists()
                else _empty_market_data(),
                downloaded,
            ]
        )
        save_market_data(portfolio_data, portfolio_cache_path)
        market_data = _combine_market_data([market_data, downloaded])

    return market_data.filter(pl.col("Symbol").is_in(symbols))


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


def _combine_market_data(frames: list[pl.DataFrame]) -> pl.DataFrame:
    if not frames:
        return _empty_market_data()
    return (
        pl.concat(frames)
        .unique(subset=["Date", "Symbol"], keep="last", maintain_order=True)
        .sort(["Date", "Symbol"])
    )


def _empty_market_data() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "Date": pl.Date,
            "Symbol": pl.String,
            "Last Price": pl.Float64,
            "Volume": pl.Int64,
        }
    )
