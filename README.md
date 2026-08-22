# Quant

Market data is represented and processed with Polars, then cached locally as
Parquet for analysis and repeatable testing. Ships two entry points:

- `quant` — CLI that prints the latest price and volume for every S&P 500 constituent.
- `quant-dashboard` — FastAPI + vanilla-JS SPA with a watchlist, holdings, and per-stock detail (price history, fundamentals, analyst ratings, earnings, options, news).

## Prerequisites

- **Python 3.14+** — the version is pinned in `.python-version`.
- **[uv](https://docs.astral.sh/uv/)** — used for dependency and environment management. Install with:

  ```sh
  # macOS / Linux
  curl -LsSf https://astral.sh/uv/install.sh | sh

  # or via Homebrew
  brew install uv
  ```

  `uv` will fetch the correct Python version automatically on first run.

## Install

Clone the repo, then from the project root:

```sh
uv sync
```

This creates a `.venv/` and installs all dependencies from `uv.lock`.

## Run the CLI

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

## Run the dashboard

```sh
uv run quant-dashboard
```

Open http://127.0.0.1:8001. Override the bind address with env vars:

```sh
HOST=0.0.0.0 PORT=9000 uv run quant-dashboard
```

Routes:

- `/` — UI (watchlist at `#/`, holdings at `#/holdings`, stock detail at `#/stock/AAPL`)
- `/api/*` — JSON API
- `/docs` — auto-generated Swagger docs

### Holdings seeding

On first run, `src/quant/dashboard/data/holdings.json` is auto-populated from `portfolio.csv` at the repo root. Only the `ticker`, `quantity`, and `cost` columns are read; extras are ignored.

Edit `portfolio.csv` before the first launch, or manage holdings through the UI afterwards.

## Tests

```sh
uv run python -m pytest
```

## Notes

- `yfinance` is unofficial and rate-limited; expect occasional 5xx responses from Yahoo.
- The dashboard keeps a 30s in-memory cache and serves stale data for up to 1h on upstream failure.
- Watchlist and holdings persist to `src/quant/dashboard/data/*.json` (git-ignored).
