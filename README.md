# Quant

Market data is represented and processed with Polars, then cached locally as
Parquet for analysis and repeatable testing.

Use the local cache, downloading the latest five trading sessions if it does not
exist:

```sh
uv run quant
```

Refresh the cache from Yahoo Finance:

```sh
uv run quant --refresh
```

The default cache is `data/sp500_market_data.parquet`. Each row contains a
trading date, symbol, company, closing price, and volume.

Analyze `portfolio.csv` using cached prices first:

```sh
uv run quant portfolio
```

The portfolio CSV columns are `ticker`, `quantity`, and `cost`, where `cost` is
the average cost per share. Symbols absent from the S&P 500 cache are downloaded
in one batch and saved to `data/portfolio_market_data.parquet`.

Analyze specific symbols with explicit trading-session windows and price basis:

```sh
uv run quant analyze AAPL MSFT --windows 5 20 --price adjusted
```

Use `--index` instead of ticker arguments to analyze the full cached S&P 500.
Market analysis calculates daily price changes, simple moving averages, rolling
highs/lows, volume averages, and relative volume. Full OHLC, adjusted close, and
volume history is cached in `data/market_analysis.parquet`.
