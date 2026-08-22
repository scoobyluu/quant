from collections.abc import Iterable
from datetime import date
from pathlib import Path

import polars as pl

from quant.market_data import MARKET_DATA_SCHEMA, get_market_history
from quant.storage import load_market_data, save_market_data


def resolve_market_history(
    symbols: Iterable[str],
    cache_paths: Iterable[Path],
    writable_cache_path: Path,
    required_columns: list[str],
    minimum_sessions: int = 1,
    refresh: bool = False,
    start: date | None = None,
) -> pl.DataFrame:
    symbols = list(dict.fromkeys(symbols))
    cached = []
    for path in cache_paths:
        if not path.exists():
            continue
        frame = load_market_data(path)
        if set(required_columns).issubset(frame.columns):
            cached.append(frame.select(required_columns))

    market_data = _combine_market_data(cached, required_columns)
    valid_data = market_data.drop_nulls(required_columns)
    available = set(
        valid_data.group_by("Symbol")
        .len()
        .filter(pl.col("len") >= minimum_sessions)
        .get_column("Symbol")
        .to_list()
    )
    symbols_to_download = (
        symbols if refresh else [symbol for symbol in symbols if symbol not in available]
    )
    if symbols_to_download:
        downloaded = (
            get_market_history(symbols_to_download, start)
            if start is not None
            else get_market_history(symbols_to_download)
        )
        existing_writable = (
            load_market_data(writable_cache_path)
            if writable_cache_path.exists()
            else pl.DataFrame(schema=MARKET_DATA_SCHEMA)
        )
        persisted = (
            pl.concat([existing_writable, downloaded], how="diagonal_relaxed")
            .unique(subset=["Date", "Symbol"], keep="last", maintain_order=True)
            .sort(["Date", "Symbol"])
        )
        save_market_data(persisted, writable_cache_path)
        market_data = _combine_market_data(
            [market_data, downloaded.select(required_columns)],
            required_columns,
        )

    return market_data.filter(pl.col("Symbol").is_in(symbols))


def _combine_market_data(
    frames: list[pl.DataFrame],
    columns: list[str],
) -> pl.DataFrame:
    if not frames:
        return pl.DataFrame(
            schema={column: MARKET_DATA_SCHEMA[column] for column in columns}
        )
    return (
        pl.concat(frames)
        .unique(subset=["Date", "Symbol"], keep="last", maintain_order=True)
        .sort(["Date", "Symbol"])
    )
