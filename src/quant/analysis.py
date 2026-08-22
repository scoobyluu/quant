import polars as pl

from quant.market_data import latest_market_snapshot


def analyze_portfolio(
    positions: pl.DataFrame,
    market_history: pl.DataFrame,
) -> pl.DataFrame:
    _require_columns(positions, {"Symbol", "Quantity", "Average Cost"}, "positions")
    _require_columns(
        market_history,
        {"Date", "Symbol", "Last Price"},
        "market history",
    )
    if positions.get_column("Symbol").n_unique() != positions.height:
        raise ValueError("Portfolio positions must contain one row per symbol")
    if positions.filter(
        (pl.col("Quantity") <= 0)
        | (pl.col("Average Cost") < 0)
        | pl.col("Quantity").is_null()
        | pl.col("Average Cost").is_null()
    ).height:
        raise ValueError("Portfolio quantities must be positive and costs non-negative")

    quotes = latest_market_snapshot(market_history).select(
        "Symbol",
        pl.col("Date").alias("As Of"),
        "Last Price",
    )
    analysis = positions.join(quotes, on="Symbol", how="left", validate="1:1")
    missing = analysis.filter(pl.col("Last Price").is_null()).get_column("Symbol")
    if not missing.is_empty():
        raise ValueError(f"Missing market data for: {', '.join(missing.to_list())}")

    analysis = (
        analysis.with_columns(
            (pl.col("Quantity") * pl.col("Average Cost")).alias("Cost Basis"),
            (pl.col("Quantity") * pl.col("Last Price")).alias("Market Value"),
        )
        .with_columns(
            (pl.col("Market Value") - pl.col("Cost Basis")).alias("Gain/Loss"),
            pl.when(pl.col("Cost Basis") > 0)
            .then(
                (pl.col("Market Value") / pl.col("Cost Basis") - 1.0) * 100.0
            )
            .otherwise(None)
            .alias("Gain/Loss %"),
        )
    )
    total_value = analysis.get_column("Market Value").sum()
    if total_value is None or total_value <= 0:
        raise ValueError("Portfolio market value must be positive")

    return analysis.with_columns(
        (pl.col("Market Value") / total_value * 100.0).alias("Weight %")
    ).sort("Weight %", descending=True)


def summarize_portfolio(analysis: pl.DataFrame) -> pl.DataFrame:
    _require_columns(
        analysis,
        {"Cost Basis", "Market Value", "Gain/Loss"},
        "portfolio analysis",
    )
    return (
        analysis.select(
            pl.col("Cost Basis").sum().alias("Cost Basis"),
            pl.col("Market Value").sum().alias("Market Value"),
            pl.col("Gain/Loss").sum().alias("Gain/Loss"),
        )
        .with_columns(
            pl.when(pl.col("Cost Basis") > 0)
            .then(pl.col("Gain/Loss") / pl.col("Cost Basis") * 100.0)
            .otherwise(None)
            .alias("Gain/Loss %")
        )
    )


def _require_columns(
    frame: pl.DataFrame,
    required: set[str],
    label: str,
) -> None:
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing {label} columns: {', '.join(sorted(missing))}")
