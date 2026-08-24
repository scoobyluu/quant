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
            "trailingPE", "forwardPE", "pegRatio", "priceToBook", "priceToSalesTrailing12Months",
            "trailingEps", "forwardEps",
            "dividendRate", "dividendYield", "payoutRatio", "fiveYearAvgDividendYield",
            "beta", "52WeekChange", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
            "fiftyDayAverage", "twoHundredDayAverage",
            "profitMargins", "operatingMargins", "grossMargins", "ebitdaMargins",
            "returnOnAssets", "returnOnEquity",
            "totalRevenue", "revenuePerShare", "revenueGrowth", "earningsGrowth",
            "grossProfits", "ebitda", "netIncomeToCommon",
            "totalCash", "totalDebt", "debtToEquity", "currentRatio", "quickRatio",
            "freeCashflow", "operatingCashflow",
            "sharesOutstanding", "floatShares", "heldPercentInsiders", "heldPercentInstitutions",
            "averageVolume", "averageVolume10days",
            "currentPrice", "regularMarketPrice",
            "targetMeanPrice", "targetHighPrice", "targetLowPrice", "targetMedianPrice",
            "recommendationKey", "recommendationMean", "numberOfAnalystOpinions",
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


def _correlation_matrix(
    returns_by_symbol: dict[str, dict[str, float]],
) -> tuple[list[str], list[list[float | None]]]:
    """Pearson correlation on each pair's intersecting dates."""
    syms = sorted(returns_by_symbol.keys())
    n = len(syms)
    matrix: list[list[float | None]] = [[None] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                matrix[i][j] = 1.0
                continue
            if j < i:
                matrix[i][j] = matrix[j][i]
                continue
            a = returns_by_symbol[syms[i]]
            b = returns_by_symbol[syms[j]]
            common = sorted(set(a) & set(b))
            if len(common) < 2:
                continue
            x = [a[d] for d in common]
            y = [b[d] for d in common]
            k = len(x)
            mx = sum(x) / k
            my = sum(y) / k
            sx2 = sum((xi - mx) ** 2 for xi in x)
            sy2 = sum((yi - my) ** 2 for yi in y)
            if sx2 == 0 or sy2 == 0:
                continue
            cov = sum((x[t] - mx) * (y[t] - my) for t in range(k))
            matrix[i][j] = cov / math.sqrt(sx2 * sy2)
    return syms, matrix


def _contributions(
    holdings: list[dict],
    histories: dict[str, list[tuple[str, float]]],
    returns_by_symbol: dict[str, dict[str, float]],
    port_returns_by_date: dict[str, float],
) -> list[dict]:
    """Per-symbol weight, period return, return contribution, risk contribution.

    Risk contribution uses w_i · cov(r_i, r_p) / var(r_p). By construction the
    contributions sum to 1 (100%), matching the standard variance decomposition.
    """
    # Sum shares across lots so a multi-lot ticker appears once.
    shares_by_sym: dict[str, float] = {}
    for hd in holdings:
        sym = hd["symbol"].upper()
        shares_by_sym[sym] = shares_by_sym.get(sym, 0.0) + float(hd["shares"])

    current_value: dict[str, float] = {}
    total_value = 0.0
    for sym, shares in shares_by_sym.items():
        series = histories.get(sym) or []
        if not series:
            continue
        v = shares * series[-1][1]
        current_value[sym] = v
        total_value += v
    if total_value == 0:
        return []

    port_dates = list(port_returns_by_date.keys())
    if len(port_dates) >= 2:
        p_vals = list(port_returns_by_date.values())
        p_mean = sum(p_vals) / len(p_vals)
        p_var = sum((r - p_mean) ** 2 for r in p_vals) / len(p_vals)
    else:
        p_mean = 0.0
        p_var = 0.0

    out: list[dict] = []
    for sym, shares in shares_by_sym.items():
        series = histories.get(sym) or []
        weight = current_value.get(sym, 0.0) / total_value if total_value else None
        symbol_return: float | None = None
        if series and series[0][1]:
            symbol_return = series[-1][1] / series[0][1] - 1.0
        return_contribution = (
            (weight or 0.0) * symbol_return if symbol_return is not None else None
        )
        risk_contribution = None
        sym_returns = returns_by_symbol.get(sym) or {}
        if p_var > 0 and sym_returns:
            common = sorted(set(sym_returns) & set(port_returns_by_date))
            if len(common) >= 2:
                x = [sym_returns[d] for d in common]
                p = [port_returns_by_date[d] for d in common]
                mx = sum(x) / len(x)
                mp = sum(p) / len(p)
                cov = sum((x[k] - mx) * (p[k] - mp) for k in range(len(x))) / len(x)
                risk_contribution = (weight or 0.0) * cov / p_var
        out.append(
            {
                "symbol": sym,
                "weight": weight,
                "return": symbol_return,
                "returnContribution": return_contribution,
                "riskContribution": risk_contribution,
            }
        )
    out.sort(key=lambda r: r.get("weight") or 0.0, reverse=True)
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


@app.get("/api/analytics/{symbol}")
def symbol_analytics(
    symbol: str,
    period: str = Query("1y", description="1mo,3mo,6mo,1y,2y,5y,10y,ytd,max"),
) -> dict:
    """Risk-adjusted return metrics for a single symbol, plus beta/alpha vs SPY."""
    def fetch(sym: str) -> list[tuple[str, float]]:
        try:
            return _daily_closes(sym, period)
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=2) as ex:
        closes_fut = ex.submit(fetch, symbol)
        bench_fut = ex.submit(fetch, BENCHMARK_SYMBOL)
        closes = closes_fut.result()
        bench = bench_fut.result()

    if len(closes) < 2:
        return _clean(
            {
                "symbol": symbol.upper(),
                "period": period,
                "benchmark": BENCHMARK_SYMBOL,
                "metrics": {},
            }
        )

    returns_by_date = _returns_by_date(closes)
    stats = _stats_from_returns(list(returns_by_date.values()))
    latest = closes[-1][0]
    one_month = _range_return(closes, _month_ago_iso(latest))
    ytd = _range_return(closes, _ytd_start_iso(latest))

    beta: float | None = None
    alpha: float | None = None
    if bench:
        beta, alpha = _beta_alpha(returns_by_date, _returns_by_date(bench))

    return _clean(
        {
            "symbol": symbol.upper(),
            "period": period,
            "benchmark": BENCHMARK_SYMBOL,
            "metrics": {
                **stats,
                "oneMonth": one_month,
                "ytd": ytd,
                "beta": beta,
                "alpha": alpha,
            },
        }
    )


# ---- Screener ----
#
# The screener ranks a set of symbols across four factor buckets:
#   Value      — cheap on forward P/E / PEG / P/B / P/S / FCF yield
#   Quality    — high ROE, margins, revenue growth, low leverage
#   Return     — good risk-adjusted return (Sharpe) and market-adjusted alpha
#   Momentum   — recent 1M / YTD / 1Y returns
#
# Value and Quality are scored by SECTOR-RELATIVE PERCENTILE — a P/E of 15 is
# cheap for a bank but expensive for software, so scoring each ticker against
# an absolute PE curve produces nonsense. We fetch a hardcoded set of sector
# representatives per request (all cached long enough that repeated hits pay
# nothing) and compute per-sector percentile samples on the fly.
#
# Return and Momentum stay on absolute scales — Sharpe > 1 is a market truth,
# not a sector one.


# Sector representatives: enough coverage per sector (2-5 names) that
# percentiles are meaningful, small enough that cold fetches finish in seconds.
SECTOR_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "ORCL",   # Tech + Comm Services
    "JPM", "BAC", "GS", "MS", "WFC",                   # Financials
    "JNJ", "UNH", "PFE", "MRK", "ABBV",                # Healthcare
    "AMZN", "HD", "MCD", "NKE", "SBUX",                # Consumer Discretionary
    "WMT", "PG", "KO", "PEP", "COST",                  # Consumer Staples
    "BA", "CAT", "GE", "HON", "UNP",                   # Industrials
    "XOM", "CVX", "COP", "SLB",                        # Energy
    "NEE", "SO", "DUK",                                # Utilities
    "LIN", "FCX", "ECL",                               # Materials
    "PLD", "AMT", "O",                                 # Real Estate
    "DIS", "NFLX", "T", "VZ",                          # Communication Services
]


def _clamp01(x: float) -> float:
    return max(0.0, min(100.0, x))


def _fcf_yield(info: dict) -> float | None:
    """FCF / market cap. Retail-friendly value signal, harder to fake than PE."""
    fcf = info.get("freeCashflow")
    mc = info.get("marketCap")
    if isinstance(fcf, (int, float)) and isinstance(mc, (int, float)) and mc > 0:
        return fcf / mc
    return None


# Factor spec: (key, extract, higher_better, absolute_score_fn)
# The absolute fallback kicks in when we lack ≥3 sector peers to compute a
# percentile against — mainly for unusual sectors and ETFs.
VALUE_FACTORS: list[tuple[str, Any, bool, Any]] = [
    ("forwardPE",     lambda i: i.get("forwardPE") or i.get("trailingPE"),
        False, lambda v: _clamp01(100.0 - (v - 10) * 5)),
    ("pegRatio",      lambda i: i.get("pegRatio"),
        False, lambda v: _clamp01(100.0 - (v - 0.5) * (100 / 1.5))),
    ("priceToBook",   lambda i: i.get("priceToBook"),
        False, lambda v: _clamp01(100.0 - (v - 1) * 20)),
    ("priceToSales",  lambda i: i.get("priceToSalesTrailing12Months"),
        False, lambda v: _clamp01(100.0 - (v - 1) * (100 / 9))),
    ("fcfYield",      _fcf_yield,
        True,  lambda v: _clamp01(v * 100 * 15)),  # 6.67% yield → 100
]

QUALITY_FACTORS: list[tuple[str, Any, bool, Any]] = [
    ("roe",           lambda i: i.get("returnOnEquity"),
        True,  lambda v: _clamp01(v * 100 * 3)),
    ("profitMargin",  lambda i: i.get("profitMargins"),
        True,  lambda v: _clamp01(v * 100 * 5)),
    ("revenueGrowth", lambda i: i.get("revenueGrowth"),
        True,  lambda v: _clamp01(v * 100 * 3)),
    ("debtToEquity",  lambda i: i.get("debtToEquity"),
        False, lambda v: _clamp01(100.0 - v * 0.5)),  # yfinance reports % (100 = 1.0x)
]


def _percentile_rank(value: float, samples: list[float]) -> float:
    """Fraction of samples <= value, in [0, 1]. Empty samples → 0.5 (neutral)."""
    if not samples:
        return 0.5
    return sum(1 for s in samples if s <= value) / len(samples)


def _build_sector_stats(infos: list[dict]) -> dict[str, dict[str, list[float]]]:
    """Group each factor's raw values by sector for percentile lookups."""
    stats: dict[str, dict[str, list[float]]] = {}
    for info_row in infos:
        sector = info_row.get("sector")
        if not sector:
            continue
        bucket = stats.setdefault(sector, {})
        for key, extract, _higher, _absolute in VALUE_FACTORS + QUALITY_FACTORS:
            v = extract(info_row)
            if isinstance(v, (int, float)) and not (math.isnan(v) or math.isinf(v)):
                bucket.setdefault(key, []).append(float(v))
    return stats


def _score_bucket(
    info_row: dict,
    factors_spec: list[tuple[str, Any, bool, Any]],
    sector_stats: dict[str, dict[str, list[float]]],
) -> tuple[float | None, dict[str, str]]:
    """Score a factor bucket, preferring sector percentiles over absolute scales.

    Returns (score, basis_by_factor) where basis is "sector" or "absolute" per
    factor — useful for debugging and for the UI to indicate coverage.
    """
    parts: list[float] = []
    basis: dict[str, str] = {}
    sector = info_row.get("sector")
    for key, extract, higher, absolute in factors_spec:
        v = extract(info_row)
        if not isinstance(v, (int, float)) or math.isnan(v) or math.isinf(v):
            continue
        samples = (sector_stats.get(sector) or {}).get(key) if sector else None
        # Need ≥3 samples for the percentile to mean anything (and drop self so
        # the target isn't scoring against itself).
        peers = [s for s in samples if s != v] if samples else []
        if peers and len(peers) >= 3:
            rank = _percentile_rank(v, peers)
            parts.append(rank * 100 if higher else (1 - rank) * 100)
            basis[key] = "sector"
        else:
            parts.append(absolute(v))
            basis[key] = "absolute"
    return (sum(parts) / len(parts) if parts else None), basis


def _score_return(sharpe: float | None, alpha: float | None) -> float | None:
    """Sharpe > 1 and positive alpha score higher. Absolute — not sector-relative."""
    parts: list[float] = []
    if isinstance(sharpe, (int, float)):
        parts.append(_clamp01((sharpe + 1) * 100 / 3))       # -1 → 0, 2 → 100
    if isinstance(alpha, (int, float)):
        parts.append(_clamp01((alpha + 0.1) * 100 / 0.3))    # -10% → 0, +20% → 100
    return sum(parts) / len(parts) if parts else None


def _score_momentum(one_month: float | None, ytd: float | None, cum: float | None) -> float | None:
    """Positive recent + longer-term returns score higher. Absolute."""
    parts: list[float] = []
    for v in (one_month, ytd, cum):
        if isinstance(v, (int, float)):
            parts.append(_clamp01((v + 0.2) * 100 / 0.6))    # -20% → 0, +40% → 100
    return sum(parts) / len(parts) if parts else None


def _signals(factors: dict) -> list[str]:
    """Boolean flags — the multi-factor case for the stock at a glance."""
    out: list[str] = []
    fpe = factors.get("forwardPE")
    peg = factors.get("pegRatio")
    pb = factors.get("priceToBook")
    if (
        isinstance(fpe, (int, float)) and 0 < fpe < 20
        and (
            (isinstance(peg, (int, float)) and 0 < peg < 1.5)
            or (isinstance(pb, (int, float)) and 0 < pb < 3)
        )
    ):
        out.append("value")
    if isinstance(fpe, (int, float)) and 0 < fpe < 15:
        out.append("cheap")
    fcfy = factors.get("fcfYield")
    if isinstance(fcfy, (int, float)) and fcfy > 0.05:
        out.append("fcfy+")
    roe = factors.get("roe")
    pm = factors.get("profitMargin")
    if isinstance(roe, (int, float)) and roe > 0.15 and isinstance(pm, (int, float)) and pm > 0.10:
        out.append("quality")
    rg = factors.get("revenueGrowth")
    if isinstance(rg, (int, float)) and rg > 0.10:
        out.append("growth")
    om = factors.get("oneMonth")
    ytd = factors.get("ytd")
    if isinstance(om, (int, float)) and om > 0 and isinstance(ytd, (int, float)) and ytd > 0:
        out.append("momentum")
    sharpe = factors.get("sharpe")
    if isinstance(sharpe, (int, float)) and sharpe > 1.0:
        out.append("sharpe+")
    alpha = factors.get("alpha")
    if isinstance(alpha, (int, float)) and alpha > 0.02:
        out.append("alpha+")
    upside = factors.get("targetUpside")
    if isinstance(upside, (int, float)) and upside > 0.15:
        out.append("upside")
    return out


@app.get("/api/screener")
def screener(
    symbols: str | None = Query(
        None,
        description="Comma-separated symbols; defaults to watchlist + holdings",
    ),
) -> dict:
    """Multi-factor screener: composite score + signal chips per symbol.

    Combines fundamentals from `.info` with 1Y risk-adjusted return metrics
    computed against SPY. Value and Quality are scored against SECTOR
    PERCENTILES (using a hardcoded universe of sector representatives) so a
    bank's PE isn't compared to software's PE. Return and Momentum stay on
    absolute scales. Ranks by composite score (mean of available factor
    scores).
    """
    if symbols:
        syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        wl: list[str] = _load_json(WATCHLIST_FILE, DEFAULT_WATCHLIST)
        holdings_syms = list({h["symbol"].upper() for h in _load_json(HOLDINGS_FILE, [])})
        syms = sorted(set(wl) | set(holdings_syms))
    syms = syms[:30]
    if not syms:
        return {"rows": [], "benchmark": BENCHMARK_SYMBOL, "period": "1y"}

    # Fetch info for the user's tickers + hardcoded sector reps in parallel.
    # info() is cached, so warm requests skip everything but the compute stage.
    universe = sorted(set(syms) | set(SECTOR_UNIVERSE))

    def fetch_info(sym: str) -> dict:
        try:
            return info(sym)
        except HTTPException:
            return {}

    with ThreadPoolExecutor(max_workers=min(len(universe), 12)) as ex:
        info_by_sym = dict(zip(universe, ex.map(fetch_info, universe)))

    sector_stats = _build_sector_stats(list(info_by_sym.values()))

    # Shared benchmark for alpha/beta — one fetch spans every row.
    try:
        bench_closes = _daily_closes(BENCHMARK_SYMBOL, "1y")
    except Exception:
        bench_closes = []
    bench_returns_by_date = _returns_by_date(bench_closes) if bench_closes else {}

    def compute(sym: str) -> dict:
        info_row = info_by_sym.get(sym) or {}
        try:
            closes = _daily_closes(sym, "1y")
        except Exception:
            closes = []

        price = (
            info_row.get("currentPrice")
            or info_row.get("regularMarketPrice")
            or (closes[-1][1] if closes else None)
        )
        target = info_row.get("targetMeanPrice")
        target_upside = (
            (target - price) / price
            if isinstance(target, (int, float)) and isinstance(price, (int, float)) and price
            else None
        )

        stats: dict = {
            "sharpe": None,
            "sortino": None,
            "volatility": None,
            "maxDrawdown": None,
            "cumulativeReturn": None,
        }
        one_month = None
        ytd = None
        beta = None
        alpha = None
        if len(closes) >= 2:
            sym_returns_by_date = _returns_by_date(closes)
            stats = _stats_from_returns(list(sym_returns_by_date.values()))
            latest = closes[-1][0]
            one_month = _range_return(closes, _month_ago_iso(latest))
            ytd = _range_return(closes, _ytd_start_iso(latest))
            if bench_returns_by_date:
                beta, alpha = _beta_alpha(sym_returns_by_date, bench_returns_by_date)

        value_score, value_basis = _score_bucket(info_row, VALUE_FACTORS, sector_stats)
        quality_score, quality_basis = _score_bucket(info_row, QUALITY_FACTORS, sector_stats)

        factors = {
            # Value
            "forwardPE": info_row.get("forwardPE"),
            "trailingPE": info_row.get("trailingPE"),
            "pegRatio": info_row.get("pegRatio"),
            "priceToBook": info_row.get("priceToBook"),
            "priceToSales": info_row.get("priceToSalesTrailing12Months"),
            "fcfYield": _fcf_yield(info_row),
            "dividendYield": info_row.get("dividendYield"),
            # Quality
            "roe": info_row.get("returnOnEquity"),
            "profitMargin": info_row.get("profitMargins"),
            "revenueGrowth": info_row.get("revenueGrowth"),
            "debtToEquity": info_row.get("debtToEquity"),
            # Return
            "sharpe": stats.get("sharpe"),
            "sortino": stats.get("sortino"),
            "volatility": stats.get("volatility"),
            "maxDrawdown": stats.get("maxDrawdown"),
            "cumulativeReturn": stats.get("cumulativeReturn"),
            "alpha": alpha,
            "beta": beta,
            # Momentum
            "oneMonth": one_month,
            "ytd": ytd,
            # Analyst
            "targetMean": target,
            "targetUpside": target_upside,
            "recommendationMean": info_row.get("recommendationMean"),
            "numberOfAnalystOpinions": info_row.get("numberOfAnalystOpinions"),
        }

        scores = {
            "value": value_score,
            "quality": quality_score,
            "return": _score_return(stats.get("sharpe"), alpha),
            "momentum": _score_momentum(one_month, ytd, stats.get("cumulativeReturn")),
        }
        available = [v for v in scores.values() if isinstance(v, (int, float))]
        composite = sum(available) / len(available) if available else None

        return {
            "symbol": sym,
            "name": info_row.get("shortName") or info_row.get("longName"),
            "sector": info_row.get("sector"),
            "price": price,
            "compositeScore": composite,
            "scores": scores,
            "scoring": {"value": value_basis, "quality": quality_basis},
            "factors": factors,
            "signals": _signals(factors),
        }

    with ThreadPoolExecutor(max_workers=min(len(syms), 10)) as ex:
        rows = list(ex.map(compute, syms))
    rows.sort(key=lambda r: r.get("compositeScore") or 0.0, reverse=True)

    # Report peer-count coverage per sector so the UI (and curl users) know how
    # much percentile scoring actually kicked in vs. absolute fallback.
    peers_by_sector = {sec: {k: len(v) for k, v in bucket.items()} for sec, bucket in sector_stats.items()}

    return _clean(
        {
            "benchmark": BENCHMARK_SYMBOL,
            "period": "1y",
            "sectorPeerCounts": peers_by_sector,
            "rows": rows,
        }
    )


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

    # Correlation matrix includes the benchmark for a visual anchor row/col.
    returns_by_symbol: dict[str, dict[str, float]] = {
        sym: _returns_by_date(series) for sym, series in histories.items() if series
    }
    corr_input = dict(returns_by_symbol)
    if bench_returns_by_date:
        corr_input[BENCHMARK_SYMBOL] = bench_returns_by_date
    corr_syms, corr_matrix = _correlation_matrix(corr_input)

    contributions = _contributions(
        raw, histories, returns_by_symbol, port_returns_by_date
    )

    return _clean(
        {
            "period": period,
            "benchmark": BENCHMARK_SYMBOL,
            "kpis": kpis,
            "curve": curve,
            "correlation": {"symbols": corr_syms, "matrix": corr_matrix},
            "contributions": contributions,
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
