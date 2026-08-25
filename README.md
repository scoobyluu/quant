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

- `/` — UI (watchlist at `#/`, holdings at `#/holdings`, screener at `#/screener`, stock detail at `#/stock/AAPL`)
- `/api/*` — JSON API
- `/docs` — auto-generated Swagger docs

FastAPI routes delegate portfolio and technical calculations to the shared
Polars services. Yahoo research and intraday responses are isolated behind a
separate provider boundary.

The CLI and dashboard both use `data/quant.db` as the source of truth for
position lots and watchlists. Positions are added and removed through the
dashboard API or by writing to the repository; CSV is no longer part of the
runtime portfolio workflow.

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
curl -s 'http://127.0.0.1:8001/api/history/AAPL?period=1y&interval=1d'
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
- **Momentum** — 1M, YTD, and **12-1** (12M return excluding the most recent
  month, the standard academic-momentum construction — recent-month returns
  mean-revert, so pure 12M smuggles reversal noise into the signal)

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
- `coverage` per row — `{used, total, ratio}` counting how many of the 14
  possible factor inputs were available. Rows below 50% coverage are dimmed
  in the UI since the composite is being averaged over a thin subset.
- `compositeScore` is **coverage-weighted**: the raw mean of the four bucket
  scores is multiplied by `sqrt(coverage.ratio)` so a thin row can't outrank
  a well-covered one on the strength of a small sample. Full coverage passes
  through unchanged (`sqrt(1.0) = 1.0`); 50% coverage scales to ~0.71x. The
  un-weighted value is also returned as `rawCompositeScore`.

Signals emitted per row:

| Signal    | Rule of thumb                                             |
|-----------|-----------------------------------------------------------|
| `value`   | Forward P/E < 20 AND (PEG < 1.5 OR P/B < 3)               |
| `cheap`   | Forward P/E < 15                                          |
| `trap`    | Would-be `value`/`cheap` BUT revenue < 0 OR earnings < -10% (value-trap guard: replaces the value/cheap chips) |
| `fcfy+`   | FCF yield > 5% (strong value signal, hard to fake)        |
| `quality` | ROE > 15% AND profit margin > 10%                         |
| `growth`  | Revenue growth > 10%                                      |
| `momentum`| 1M > 0 AND YTD > 0                                        |
| `sharpe+` | 1Y Sharpe > 1.0                                           |
| `alpha+`  | Annualized alpha vs SPY > 2%                              |
| `upside`  | Analyst mean target > 15% above current price             |

A row lighting up ≥3 chips is the multi-factor case. A `trap` chip is a
warning: the stock looks statistically cheap but the top or bottom line is
shrinking — the classic value-trap pattern.

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

## Notes

- `yfinance` is unofficial and rate-limited; expect occasional 5xx responses from Yahoo.
- The dashboard keeps a 30s in-memory research cache (news feed is 5min, analytics 15min) and serves stale data for up to 1h on upstream failure.
