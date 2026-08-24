# Repository Guide

## Commands

- Use Python 3.14 through `uv`; install/sync with `uv sync`.
- Run all tests with `PYTHONDONTWRITEBYTECODE=1 uv run python -m unittest discover -s tests`.
- Run one test with `PYTHONDONTWRITEBYTECODE=1 uv run python -m unittest tests.test_market_data.LatestMarketDataTests.test_downloads_all_symbols_in_one_batch`.
- `uv run quant index` reads `data/sp500_market_data.parquet`; if absent, it downloads data and prints roughly 500 rows. Add `--refresh` for live Wikipedia/Yahoo requests or `--cache PATH` for an isolated cache.
- Market analysis requires explicit choices: `uv run quant market AAPL MSFT --windows 5 20 --price adjusted`; replace tickers with `--index` for the cached S&P 500 universe.
- `uv run quant-dashboard` serves the FastAPI dashboard at `http://127.0.0.1:8001`; routes under `/api/*` must delegate portfolio and indicator work to shared services rather than recalculate it.
- No lint, formatter, or typecheck command is configured; do not claim those checks ran.

## Boundaries

- The `quant` console script resolves to `main()` in `src/quant/cli.py`; `src/quant/__init__.py` has no CLI logic.
- `src/quant/market_data.py` owns constituent discovery, Yahoo batching, symbol normalization (`BRK.B` -> `BRK-B`), and conversion to Polars.
- `src/quant/storage.py` owns atomic Parquet reads/writes. Full market bars use `Date`, `Symbol`, `Open`, `High`, `Low`, `Close`, `Adjusted Close`, `Last Price`, and `Volume`; index data also has `Company`.
- `src/quant/analysis.py` contains pure Polars calculations; `src/quant/portfolio.py` owns cache-first portfolio quote resolution and shared position analysis.
- `src/quant/quotes.py` is the shared cache-first resolver used by portfolio and market analysis. `src/quant/market_analysis.py` owns market-analysis orchestration, not indicator math.
- `src/quant/dashboard/services.py` adapts shared Polars analysis for the API. `src/quant/user_data.py` owns SQLite positions and watchlists; FastAPI routes must not read or write persistence directly.
- `src/quant/dashboard/research.py` confines Yahoo research/intraday data and its short-lived stale cache. `src/quant/dashboard/server.py` should remain a thin FastAPI adapter.
- Keep project-facing data as `polars.DataFrame`. `yfinance` returns pandas internally, but pandas must remain confined to that dependency boundary; do not add pandas imports or direct pandas dependencies.
- Unit tests must not call Wikipedia or Yahoo. Mock the `yfinance` boundary and use temporary paths for storage tests.

## Data

- `data/*.parquet`, `data/*.db*`, and `__pycache__/` are generated and ignored; do not commit them.
- `data/quant.db` is the source of truth for portfolio position lots and watchlists, shared by the CLI and dashboard.
