from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

import yfinance as yf
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from quant.dashboard.services import DashboardService

try:
    from curl_cffi import requests as curl_requests  # type: ignore

    _SESSION = curl_requests.Session(impersonate="chrome")
except Exception:
    _SESSION = None

app = FastAPI(title="Quant Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_cache: dict[str, tuple[float, Any]] = {}
CACHE_TTL_SECONDS = 30
STALE_TTL_SECONDS = 60 * 60
_dashboard_service = DashboardService()


def _cached(key: str, producer):
    """Return fresh value if cached < TTL; otherwise call producer.

    On producer failure, fall back to a stale cached value (<= STALE_TTL) so a
    transient upstream 429 doesn't blank the UI.
    """
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    try:
        value = producer()
    except Exception:
        if hit and now - hit[0] < STALE_TTL_SECONDS:
            return hit[1]
        raise
    _cache[key] = (now, value)
    return value


def _clean(value: Any) -> Any:
    """Recursively make a value JSON-safe: drop NaN/Inf, stringify weird types."""
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, (int, str, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    try:
        return str(value)
    except Exception:
        return None


def _ticker(symbol: str) -> yf.Ticker:
    if _SESSION is not None:
        return yf.Ticker(symbol.upper(), session=_SESSION)
    return yf.Ticker(symbol.upper())


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/quotes")
def quotes(symbols: str = Query(..., description="Comma-separated symbols")) -> dict:
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    syms = syms[:20]
    results: list[dict] = []
    for sym in syms:
        try:
            results.append(quote(sym))
        except HTTPException:
            results.append({"symbol": sym, "error": True})
    return {"quotes": results}


@app.get("/api/quote/{symbol}")
def quote(symbol: str) -> dict:
    def produce() -> dict:
        t = _ticker(symbol)
        info = t.info or {}
        price = (
            info.get("regularMarketPrice")
            or info.get("currentPrice")
            or info.get("previousClose")
        )
        prev = info.get("regularMarketPreviousClose") or info.get("previousClose")
        change = None
        change_pct = None
        if price is not None and prev:
            change = price - prev
            change_pct = (change / prev) * 100 if prev else None
        return _clean(
            {
                "symbol": symbol.upper(),
                "name": info.get("shortName") or info.get("longName"),
                "price": price,
                "previousClose": prev,
                "change": change,
                "changePercent": change_pct,
                "currency": info.get("currency"),
                "marketState": info.get("marketState"),
                "dayHigh": info.get("dayHigh"),
                "dayLow": info.get("dayLow"),
                "volume": info.get("volume") or info.get("regularMarketVolume"),
            }
        )

    try:
        return _cached(f"quote:{symbol.upper()}", produce)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"yfinance error: {e}")


@app.get("/api/history/{symbol}")
def history(
    symbol: str,
    period: str = Query("1mo", description="1d,5d,1mo,3mo,6mo,1y,2y,5y,10y,ytd,max"),
    interval: str = Query("1d", description="1m,5m,15m,30m,1h,1d,1wk,1mo"),
) -> dict:
    def produce() -> dict:
        df = _ticker(symbol).history(period=period, interval=interval, auto_adjust=False)
        if df.empty:
            return {"symbol": symbol.upper(), "period": period, "interval": interval, "candles": []}
        df = df.reset_index()
        date_col = "Datetime" if "Datetime" in df.columns else "Date"
        candles = []
        for _, row in df.iterrows():
            candles.append(
                {
                    "t": row[date_col].isoformat() if hasattr(row[date_col], "isoformat") else str(row[date_col]),
                    "open": row.get("Open"),
                    "high": row.get("High"),
                    "low": row.get("Low"),
                    "close": row.get("Close"),
                    "volume": row.get("Volume"),
                }
            )
        return _clean(
            {
                "symbol": symbol.upper(),
                "period": period,
                "interval": interval,
                "candles": candles,
            }
        )

    try:
        return _cached(f"hist:{symbol.upper()}:{period}:{interval}", produce)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"yfinance error: {e}")


@app.get("/api/info/{symbol}")
def info(symbol: str) -> dict:
    def produce() -> dict:
        raw = _ticker(symbol).info or {}
        keys = [
            "shortName", "longName", "symbol", "sector", "industry", "country", "website",
            "longBusinessSummary", "fullTimeEmployees",
            "marketCap", "enterpriseValue",
            "trailingPE", "forwardPE", "priceToBook", "priceToSalesTrailing12Months",
            "trailingEps", "forwardEps",
            "dividendRate", "dividendYield", "payoutRatio", "fiveYearAvgDividendYield",
            "beta", "52WeekChange", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
            "fiftyDayAverage", "twoHundredDayAverage",
            "profitMargins", "operatingMargins", "grossMargins", "ebitdaMargins",
            "returnOnAssets", "returnOnEquity",
            "totalRevenue", "revenuePerShare", "revenueGrowth",
            "grossProfits", "ebitda", "netIncomeToCommon",
            "totalCash", "totalDebt", "debtToEquity", "currentRatio", "quickRatio",
            "freeCashflow", "operatingCashflow",
            "sharesOutstanding", "floatShares", "heldPercentInsiders", "heldPercentInstitutions",
            "averageVolume", "averageVolume10days",
        ]
        subset = {k: raw.get(k) for k in keys}
        return _clean(subset)

    try:
        return _cached(f"info:{symbol.upper()}", produce)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"yfinance error: {e}")


@app.get("/api/analyst/{symbol}")
def analyst(symbol: str) -> dict:
    def produce() -> dict:
        t = _ticker(symbol)
        out: dict[str, Any] = {"symbol": symbol.upper()}

        raw = t.info or {}
        out["recommendationKey"] = raw.get("recommendationKey")
        out["recommendationMean"] = raw.get("recommendationMean")
        out["numberOfAnalystOpinions"] = raw.get("numberOfAnalystOpinions")
        out["targetHigh"] = raw.get("targetHighPrice")
        out["targetLow"] = raw.get("targetLowPrice")
        out["targetMean"] = raw.get("targetMeanPrice")
        out["targetMedian"] = raw.get("targetMedianPrice")

        try:
            rec = t.recommendations
            if rec is not None and not rec.empty:
                recent = rec.tail(25).reset_index()
                out["recommendations"] = [
                    {str(k): v for k, v in row.items()}
                    for _, row in recent.iterrows()
                ]
        except Exception:
            out["recommendations"] = []

        try:
            ug = t.upgrades_downgrades
            if ug is not None and not ug.empty:
                ug = ug.head(25).reset_index()
                out["upgradesDowngrades"] = [
                    {str(k): v for k, v in row.items()}
                    for _, row in ug.iterrows()
                ]
        except Exception:
            out["upgradesDowngrades"] = []

        return _clean(out)

    try:
        return _cached(f"analyst:{symbol.upper()}", produce)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"yfinance error: {e}")


@app.get("/api/earnings/{symbol}")
def earnings(symbol: str) -> dict:
    def produce() -> dict:
        t = _ticker(symbol)

        next_earnings: str | None = None
        eps_estimate: dict[str, Any] = {}
        revenue_estimate: dict[str, Any] = {}
        dividend_date: str | None = None
        ex_dividend_date: str | None = None
        try:
            cal = t.calendar
            if isinstance(cal, dict):
                dates = cal.get("Earnings Date")
                if isinstance(dates, list) and dates:
                    d = dates[0]
                    next_earnings = d.isoformat() if hasattr(d, "isoformat") else str(d)
                for lo_key, hi_key, avg_key, out in (
                    ("Earnings Low", "Earnings High", "Earnings Average", eps_estimate),
                    ("Revenue Low", "Revenue High", "Revenue Average", revenue_estimate),
                ):
                    out["low"] = cal.get(lo_key)
                    out["high"] = cal.get(hi_key)
                    out["average"] = cal.get(avg_key)
                dd = cal.get("Dividend Date")
                xd = cal.get("Ex-Dividend Date")
                dividend_date = dd.isoformat() if hasattr(dd, "isoformat") else (str(dd) if dd else None)
                ex_dividend_date = xd.isoformat() if hasattr(xd, "isoformat") else (str(xd) if xd else None)
        except Exception:
            pass

        history_rows: list[dict] = []
        try:
            ed = t.earnings_dates
            if ed is not None and not ed.empty:
                df = ed.reset_index()
                date_col = df.columns[0]
                for _, row in df.head(12).iterrows():
                    d = row[date_col]
                    history_rows.append(
                        {
                            "date": d.isoformat() if hasattr(d, "isoformat") else str(d),
                            "epsEstimate": row.get("EPS Estimate"),
                            "epsReported": row.get("Reported EPS"),
                            "surprisePercent": row.get("Surprise(%)"),
                        }
                    )
        except Exception:
            pass

        return _clean(
            {
                "symbol": symbol.upper(),
                "nextEarnings": next_earnings,
                "epsEstimate": eps_estimate,
                "revenueEstimate": revenue_estimate,
                "dividendDate": dividend_date,
                "exDividendDate": ex_dividend_date,
                "history": history_rows,
            }
        )

    try:
        return _cached(f"earn:{symbol.upper()}", produce)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"yfinance error: {e}")


@app.get("/api/options/{symbol}")
def options(symbol: str, expiration: str | None = None) -> dict:
    def produce() -> dict:
        t = _ticker(symbol)
        try:
            expirations = list(t.options or [])
        except Exception:
            expirations = []
        chosen = expiration or (expirations[0] if expirations else None)
        calls: list = []
        puts: list = []
        if chosen:
            try:
                chain = t.option_chain(chosen)
                calls = chain.calls.to_dict(orient="records") if chain.calls is not None else []
                puts = chain.puts.to_dict(orient="records") if chain.puts is not None else []
            except Exception:
                calls, puts = [], []
        return _clean(
            {
                "symbol": symbol.upper(),
                "expirations": expirations,
                "expiration": chosen,
                "calls": calls,
                "puts": puts,
            }
        )

    try:
        return _cached(f"opt:{symbol.upper()}:{expiration or ''}", produce)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"yfinance error: {e}")


def _pick_thumbnail(thumb: Any) -> str | None:
    """Pick a small-ish thumbnail URL. Prefer 100-400px wide; fall back to original."""
    if not isinstance(thumb, dict):
        return None
    resolutions = thumb.get("resolutions") or []
    if isinstance(resolutions, list) and resolutions:
        candidates = [r for r in resolutions if isinstance(r, dict) and r.get("url")]
        small = [r for r in candidates if isinstance(r.get("width"), (int, float)) and 100 <= r["width"] <= 400]
        if small:
            small.sort(key=lambda r: r["width"])
            return small[0]["url"]
        if candidates:
            return candidates[0]["url"]
    return thumb.get("originalUrl") or thumb.get("url")


@app.get("/api/news/{symbol}")
def news(symbol: str) -> dict:
    def produce() -> dict:
        try:
            items = _ticker(symbol).news or []
        except Exception:
            items = []
        normalized = []
        for it in items[:25]:
            content = it.get("content") if isinstance(it, dict) else None
            if isinstance(content, dict):
                normalized.append(
                    {
                        "title": content.get("title"),
                        "publisher": (content.get("provider") or {}).get("displayName")
                        if isinstance(content.get("provider"), dict)
                        else content.get("publisher"),
                        "link": (content.get("canonicalUrl") or {}).get("url")
                        if isinstance(content.get("canonicalUrl"), dict)
                        else content.get("link"),
                        "publishedAt": content.get("pubDate") or content.get("displayTime"),
                        "summary": content.get("summary"),
                        "thumbnail": _pick_thumbnail(content.get("thumbnail")),
                    }
                )
            elif isinstance(it, dict):
                thumb = it.get("thumbnail")
                thumb_url = None
                if isinstance(thumb, dict):
                    thumb_url = _pick_thumbnail(thumb)
                normalized.append(
                    {
                        "title": it.get("title"),
                        "publisher": it.get("publisher"),
                        "link": it.get("link"),
                        "publishedAt": it.get("providerPublishTime"),
                        "summary": it.get("summary"),
                        "thumbnail": thumb_url,
                    }
                )
        return _clean({"symbol": symbol.upper(), "items": normalized})

    try:
        return _cached(f"news:{symbol.upper()}", produce)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"yfinance error: {e}")


@app.get("/api/news")
def news_feed(symbols: str | None = Query(None, description="Comma-separated symbols; defaults to watchlist")) -> dict:
    """Aggregated news across a set of symbols (watchlist by default)."""
    if symbols:
        syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        syms = _dashboard_service.watchlist()
    syms = syms[:10]

    def produce() -> dict:
        merged: list[dict] = []
        seen: set[str] = set()
        for sym in syms:
            try:
                payload = news(sym)
                items = payload.get("items") or []
            except HTTPException:
                items = []
            for it in items:
                key = it.get("link") or it.get("title")
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append({**it, "symbol": sym})

        def sort_key(item: dict):
            v = item.get("publishedAt")
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str):
                try:
                    from datetime import datetime

                    return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
                except Exception:
                    return 0.0
            return 0.0

        merged.sort(key=sort_key, reverse=True)
        return _clean({"symbols": syms, "items": merged[:30]})

    key = "feed:" + ",".join(syms)
    try:
        return _cached(key, produce)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"yfinance error: {e}")


@app.get("/api/search")
def search(q: str = Query(..., min_length=1)) -> dict:
    def produce() -> dict:
        try:
            s = yf.Search(q, max_results=10)
            quotes_ = s.quotes or []
        except Exception:
            quotes_ = []
        results = []
        for item in quotes_:
            results.append(
                {
                    "symbol": item.get("symbol"),
                    "name": item.get("shortname") or item.get("longname"),
                    "exchange": item.get("exchDisp") or item.get("exchange"),
                    "type": item.get("typeDisp") or item.get("quoteType"),
                }
            )
        return _clean({"query": q, "results": results})

    try:
        return _cached(f"search:{q}", produce)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"yfinance error: {e}")


# ---- Watchlist ----


class WatchlistAdd(BaseModel):
    symbol: str


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


# ---- Holdings ----


class HoldingIn(BaseModel):
    symbol: str
    shares: float
    costBasis: float  # per-share cost
    account: str | None = None
    assetClass: str | None = None
    sector: str | None = None
    acquired: str | None = None


@app.get("/api/holdings")
def holdings_list(refresh: bool = False) -> dict:
    try:
        return _clean(_dashboard_service.holdings(refresh))
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
        return _clean(
            _dashboard_service.market_analysis(
                symbol, parsed_windows, price, refresh
            )
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


# ---- Static frontend ----
# Mount LAST so /api/* and /favicon.ico routes take precedence.
from fastapi.staticfiles import StaticFiles  # noqa: E402

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def run() -> None:
    """Entry point: `uv run quant-dashboard`. Honors HOST/PORT env vars."""
    import os

    import uvicorn

    uvicorn.run(
        "quant.dashboard.server:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8001")),
        reload=False,
    )
