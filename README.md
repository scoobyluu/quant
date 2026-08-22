# Quant

Market data is represented and processed with Polars. Ships two entry points:

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

Fetch and print the latest price and volume for every S&P 500 constituent:

```sh
uv run quant
```

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

On first run, `src/quant/dashboard/data/holdings.json` is auto-populated from `portfolio.csv` at the repo root. The CSV format is:

```csv
ticker,quantity,cost
AAPL,23,327.82
```

Edit `portfolio.csv` before the first launch, or manage holdings through the UI afterwards.

## Tests

```sh
uv run python -m pytest
```

## Notes

- `yfinance` is unofficial and rate-limited; expect occasional 5xx responses from Yahoo.
- The dashboard keeps a 30s in-memory cache and serves stale data for up to 1h on upstream failure.
- Watchlist and holdings persist to `src/quant/dashboard/data/*.json` (git-ignored).
