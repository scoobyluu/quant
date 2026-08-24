# Quant

Quant is a cache-first portfolio and market-analysis project built with Polars.
It provides a terminal CLI and a FastAPI dashboard backed by shared analysis and
market-data services.

## Install

Python 3.14 is pinned in `.python-version`. Install dependencies with:

```sh
uv sync
```

## CLI

Print the latest cached S&P 500 constituent quotes:

```sh
uv run quant index
```

Refresh the index cache from Yahoo Finance:

```sh
uv run quant index --refresh
```

Analyze the SQLite portfolio using cached prices first:

```sh
uv run quant portfolio
```

Use an alternate SQLite database when needed:

```sh
uv run quant portfolio --database /path/to/quant.db
```

Analyze selected symbols with explicit trading-session windows and price basis:

```sh
uv run quant market AAPL MSFT --windows 5 20 --price adjusted
```

Analyze the full cached S&P 500:

```sh
uv run quant market --index --windows 5 20 --price adjusted
```

Market analysis calculates daily price changes, moving averages, rolling
highs/lows, volume averages, and relative volume.

Portfolio positions are stored as individual lots in SQLite. Each lot records
the symbol, quantity, average cost, and optional account, asset class, sector,
and acquired date metadata.

## Dashboard

Run the FastAPI dashboard:

```sh
uv run quant-dashboard
```

Open http://127.0.0.1:8001. Override the bind address when needed:

```sh
HOST=0.0.0.0 PORT=9000 uv run quant-dashboard
```

Routes:

- `/` serves the dashboard.
- `/api/*` provides JSON APIs.
- `/docs` provides generated API documentation.

The dashboard includes a watchlist, holdings, stock history, fundamentals,
analyst ratings, earnings, options, and news.

FastAPI routes delegate portfolio and technical calculations to the shared
Polars services. Yahoo research and intraday responses are isolated behind a
separate provider boundary.

The CLI and dashboard both use `data/quant.db` as the source of truth for
position lots and watchlists. Positions are added and removed through the
dashboard API or by writing to the repository; CSV is no longer part of the
runtime portfolio workflow.

## Storage

- `data/sp500_market_data.parquet` stores cached index bars.
- `data/portfolio_market_data.parquet` stores supplemental portfolio quotes.
- `data/market_analysis.parquet` stores full bars used by market analysis.
- `data/quant.db` stores app-managed position lots and watchlists.

## Tests

```sh
PYTHONDONTWRITEBYTECODE=1 uv run python -m unittest discover -s tests
```

Unit tests must mock Yahoo and Wikipedia boundaries; they should not require
network access.

See `INTEGRATION_REVIEW.md` for the merged PR inventory, consolidated ownership,
and intentional CLI/web differences that remain open for product review.
