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

Analyze `portfolio.csv` using cached prices first:

```sh
uv run quant portfolio
```

Analyze selected symbols with explicit trading-session windows and price basis:

```sh
uv run quant market AAPL MSFT --windows 5 20 --price adjusted
```

Analyze the full cached S&P 500:

```sh
uv run quant market --index --windows 5 20 --price adjusted
```

The portfolio CSV requires `ticker`, `quantity`, and `cost`, where `cost` is the
average cost per share. Additional portfolio metadata columns are preserved as
import data for the dashboard.

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

The dashboard currently includes a watchlist, holdings, stock history,
fundamentals, analyst ratings, earnings, options, and news.

## Storage

- `data/sp500_market_data.parquet` stores cached index bars.
- `data/portfolio_market_data.parquet` stores supplemental portfolio quotes.
- `data/market_analysis.parquet` stores full bars used by market analysis.
- `portfolio.csv` is the current import format for portfolio positions.

## Tests

```sh
PYTHONDONTWRITEBYTECODE=1 uv run python -m unittest discover -s tests
```

Unit tests must mock Yahoo and Wikipedia boundaries; they should not require
network access.
