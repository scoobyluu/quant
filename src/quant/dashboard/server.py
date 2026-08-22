from __future__ import annotations

import csv
import json
import math
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import yfinance as yf
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

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


DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
HOLDINGS_FILE = DATA_DIR / "holdings.json"
WATCHLIST_FILE = DATA_DIR / "watchlist.json"

# portfolio.csv lives at the repo root and follows: ticker,quantity,cost
PORTFOLIO_CSV = Path(__file__).resolve().parents[3] / "portfolio.csv"

DEFAULT_WATCHLIST = ["AAPL", "MSFT", "NVDA", "GOOGL", "TSLA"]

_cache: dict[str, tuple[float, Any]] = {}
CACHE_TTL_SECONDS = 30
NEWS_TTL_SECONDS = 60 * 5  # news changes slower than quotes; cache longer
NAME_TTL_SECONDS = 60 * 60 * 24  # company names effectively never change
STALE_TTL_SECONDS = 60 * 60
ANALYTICS_TTL_SECONDS = 60 * 15  # returns-based analytics are heavy; refresh every 15m
TRADING_DAYS = 252
BENCHMARK_SYMBOL = "SPY"


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def _save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, default=str))


def _seed_holdings_from_csv() -> list[dict]:
    """One-time seed: convert portfolio.csv (ticker,quantity,cost) into holdings.json."""
    if not PORTFOLIO_CSV.exists():
        return []
    seeded: list[dict] = []
    with PORTFOLIO_CSV.open() as f:
        for row in csv.DictReader(f):
            symbol = (row.get("ticker") or "").strip().upper()
            if not symbol:
                continue
            try:
                shares = float(row.get("quantity") or 0)
                cost = float(row.get("cost") or 0)
            except ValueError:
                continue
            seeded.append(
                {
                    "id": str(uuid.uuid4()),
                    "symbol": symbol,
                    "shares": shares,
                    "costBasis": cost,
                }
            )
    if seeded:
        _save_json(HOLDINGS_FILE, seeded)
    return seeded


if not HOLDINGS_FILE.exists():
    _seed_holdings_from_csv()


def _cached(key: str, producer, ttl: int = CACHE_TTL_SECONDS):
    """Return fresh value if cached < ttl; otherwise call producer.

    On producer failure, fall back to a stale cached value (<= STALE_TTL) so a
    transient upstream 429 doesn't blank the UI.
    """
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
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
    if not syms:
        return {"quotes": []}

    def fetch(sym: str) -> dict:
        try:
            return quote(sym)
        except HTTPException:
            return {"symbol": sym, "error": True}

    with ThreadPoolExecutor(max_workers=min(len(syms), 10)) as ex:
        results = list(ex.map(fetch, syms))
    return {"quotes": results}


def _fi_get(fi: Any, *keys: str) -> Any:
    """Read a field from a yfinance FastInfo (supports attr and dict access)."""
    for k in keys:
        try:
            if hasattr(fi, k):
                v = getattr(fi, k)
                if v is not None:
                    return v
            if hasattr(fi, "get"):
                v = fi.get(k)
                if v is not None:
                    return v
        except Exception:
            pass
    return None


def _name(symbol: str) -> str | None:
    """Long-cached company name. Falls back to None on failure."""
    def produce() -> str | None:
        info = _ticker(symbol).info or {}
        return info.get("shortName") or info.get("longName")

    try:
        return _cached(f"name:{symbol.upper()}", produce, ttl=NAME_TTL_SECONDS)
    except Exception:
        return None


@app.get("/api/quote/{symbol}")
def quote(symbol: str) -> dict:
    def produce() -> dict:
        t = _ticker(symbol)

        # Fetch fast_info and the (long-cached) name in parallel so cold requests
        # aren't gated by the name lookup.
        with ThreadPoolExecutor(max_workers=2) as ex:
            fi_fut = ex.submit(lambda: t.fast_info)
            name_fut = ex.submit(_name, symbol)
            try:
                fi = fi_fut.result()
            except Exception:
                fi = None
            name = name_fut.result()

        price = _fi_get(fi, "last_price", "lastPrice", "regular_market_price")
        prev = _fi_get(fi, "previous_close", "regular_market_previous_close")
        day_high = _fi_get(fi, "day_high", "dayHigh")
        day_low = _fi_get(fi, "day_low", "dayLow")
        volume = _fi_get(fi, "last_volume", "regular_market_volume", "volume")
        currency = _fi_get(fi, "currency")

        # fast_info doesn't expose marketState; if we need to backfill any missing
        # volatile field, fall back to the slower .info path once.
        if price is None:
            info = t.info or {}
            price = (
                info.get("regularMarketPrice")
                or info.get("currentPrice")
                or info.get("previousClose")
            )
            prev = prev or info.get("regularMarketPreviousClose") or info.get("previousClose")
            day_high = day_high or info.get("dayHigh")
            day_low = day_low or info.get("dayLow")
            volume = volume or info.get("volume") or info.get("regularMarketVolume")
            currency = currency or info.get("currency")

        change = None
        change_pct = None
        if price is not None and prev:
            change = price - prev
            change_pct = (change / prev) * 100 if prev else None
        return _clean(
            {
                "symbol": symbol.upper(),
                "name": name,
                "price": price,
                "previousClose": prev,
                "change": change,
                "changePercent": change_pct,
                "currency": currency,
                "marketState": None,
                "dayHigh": day_high,
                "dayLow": day_low,
                "volume": volume,
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
        return _cached(f"news:{symbol.upper()}", produce, ttl=NEWS_TTL_SECONDS)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"yfinance error: {e}")


@app.get("/api/news")
def news_feed(symbols: str | None = Query(None, description="Comma-separated symbols; defaults to watchlist")) -> dict:
    """Aggregated news across a set of symbols (watchlist by default)."""
    if symbols:
        syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        syms = _load_json(WATCHLIST_FILE, DEFAULT_WATCHLIST)
    syms = syms[:10]

    def produce() -> dict:
        def fetch(sym: str) -> tuple[str, list[dict]]:
            try:
                return sym, (news(sym).get("items") or [])
            except HTTPException:
                return sym, []

        merged: list[dict] = []
        seen: set[str] = set()
        if syms:
            with ThreadPoolExecutor(max_workers=min(len(syms), 10)) as ex:
                # ex.map preserves input order, giving stable de-dup priority.
                for sym, items in ex.map(fetch, syms):
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
        return _cached(key, produce, ttl=NEWS_TTL_SECONDS)
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


# ---- Analytics ----


def _daily_closes(symbol: str, period: str = "1y") -> list[tuple[str, float]]:
    """Return [(iso_date, adjusted_close)] daily bars for `symbol`.

    Uses auto-adjusted closes so returns reflect splits/dividends.
    """
    def produce() -> list[tuple[str, float]]:
        df = _ticker(symbol).history(period=period, interval="1d", auto_adjust=True)
        if df.empty:
            return []
        df = df.reset_index()
        date_col = "Datetime" if "Datetime" in df.columns else "Date"
        out: list[tuple[str, float]] = []
        for _, row in df.iterrows():
            close = row.get("Close")
            if close is None:
                continue
            try:
                c = float(close)
            except (TypeError, ValueError):
                continue
            if math.isnan(c) or math.isinf(c):
                continue
            d = row[date_col]
            iso = d.date().isoformat() if hasattr(d, "date") else str(d)[:10]
            out.append((iso, c))
        return out

    return _cached(f"closes:{symbol.upper()}:{period}", produce, ttl=ANALYTICS_TTL_SECONDS)


def _stats_from_returns(returns: list[float]) -> dict:
    """Sharpe, Sortino, annualized vol, max drawdown, cumulative return.

    Sharpe/Sortino here are excess-over-zero — no risk-free adjustment. That's
    a common simplification for a dashboard view.
    """
    if not returns:
        return {
            "sharpe": None,
            "sortino": None,
            "volatility": None,
            "maxDrawdown": None,
            "cumulativeReturn": None,
        }
    n = len(returns)
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / n if n > 1 else 0.0
    std = math.sqrt(var)
    downside = [r for r in returns if r < 0]
    dvar = sum(r * r for r in downside) / n if downside else 0.0
    dstd = math.sqrt(dvar)
    ann_ret = mean * TRADING_DAYS
    ann_vol = std * math.sqrt(TRADING_DAYS)
    ann_dvol = dstd * math.sqrt(TRADING_DAYS)

    cum = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns:
        cum *= 1.0 + r
        if cum > peak:
            peak = cum
        dd = cum / peak - 1.0
        if dd < max_dd:
            max_dd = dd

    return {
        "sharpe": (ann_ret / ann_vol) if ann_vol else None,
        "sortino": (ann_ret / ann_dvol) if ann_dvol else None,
        "volatility": ann_vol,
        "maxDrawdown": max_dd,
        "cumulativeReturn": cum - 1.0,
    }


def _beta_alpha(
    port_by_date: dict[str, float], bench_by_date: dict[str, float]
) -> tuple[float | None, float | None]:
    """Beta = cov(p,m)/var(m); alpha = annualized daily intercept."""
    dates = sorted(set(port_by_date) & set(bench_by_date))
    if len(dates) < 2:
        return None, None
    p = [port_by_date[d] for d in dates]
    m = [bench_by_date[d] for d in dates]
    n = len(dates)
    pm = sum(p) / n
    mm = sum(m) / n
    cov = sum((p[i] - pm) * (m[i] - mm) for i in range(n)) / n
    var_m = sum((m[i] - mm) ** 2 for i in range(n)) / n
    if not var_m:
        return None, None
    beta = cov / var_m
    alpha_daily = pm - beta * mm
    return beta, alpha_daily * TRADING_DAYS


def _returns_by_date(closes: list[tuple[str, float]]) -> dict[str, float]:
    """Simple daily returns keyed by trailing date."""
    out: dict[str, float] = {}
    for i in range(1, len(closes)):
        prev = closes[i - 1][1]
        if prev:
            out[closes[i][0]] = closes[i][1] / prev - 1.0
    return out


def _portfolio_value_series(
    holdings: list[dict], histories: dict[str, list[tuple[str, float]]]
) -> dict[str, float]:
    """Daily portfolio market value assuming current shares held throughout."""
    if not holdings:
        return {}
    # Union of dates, then forward-fill each symbol's close so a missing day
    # (rare — halts, holidays) doesn't drop the whole portfolio row.
    all_dates: set[str] = set()
    for series in histories.values():
        for date, _ in series:
            all_dates.add(date)
    dates = sorted(all_dates)
    if not dates:
        return {}

    filled: dict[str, dict[str, float]] = {}
    for sym, series in histories.items():
        d2c = dict(series)
        last: float | None = None
        col: dict[str, float] = {}
        for date in dates:
            if date in d2c:
                last = d2c[date]
            if last is not None:
                col[date] = last
        filled[sym] = col

    out: dict[str, float] = {}
    for date in dates:
        v = 0.0
        ok = True
        for h in holdings:
            sym = h["symbol"].upper()
            close = filled.get(sym, {}).get(date)
            if close is None:
                ok = False
                break
            v += float(h["shares"]) * close
        if ok:
            out[date] = v
    return out


def _range_return(closes: list[tuple[str, float]], start_iso: str | None) -> float | None:
    """Percent change from the first close on/after start_iso to the latest close."""
    if not closes:
        return None
    if start_iso is None:
        first = closes[0][1]
    else:
        first = None
        for date, c in closes:
            if date >= start_iso:
                first = c
                break
    if not first:
        return None
    last = closes[-1][1]
    return last / first - 1.0


def _month_ago_iso(latest_iso: str) -> str:
    """Rough one-month lookback: 30 days back from the latest bar."""
    from datetime import date, timedelta

    try:
        y, m, d = (int(x) for x in latest_iso.split("-"))
        return (date(y, m, d) - timedelta(days=30)).isoformat()
    except Exception:
        return latest_iso


def _ytd_start_iso(latest_iso: str) -> str:
    """Jan 1 of the year of the latest bar (returned as ISO date)."""
    return f"{latest_iso[:4]}-01-01"


# ---- Watchlist ----


class WatchlistAdd(BaseModel):
    symbol: str


@app.get("/api/watchlist")
def watchlist_get() -> dict:
    return {"symbols": _load_json(WATCHLIST_FILE, DEFAULT_WATCHLIST)}


@app.get("/api/watchlist/quotes")
def watchlist_quotes() -> dict:
    """Latest quotes for every symbol in the watchlist, fetched in parallel."""
    syms: list[str] = _load_json(WATCHLIST_FILE, DEFAULT_WATCHLIST)
    if not syms:
        return {"symbols": [], "quotes": []}

    def fetch(sym: str) -> dict:
        try:
            return quote(sym)
        except HTTPException:
            return {"symbol": sym, "error": True}

    with ThreadPoolExecutor(max_workers=min(len(syms), 10)) as ex:
        results = list(ex.map(fetch, syms))
    return {"symbols": syms, "quotes": results}


@app.get("/api/watchlist/analytics")
def watchlist_analytics() -> dict:
    """Per-symbol 1M return, YTD return, 1Y Sharpe and 1Y annualized vol."""
    syms: list[str] = _load_json(WATCHLIST_FILE, DEFAULT_WATCHLIST)
    if not syms:
        return {"symbols": [], "metrics": []}

    def compute(sym: str) -> dict:
        try:
            closes = _daily_closes(sym, "1y")
        except Exception:
            return {"symbol": sym, "error": True}
        if len(closes) < 2:
            return {"symbol": sym}
        latest = closes[-1][0]
        one_month = _range_return(closes, _month_ago_iso(latest))
        ytd = _range_return(closes, _ytd_start_iso(latest))
        returns = list(_returns_by_date(closes).values())
        stats = _stats_from_returns(returns)
        return {
            "symbol": sym,
            "oneMonth": one_month,
            "ytd": ytd,
            "sharpe": stats["sharpe"],
            "volatility": stats["volatility"],
        }

    with ThreadPoolExecutor(max_workers=min(len(syms), 10)) as ex:
        metrics = list(ex.map(compute, syms))
    return _clean({"symbols": syms, "metrics": metrics})


@app.post("/api/watchlist")
def watchlist_add(body: WatchlistAdd) -> dict:
    symbols: list[str] = _load_json(WATCHLIST_FILE, DEFAULT_WATCHLIST)
    sym = body.symbol.upper().strip()
    if sym and sym not in symbols:
        symbols.append(sym)
        _save_json(WATCHLIST_FILE, symbols)
    return {"symbols": symbols}


@app.delete("/api/watchlist/{symbol}")
def watchlist_remove(symbol: str) -> dict:
    symbols: list[str] = _load_json(WATCHLIST_FILE, DEFAULT_WATCHLIST)
    sym = symbol.upper()
    symbols = [s for s in symbols if s != sym]
    _save_json(WATCHLIST_FILE, symbols)
    return {"symbols": symbols}


# ---- Holdings ----


class HoldingIn(BaseModel):
    symbol: str
    shares: float
    costBasis: float  # per-share cost


class Holding(HoldingIn):
    id: str


@app.get("/api/holdings")
def holdings_list() -> dict:
    raw: list[dict] = _load_json(HOLDINGS_FILE, [])
    enriched = []
    total_cost = 0.0
    total_value = 0.0

    # De-dup symbols so we only hit yfinance once per ticker even if the user
    # holds it across multiple lots.
    unique_syms = list({h["symbol"].upper() for h in raw})

    def fetch_price(sym: str) -> tuple[str, float | None]:
        try:
            return sym, quote(sym).get("price")
        except HTTPException:
            return sym, None

    prices: dict[str, float | None] = {}
    if unique_syms:
        with ThreadPoolExecutor(max_workers=min(len(unique_syms), 10)) as ex:
            for sym, price in ex.map(fetch_price, unique_syms):
                prices[sym] = price

    for h in raw:
        sym = h["symbol"].upper()
        price = prices.get(sym)
        shares = float(h["shares"])
        cost = float(h["costBasis"])
        market_value = price * shares if price is not None else None
        cost_value = cost * shares
        gain = (market_value - cost_value) if market_value is not None else None
        gain_pct = (gain / cost_value * 100) if (gain is not None and cost_value) else None
        if market_value is not None:
            total_value += market_value
        total_cost += cost_value
        enriched.append(
            {
                "id": h["id"],
                "symbol": sym,
                "shares": shares,
                "costBasis": cost,
                "price": price,
                "marketValue": market_value,
                "costValue": cost_value,
                "gain": gain,
                "gainPercent": gain_pct,
            }
        )
    total_gain = total_value - total_cost if total_value else None
    total_gain_pct = (total_gain / total_cost * 100) if (total_gain is not None and total_cost) else None
    return _clean(
        {
            "holdings": enriched,
            "totals": {
                "cost": total_cost,
                "value": total_value,
                "gain": total_gain,
                "gainPercent": total_gain_pct,
            },
        }
    )


@app.get("/api/portfolio/analytics")
def portfolio_analytics(period: str = Query("1y", description="1mo,3mo,6mo,1y,2y,5y,10y,ytd,max")) -> dict:
    """Risk-adjusted return metrics + cumulative-return curve vs SPY.

    Assumes the user's current shares were held throughout `period` — a
    standard backtest simplification, not an accurate historical P&L.
    """
    raw: list[dict] = _load_json(HOLDINGS_FILE, [])
    if not raw:
        return {
            "period": period,
            "benchmark": BENCHMARK_SYMBOL,
            "kpis": {},
            "curve": [],
        }

    unique_syms = list({h["symbol"].upper() for h in raw})
    fetch_syms = unique_syms + [BENCHMARK_SYMBOL]

    def fetch(sym: str) -> tuple[str, list[tuple[str, float]]]:
        try:
            return sym, _daily_closes(sym, period)
        except Exception:
            return sym, []

    histories: dict[str, list[tuple[str, float]]] = {}
    with ThreadPoolExecutor(max_workers=min(len(fetch_syms), 10)) as ex:
        for sym, series in ex.map(fetch, fetch_syms):
            histories[sym] = series

    bench_series = histories.pop(BENCHMARK_SYMBOL, [])
    port_value = _portfolio_value_series(raw, histories)
    if not port_value or not bench_series:
        return {
            "period": period,
            "benchmark": BENCHMARK_SYMBOL,
            "kpis": {},
            "curve": [],
        }

    dates = sorted(port_value.keys())
    values = [port_value[d] for d in dates]
    port_returns_seq = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values)) if values[i - 1]]
    port_returns_by_date = {dates[i]: values[i] / values[i - 1] - 1.0 for i in range(1, len(values)) if values[i - 1]}

    bench_returns_by_date = _returns_by_date(bench_series)
    bench_by_date_close = dict(bench_series)

    port_stats = _stats_from_returns(port_returns_seq)
    beta, alpha = _beta_alpha(port_returns_by_date, bench_returns_by_date)

    # Cumulative return curve, aligned on the intersection of portfolio and
    # benchmark dates so the two series start at 0 on the same day.
    common = sorted(set(dates) & set(bench_by_date_close.keys()))
    curve: list[dict] = []
    if len(common) >= 2:
        p0 = port_value[common[0]]
        b0 = bench_by_date_close[common[0]]
        if p0 and b0:
            for d in common:
                curve.append(
                    {
                        "t": d,
                        "portfolio": port_value[d] / p0 - 1.0,
                        "benchmark": bench_by_date_close[d] / b0 - 1.0,
                    }
                )

    bench_return = (bench_series[-1][1] / bench_series[0][1] - 1.0) if bench_series and bench_series[0][1] else None

    kpis = {
        **port_stats,
        "beta": beta,
        "alpha": alpha,
        "benchmarkReturn": bench_return,
    }

    return _clean(
        {
            "period": period,
            "benchmark": BENCHMARK_SYMBOL,
            "kpis": kpis,
            "curve": curve,
        }
    )


@app.post("/api/holdings")
def holdings_add(body: HoldingIn) -> dict:
    raw: list[dict] = _load_json(HOLDINGS_FILE, [])
    holding = {
        "id": str(uuid.uuid4()),
        "symbol": body.symbol.upper().strip(),
        "shares": body.shares,
        "costBasis": body.costBasis,
    }
    raw.append(holding)
    _save_json(HOLDINGS_FILE, raw)
    return holding


@app.delete("/api/holdings/{holding_id}")
def holdings_remove(holding_id: str) -> dict:
    raw: list[dict] = _load_json(HOLDINGS_FILE, [])
    new_raw = [h for h in raw if h["id"] != holding_id]
    _save_json(HOLDINGS_FILE, new_raw)
    return {"removed": len(raw) - len(new_raw)}


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
