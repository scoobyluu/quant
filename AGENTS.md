# Repository Guide

## Commands

- Use Python 3.14 through `uv`; install/sync with `uv sync`.
- Run all tests with `PYTHONDONTWRITEBYTECODE=1 uv run python -m unittest discover -s tests`.
- Run one test with `PYTHONDONTWRITEBYTECODE=1 uv run python -m unittest tests.test_market_data.LatestMarketDataTests.test_downloads_all_symbols_in_one_batch`.
- `uv run quant` reads `data/sp500_market_data.parquet`; if absent, it downloads data and prints roughly 500 rows.
- `uv run quant --refresh` makes live Wikipedia/Yahoo requests and replaces the cache with the latest five trading sessions. Use `--cache PATH` for an isolated cache.
- No lint, formatter, or typecheck command is configured; do not claim those checks ran.

## Boundaries

- The `quant` console script resolves to `main()` in `src/quant/__init__.py`.
- `src/quant/market_data.py` owns constituent discovery, Yahoo batching, symbol normalization (`BRK.B` -> `BRK-B`), and conversion to Polars.
- `src/quant/storage.py` owns atomic Parquet reads/writes. Stored columns are `Date`, `Symbol`, `Company`, `Last Price`, and `Volume`.
- Keep project-facing data as `polars.DataFrame`. `yfinance` returns pandas internally, but pandas must remain confined to that dependency boundary; do not add pandas imports or direct pandas dependencies.
- Unit tests must not call Wikipedia or Yahoo. Mock the `yfinance` boundary and use temporary paths for storage tests.

## Data

- `data/*.parquet` and `__pycache__/` are generated and ignored; do not commit them.
- `portfolio.csv` is not yet consumed by application code. Treat it as user-owned input and leave it unchanged unless the task explicitly targets portfolio ingestion.
