import argparse
from pathlib import Path

import polars as pl

from quant.analysis import summarize_portfolio
from quant.market_data import get_sp500_market_history, latest_market_snapshot
from quant.portfolio import (
    DEFAULT_PORTFOLIO_MARKET_DATA_PATH,
    DEFAULT_PORTFOLIO_PATH,
    analyze_portfolio_file,
)
from quant.storage import DEFAULT_MARKET_DATA_PATH, load_market_data, save_market_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Market and portfolio analysis")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("market", "portfolio"),
        default="market",
    )
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
    parser.add_argument(
        "--portfolio",
        type=Path,
        default=DEFAULT_PORTFOLIO_PATH,
        help="path to the portfolio CSV",
    )
    parser.add_argument(
        "--portfolio-cache",
        type=Path,
        default=DEFAULT_PORTFOLIO_MARKET_DATA_PATH,
        help="path to the supplemental portfolio market-data cache",
    )
    args = parser.parse_args()

    if args.command == "portfolio":
        _print_portfolio_analysis(
            analyze_portfolio_file(
                args.portfolio,
                args.cache,
                args.portfolio_cache,
                args.refresh,
            )
        )
        return

    _print_market_data(args.cache, args.refresh)


def _print_market_data(cache: Path, refresh: bool) -> None:
    if refresh or not cache.exists():
        history = get_sp500_market_history()
        save_market_data(history, cache)
        print(f"Downloaded and cached {history.height:,} rows at {cache}")
    else:
        history = load_market_data(cache)
        print(f"Loaded {history.height:,} cached rows from {cache}")

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


def _print_portfolio_analysis(analysis: pl.DataFrame) -> None:
    summary = summarize_portfolio(analysis).row(0, named=True)
    total_return = summary["Gain/Loss %"]
    formatted_return = f"{total_return:,.2f}%" if total_return is not None else "N/A"
    display = analysis.select(
        "Symbol",
        "Quantity",
        "Average Cost",
        "Last Price",
        "Market Value",
        "Gain/Loss",
        "Gain/Loss %",
        "Weight %",
        "As Of",
    ).with_columns(
        pl.col("Quantity").map_elements(
            lambda value: f"{value:,.4f}".rstrip("0").rstrip("."),
            return_dtype=pl.String,
        ),
        pl.col("Average Cost", "Last Price", "Market Value", "Gain/Loss")
        .map_elements(
            lambda value: (
                f"-${abs(value):,.2f}" if value < 0 else f"${value:,.2f}"
            ),
            return_dtype=pl.String,
        ),
        pl.col("Gain/Loss %", "Weight %")
        .map_elements(lambda value: f"{value:,.2f}%", return_dtype=pl.String)
        .fill_null("N/A"),
    )

    print(f"Portfolio analysis ({analysis.height} positions)")
    print(
        f"Market value: ${summary['Market Value']:,.2f} | "
        f"Cost basis: ${summary['Cost Basis']:,.2f} | "
        f"Gain/loss: ${summary['Gain/Loss']:,.2f} "
        f"({formatted_return})"
    )
    with pl.Config(
        tbl_rows=-1,
        tbl_cols=-1,
        tbl_width_chars=180,
        fmt_str_lengths=40,
        tbl_hide_dataframe_shape=True,
        tbl_hide_column_data_types=True,
    ):
        print(display)
