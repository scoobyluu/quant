# Repository Guide

## Commands

- Use Python 3.14 through `uv`; install/sync with `uv sync`.
- Run all tests with `PYTHONDONTWRITEBYTECODE=1 uv run python -m unittest discover -s tests`.
- Run one test with `PYTHONDONTWRITEBYTECODE=1 uv run python -m unittest tests.test_market_data.LatestMarketDataTests.test_downloads_all_symbols_in_one_batch`.
- `uv run quant` reads `data/sp500_market_data.parquet`; if absent, it downloads data and prints roughly 500 rows.
- `uv run quant --refresh` makes live Wikipedia/Yahoo requests and replaces the cache with the latest five trading sessions. Use `--cache PATH` for an isolated cache.
- Market analysis requires explicit choices: `uv run quant analyze AAPL MSFT --windows 5 20 --price adjusted`; replace tickers with `--index` for the cached S&P 500 universe.
- No lint, formatter, or typecheck command is configured; do not claim those checks ran.

## Boundaries

- The `quant` console script resolves to `main()` in `src/quant/__init__.py`.
- `src/quant/market_data.py` owns constituent discovery, Yahoo batching, symbol normalization (`BRK.B` -> `BRK-B`), and conversion to Polars.
- `src/quant/storage.py` owns atomic Parquet reads/writes. Full market bars use `Date`, `Symbol`, `Open`, `High`, `Low`, `Close`, `Adjusted Close`, `Last Price`, and `Volume`; index data also has `Company`.
- `src/quant/analysis.py` contains pure Polars calculations; `src/quant/portfolio.py` owns portfolio CSV normalization and cache-first quote resolution.
- `src/quant/quotes.py` is the shared cache-first resolver used by portfolio and market analysis. `src/quant/market_analysis.py` owns market-analysis orchestration, not indicator math.
- Keep project-facing data as `polars.DataFrame`. `yfinance` returns pandas internally, but pandas must remain confined to that dependency boundary; do not add pandas imports or direct pandas dependencies.
- Unit tests must not call Wikipedia or Yahoo. Mock the `yfinance` boundary and use temporary paths for storage tests.

## Data

- `data/*.parquet` and `__pycache__/` are generated and ignored; do not commit them.
- `portfolio.csv` uses `ticker,quantity,cost`, with `cost` interpreted as average cost per share. Treat it as user-owned input; read it but do not rewrite it.
