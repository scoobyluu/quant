import argparse
from pathlib import Path

import polars as pl

from quant.market_data import get_sp500_market_history, latest_market_snapshot
from quant.storage import DEFAULT_MARKET_DATA_PATH, load_market_data, save_market_data


def main() -> None:
    parser = argparse.ArgumentParser(description="S&P 500 market data")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="download fresh data instead of using the local cache",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_MARKET_DATA_PATH,
        help="path to the Parquet cache",
    )
    args = parser.parse_args()

    if args.refresh or not args.cache.exists():
        history = get_sp500_market_history()
        save_market_data(history, args.cache)
        print(f"Downloaded and cached {history.height:,} rows at {args.cache}")
    else:
        history = load_market_data(args.cache)
        print(f"Loaded {history.height:,} cached rows from {args.cache}")

    snapshot = latest_market_snapshot(history)
    display = snapshot.with_columns(
        pl.col("Last Price")
        .map_elements(lambda value: f"{value:,.2f}", return_dtype=pl.String)
        .fill_null("N/A"),
        pl.col("Volume")
        .map_elements(lambda value: f"{value:,}", return_dtype=pl.String)
        .fill_null("N/A"),
    )

    print(f"Latest S&P 500 constituent data ({snapshot.height} listings)")
    with pl.Config(
        tbl_rows=-1,
        tbl_cols=-1,
        tbl_width_chars=200,
        fmt_str_lengths=100,
        tbl_hide_dataframe_shape=True,
        tbl_hide_column_data_types=True,
    ):
        print(display)
