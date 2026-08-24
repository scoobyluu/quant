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

Analyze specific symbols with explicit trading-session windows and price basis:

```sh
uv run quant analyze AAPL MSFT --windows 5 20 --price adjusted
```

Use `--index` instead of ticker arguments to analyze the full cached S&P 500.
Market analysis calculates daily price changes, simple moving averages, rolling
highs/lows, volume averages, and relative volume. Full OHLC, adjusted close, and
volume history is cached in `data/market_analysis.parquet`.

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

### Batch quote endpoints

Fetching many symbols in one HTTP call avoids the browser's per-origin
connection cap and lets the server parallelize upstream requests:

```sh
# every symbol in the persisted watchlist
curl -s http://127.0.0.1:8001/api/watchlist/quotes

# ad-hoc list (comma-separated, capped at 20)
curl -s 'http://127.0.0.1:8001/api/quotes?symbols=AAPL,MSFT,NVDA'
```

Both return `{"quotes": [...]}` (watchlist also returns `"symbols"`). A symbol
whose upstream fetch fails comes back as `{"symbol": "XYZ", "error": true}`
instead of taking the whole response down.

### Other API examples

Per-symbol reads:

```sh
curl -s http://127.0.0.1:8001/api/quote/AAPL
curl -s 'http://127.0.0.1:8001/api/history/AAPL?range=1y&interval=1d'
curl -s http://127.0.0.1:8001/api/info/AAPL
curl -s http://127.0.0.1:8001/api/analyst/AAPL
curl -s http://127.0.0.1:8001/api/earnings/AAPL
curl -s http://127.0.0.1:8001/api/options/AAPL
```

Aggregated news (defaults to the watchlist; pass `?symbols=` to override):

```sh
curl -s http://127.0.0.1:8001/api/news
curl -s 'http://127.0.0.1:8001/api/news?symbols=AAPL,MSFT'
curl -s http://127.0.0.1:8001/api/news/AAPL
```

Watchlist management:

```sh
curl -s http://127.0.0.1:8001/api/watchlist
curl -s -X POST http://127.0.0.1:8001/api/watchlist \
  -H 'content-type: application/json' \
  -d '{"symbol":"NVDA"}'
curl -s -X DELETE http://127.0.0.1:8001/api/watchlist/NVDA
```

Holdings management (`costBasis` is per-share):

```sh
curl -s http://127.0.0.1:8001/api/holdings
curl -s -X POST http://127.0.0.1:8001/api/holdings \
  -H 'content-type: application/json' \
  -d '{"symbol":"AAPL","shares":10,"costBasis":150.25}'
curl -s -X DELETE http://127.0.0.1:8001/api/holdings/<holding-id>
```

Symbol search and health:

```sh
curl -s 'http://127.0.0.1:8001/api/search?q=apple'
curl -s http://127.0.0.1:8001/api/health
```

### Analytics endpoints

Risk-adjusted return metrics computed from adjusted-close history. All three
endpoints share a 15-minute price-history cache, so subsequent calls are
cheap.

```sh
# Per-symbol: Sharpe, Sortino, vol, max drawdown, 1M/YTD returns, beta vs SPY.
curl -s 'http://127.0.0.1:8001/api/analytics/AAPL?period=1y'

# Every watchlist symbol: 1M %, YTD %, Sharpe, and annualized vol (1Y window).
curl -s http://127.0.0.1:8001/api/watchlist/analytics

# Portfolio-level: KPIs, cumulative return curve vs SPY, correlation matrix,
# and per-holding weight / return / return-contribution / risk-contribution.
curl -s 'http://127.0.0.1:8001/api/portfolio/analytics?period=1y'
```

Modeling notes:

- Portfolio return series assumes today's shares were held throughout `period`
  (standard dashboard simplification, not a real historical P&L).
- Sharpe / Sortino are excess-over-zero — no risk-free rate applied.
- Beta is regressed on daily returns aligned by date; alpha is the annualized
  daily intercept.
- Risk contributions sum to ≈1 (not exactly, since the portfolio return series
  reflects drifting weights while the decomposition uses current weights).

### Screener

Multi-factor ranking of the watchlist ∪ holdings (or an explicit `symbols=`
list). Each row gets a 0–100 composite score plus a set of boolean signal
chips for at-a-glance scanning.

```sh
# default: watchlist + holdings unioned
curl -s http://127.0.0.1:8001/api/screener

# explicit list (max 30)
curl -s 'http://127.0.0.1:8001/api/screener?symbols=AAPL,MSFT,GOOGL,NVDA'
```

Factor buckets (each 0–100, averaged into the composite; missing factors are
skipped, not zeroed):

- **Value** — forward P/E, PEG, price/book, price/sales, **FCF yield**
- **Quality** — ROE, profit margin, revenue growth, debt/equity
- **Return** — 1Y Sharpe and annualized alpha vs SPY
- **Momentum** — 1M, YTD, and 1Y return

**Value and Quality are scored by sector-relative percentile.** A P/E of 15 is
cheap for a bank but expensive for software, so scoring every ticker against
an absolute P/E scale produces nonsense. Each factor is instead ranked within
the ticker's sector against a hardcoded universe of ~45 sector representatives
(fetched in parallel and cached). Return and Momentum stay on absolute scales
— Sharpe > 1 is a market truth, not a sector one. When a sector has < 3 peers
in the universe (unusual / ETF), scoring falls back to the absolute
piecewise-linear scale.

The response includes:

- `rows` — sorted by composite score, descending
- `sectorPeerCounts` — how many peers were available per sector per factor
  (a coverage hint for interpretation)
- `scoring.value` / `scoring.quality` per row — which basis (`sector` or
  `absolute`) each factor scored on, useful for debugging

Signals emitted per row:

| Signal    | Rule of thumb                                             |
|-----------|-----------------------------------------------------------|
| `value`   | Forward P/E < 20 AND (PEG < 1.5 OR P/B < 3)               |
| `cheap`   | Forward P/E < 15                                          |
| `fcfy+`   | FCF yield > 5% (strong value signal, hard to fake)        |
| `quality` | ROE > 15% AND profit margin > 10%                         |
| `growth`  | Revenue growth > 10%                                      |
| `momentum`| 1M > 0 AND YTD > 0                                        |
| `sharpe+` | 1Y Sharpe > 1.0                                           |
| `alpha+`  | Annualized alpha vs SPY > 2%                              |
| `upside`  | Analyst mean target > 15% above current price             |

A row lighting up ≥3 chips is the multi-factor case.

### Holdings seeding

On first run, `src/quant/dashboard/data/holdings.json` is auto-populated from `portfolio.csv` at the repo root. Only the `ticker`, `quantity`, and `cost` columns are read; extras are ignored.

Edit `portfolio.csv` before the first launch, or manage holdings through the UI afterwards.

## Tests

```sh
uv run python -m pytest
```

## Notes

- `yfinance` is unofficial and rate-limited; expect occasional 5xx responses from Yahoo.
- The dashboard keeps a 30s in-memory cache (news feed is 5min, company names 24h) and serves stale data for up to 1h on upstream failure.
- Watchlist and holdings persist to `src/quant/dashboard/data/*.json` (git-ignored).
