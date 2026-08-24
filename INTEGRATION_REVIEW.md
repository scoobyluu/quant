# Integration Review

## Merged Work

- PR #1 (`UI v1`) is present: FastAPI, watchlist, holdings, stock detail,
  fundamentals, analyst data, earnings, options, news, search, and static UI.
- PR #2 (`Add market analysis indicators`) is present: full daily bars, shared
  quote resolution, moving averages, changes, rolling levels, and volume metrics.
- PR #3 was closed after its merge commit and portfolio metadata were incorporated
  into PR #4.
- PR #4 reconciled the dashboard, CLI, analysis engine, portfolio metadata,
  SQLite user data, and shared API services into `master`.

## Consolidated Boundaries

- `analysis.py` contains pure Polars portfolio and market calculations.
- `portfolio.py` owns shared cache-first portfolio analysis orchestration used by
  both CLI and dashboard.
- `market_analysis.py` owns shared technical-analysis orchestration used by CLI
  and API.
- `quotes.py` owns persistent daily-bar cache resolution and targeted downloads.
- `dashboard/services.py` adapts shared analysis results to web response shapes.
- `dashboard/research.py` owns short-lived Yahoo research and intraday data.
- `user_data.py` owns app-managed SQLite positions and watchlists.
- `dashboard/server.py` is a thin HTTP/static-file adapter.

## Intentionally Preserved Differences

| Area | CLI / core | Web dashboard | Review question |
| --- | --- | --- | --- |
| Portfolio source | Reads SQLite position lots | Reads SQLite position lots | Unified in this change |
| Position detail | Preserves individual lots internally; terminal output remains position-oriented | Preserves account, asset class, sector, acquired date, and lot IDs | Should CLI expose lot and allocation views? |
| Market prices | Persistent daily Parquet bars | Same bars for holdings and indicators; live quotes use a short TTL | Is the daily/live freshness distinction clear enough in UI copy? |
| History | Daily analysis history through Polars | Intraday/arbitrary-period chart history through Yahoo research boundary | Should daily chart requests prefer Parquet before Yahoo? |
| Indicators | User-supplied windows and price basis | Stock chart requests fixed 50/200 close-based averages | Should web controls expose windows and adjusted/close selection? |
| Output contract | Polars frames formatted for terminal | JSON with camelCase fields for browser compatibility | Keep camelCase or standardize API fields later? |

The remaining differences are deliberate presentation and freshness boundaries,
not separate portfolio sources. CSV portfolio import has been removed from the
runtime code; SQLite is now shared by the CLI and dashboard.
