import polars as pl

from quant.market_data import get_sp500_market_data


def main() -> None:
    market_data = get_sp500_market_data()
    display = market_data.with_columns(
        pl.col("Last Price")
        .map_elements(lambda value: f"{value:,.2f}", return_dtype=pl.String)
        .fill_null("N/A"),
        pl.col("Volume")
        .map_elements(lambda value: f"{value:,}", return_dtype=pl.String)
        .fill_null("N/A"),
    )

    print(f"S&P 500 constituents ({market_data.height} listings)")
    with pl.Config(
        tbl_rows=-1,
        tbl_cols=-1,
        tbl_width_chars=200,
        fmt_str_lengths=100,
        tbl_hide_dataframe_shape=True,
        tbl_hide_column_data_types=True,
    ):
        print(display)
