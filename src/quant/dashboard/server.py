from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from quant.dashboard.research import YahooResearchService, clean_json
from quant.dashboard.services import DashboardService


app = FastAPI(title="Quant Dashboard API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_dashboard_service = DashboardService()
_research_service = YahooResearchService()


class WatchlistAdd(BaseModel):
    symbol: str


class HoldingIn(BaseModel):
    symbol: str
    shares: float
    costBasis: float
    account: str | None = None
    assetClass: str | None = None
    sector: str | None = None
    acquired: str | None = None


def _research(producer: Callable[[], dict]) -> dict:
    try:
        return producer()
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"yfinance error: {error}") from error


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/quotes")
def quotes(symbols: str = Query(..., description="Comma-separated symbols")) -> dict:
    parsed = [symbol.strip().upper() for symbol in symbols.split(",") if symbol.strip()]
    return _research(lambda: _research_service.quotes(parsed))


@app.get("/api/quote/{symbol}")
def quote(symbol: str) -> dict:
    return _research(lambda: _research_service.quote(symbol))


@app.get("/api/history/{symbol}")
def history(
    symbol: str,
    period: str = Query("1mo"),
    interval: str = Query("1d"),
) -> dict:
    return _research(lambda: _research_service.history(symbol, period, interval))


@app.get("/api/info/{symbol}")
def info(symbol: str) -> dict:
    return _research(lambda: _research_service.info(symbol))


@app.get("/api/analyst/{symbol}")
def analyst(symbol: str) -> dict:
    return _research(lambda: _research_service.analyst(symbol))


@app.get("/api/earnings/{symbol}")
def earnings(symbol: str) -> dict:
    return _research(lambda: _research_service.earnings(symbol))


@app.get("/api/options/{symbol}")
def options(symbol: str, expiration: str | None = None) -> dict:
    return _research(lambda: _research_service.options(symbol, expiration))


@app.get("/api/news/{symbol}")
def news(symbol: str) -> dict:
    return _research(lambda: _research_service.news(symbol))


@app.get("/api/news")
def news_feed(
    symbols: str | None = Query(None, description="Comma-separated symbols"),
) -> dict:
    parsed = (
        [symbol.strip().upper() for symbol in symbols.split(",") if symbol.strip()]
        if symbols
        else _dashboard_service.watchlist()
    )
    return _research(lambda: _research_service.news_feed(parsed))


@app.get("/api/search")
def search(q: str = Query(..., min_length=1)) -> dict:
    return _research(lambda: _research_service.search(q))


@app.get("/api/watchlist")
def watchlist_get() -> dict:
    return {"symbols": _dashboard_service.watchlist()}


@app.post("/api/watchlist")
def watchlist_add(body: WatchlistAdd) -> dict:
    try:
        return {"symbols": _dashboard_service.add_watchlist(body.symbol)}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.delete("/api/watchlist/{symbol}")
def watchlist_remove(symbol: str) -> dict:
    return {"symbols": _dashboard_service.remove_watchlist(symbol)}


@app.get("/api/holdings")
def holdings_list(refresh: bool = False) -> dict:
    try:
        return clean_json(_dashboard_service.holdings(refresh))
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.post("/api/holdings")
def holdings_add(body: HoldingIn) -> dict:
    try:
        return _dashboard_service.add_holding(
            body.symbol,
            body.shares,
            body.costBasis,
            body.account,
            body.assetClass,
            body.sector,
            body.acquired,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.delete("/api/holdings/{holding_id}")
def holdings_remove(holding_id: str) -> dict:
    return {"removed": int(_dashboard_service.remove_holding(holding_id))}


@app.get("/api/analysis/{symbol}")
def technical_analysis(
    symbol: str,
    windows: str = Query(..., description="Comma-separated trading sessions"),
    price: str = Query(..., pattern="^(close|adjusted)$"),
    refresh: bool = False,
) -> dict:
    try:
        parsed_windows = list(
            dict.fromkeys(int(value.strip()) for value in windows.split(","))
        )
        if not parsed_windows or any(window <= 0 for window in parsed_windows):
            raise ValueError("Windows must be positive integers")
        return clean_json(
            _dashboard_service.market_analysis(
                symbol, parsed_windows, price, refresh
            )
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def run() -> None:
    import os

    import uvicorn

    uvicorn.run(
        "quant.dashboard.server:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8001")),
        reload=False,
    )
