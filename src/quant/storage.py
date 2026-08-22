from pathlib import Path

import polars as pl


DEFAULT_MARKET_DATA_PATH = Path("data/sp500_market_data.parquet")


def load_market_data(path: Path = DEFAULT_MARKET_DATA_PATH) -> pl.DataFrame:
    return pl.read_parquet(path)


def save_market_data(
    market_data: pl.DataFrame,
    path: Path = DEFAULT_MARKET_DATA_PATH,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    market_data.write_parquet(temporary_path)
    temporary_path.replace(path)
