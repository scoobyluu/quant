from __future__ import annotations

import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import yfinance as yf
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

ANALYTICS_TTL_SECONDS = 60 * 15
TRADING_DAYS = 252
BENCHMARK_SYMBOL = "SPY"


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


def _ticker(symbol: str) -> yf.Ticker:
    return _research_service._ticker(symbol.upper())


def _holdings_raw() -> list[dict]:
    """Positions from the shared repository, shaped for analytics helpers."""
    return [
        {
            "id": row["id"],
            "symbol": row["symbol"].upper(),
            "shares": float(row["quantity"]),
            "costBasis": float(row["average_cost"]),
        }
        for row in _dashboard_service.repository.list_positions()
    ]


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/quotes")
def quotes(symbols: str = Query(..., description="Comma-separated symbols")) -> dict:
    """Batched quote fetch, parallelized so 20 symbols don't block serially."""
    parsed = [symbol.strip().upper() for symbol in symbols.split(",") if symbol.strip()][:20]
    if not parsed:
        return {"quotes": []}

    def fetch(sym: str) -> dict:
        try:
            return _research_service.quote(sym)
        except Exception:
            return {"symbol": sym, "error": True}

    with ThreadPoolExecutor(max_workers=min(len(parsed), 10)) as ex:
        results = list(ex.map(fetch, parsed))
    return {"quotes": results}


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


# ---- Analytics ----
#
# Risk-adjusted return metrics, computed from daily closes. Uses a small
# ad-hoc TTL cache so heavy pages (portfolio analytics, screener) don't
# re-fetch history for every row when panning the UI.


_analytics_cache: dict[str, tuple[float, Any]] = {}
_ANALYTICS_STALE_TTL_SECONDS = 60 * 60


def _analytics_cached(key: str, producer: Callable[[], Any]) -> Any:
    """Fresh if cached < ANALYTICS_TTL; fall back to stale (<= 1h) on failure."""
    import time

    now = time.time()
    hit = _analytics_cache.get(key)
    if hit and now - hit[0] < ANALYTICS_TTL_SECONDS:
        return hit[1]
    try:
        value = producer()
    except Exception:
        if hit and now - hit[0] < _ANALYTICS_STALE_TTL_SECONDS:
            return hit[1]
        raise
    _analytics_cache[key] = (now, value)
    return value


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

    return _analytics_cached(f"closes:{symbol.upper()}:{period}", produce)


def _stats_from_returns(returns: list[float]) -> dict:
    """Sharpe, Sortino, annualized vol, max drawdown, cumulative return.

    Sharpe/Sortino here are excess-over-zero — no risk-free adjustment.
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

    Risk contribution uses w_i · cov(r_i, r_p) / var(r_p). Contributions sum
    to 1 by construction — the standard variance decomposition.
    """
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


def _twelve_one_return(closes: list[tuple[str, float]]) -> float | None:
    """12-minus-1 momentum: return from earliest close to the close ~1 month ago.

    Recent-month returns mean-revert, so pure 12M smuggles reversal noise into
    the signal. Dropping the last month is the canonical academic construction.
    """
    if len(closes) < 2:
        return None
    latest = closes[-1][0]
    cutoff = _month_ago_iso(latest)
    trimmed = [(d, c) for d, c in closes if d <= cutoff]
    if len(trimmed) < 2:
        return None
    start = trimmed[0][1]
    end = trimmed[-1][1]
    if not start:
        return None
    return end / start - 1.0


def _month_ago_iso(latest_iso: str) -> str:
    from datetime import date, timedelta

    try:
        y, m, d = (int(x) for x in latest_iso.split("-"))
        return (date(y, m, d) - timedelta(days=30)).isoformat()
    except Exception:
        return latest_iso


def _ytd_start_iso(latest_iso: str) -> str:
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
        return clean_json(
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

    return clean_json(
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
# Ranks a set of symbols across four factor buckets:
#   Value     — cheap on forward P/E / PEG / P/B / P/S / FCF yield
#   Quality   — high ROE, margins, revenue growth, low leverage
#   Return    — Sharpe and market-adjusted alpha vs SPY
#   Momentum  — 1M / YTD / 12-1 returns
#
# Value and Quality score by SECTOR-RELATIVE PERCENTILE — a P/E of 15 is cheap
# for a bank, expensive for software. Return/Momentum stay on absolute scales.


SECTOR_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "ORCL",
    "JPM", "BAC", "GS", "MS", "WFC",
    "JNJ", "UNH", "PFE", "MRK", "ABBV",
    "AMZN", "HD", "MCD", "NKE", "SBUX",
    "WMT", "PG", "KO", "PEP", "COST",
    "BA", "CAT", "GE", "HON", "UNP",
    "XOM", "CVX", "COP", "SLB",
    "NEE", "SO", "DUK",
    "LIN", "FCX", "ECL",
    "PLD", "AMT", "O",
    "DIS", "NFLX", "T", "VZ",
]


def _clamp01(x: float) -> float:
    return max(0.0, min(100.0, x))


def _fcf_yield(info_row: dict) -> float | None:
    """FCF / market cap. Retail-friendly value signal, harder to fake than PE."""
    fcf = info_row.get("freeCashflow")
    mc = info_row.get("marketCap")
    if isinstance(fcf, (int, float)) and isinstance(mc, (int, float)) and mc > 0:
        return fcf / mc
    return None


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
        True,  lambda v: _clamp01(v * 100 * 15)),
]

QUALITY_FACTORS: list[tuple[str, Any, bool, Any]] = [
    ("roe",           lambda i: i.get("returnOnEquity"),
        True,  lambda v: _clamp01(v * 100 * 3)),
    ("profitMargin",  lambda i: i.get("profitMargins"),
        True,  lambda v: _clamp01(v * 100 * 5)),
    ("revenueGrowth", lambda i: i.get("revenueGrowth"),
        True,  lambda v: _clamp01(v * 100 * 3)),
    ("debtToEquity",  lambda i: i.get("debtToEquity"),
        False, lambda v: _clamp01(100.0 - v * 0.5)),
]


def _percentile_rank(value: float, samples: list[float]) -> float:
    if not samples:
        return 0.5
    return sum(1 for s in samples if s <= value) / len(samples)


def _build_sector_stats(infos: list[dict]) -> dict[str, dict[str, list[float]]]:
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
    """Score a factor bucket, preferring sector percentiles over absolute scales."""
    parts: list[float] = []
    basis: dict[str, str] = {}
    sector = info_row.get("sector")
    for key, extract, higher, absolute in factors_spec:
        v = extract(info_row)
        if not isinstance(v, (int, float)) or math.isnan(v) or math.isinf(v):
            continue
        samples = (sector_stats.get(sector) or {}).get(key) if sector else None
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
    parts: list[float] = []
    if isinstance(sharpe, (int, float)):
        parts.append(_clamp01((sharpe + 1) * 100 / 3))
    if isinstance(alpha, (int, float)):
        parts.append(_clamp01((alpha + 0.1) * 100 / 0.3))
    return sum(parts) / len(parts) if parts else None


def _score_momentum(
    one_month: float | None, ytd: float | None, twelve_one: float | None
) -> float | None:
    parts: list[float] = []
    for v in (one_month, ytd, twelve_one):
        if isinstance(v, (int, float)):
            parts.append(_clamp01((v + 0.2) * 100 / 0.6))
    return sum(parts) / len(parts) if parts else None


def _signals(factors: dict) -> list[str]:
    """Boolean flags — the multi-factor case for the stock at a glance.

    Value-trap guard: a stock that looks cheap but is shrinking on the top line
    is more likely a value trap. Strip the `value`/`cheap` chips and emit `trap`.
    """
    out: list[str] = []
    fpe = factors.get("forwardPE")
    peg = factors.get("pegRatio")
    pb = factors.get("priceToBook")
    rg = factors.get("revenueGrowth")
    eg = factors.get("earningsGrowth")

    would_be_value = (
        isinstance(fpe, (int, float)) and 0 < fpe < 20
        and (
            (isinstance(peg, (int, float)) and 0 < peg < 1.5)
            or (isinstance(pb, (int, float)) and 0 < pb < 3)
        )
    )
    would_be_cheap = isinstance(fpe, (int, float)) and 0 < fpe < 15
    shrinking = (
        (isinstance(rg, (int, float)) and rg < 0)
        or (isinstance(eg, (int, float)) and eg < -0.10)
    )

    if (would_be_value or would_be_cheap) and shrinking:
        out.append("trap")
    else:
        if would_be_value:
            out.append("value")
        if would_be_cheap:
            out.append("cheap")

    fcfy = factors.get("fcfYield")
    if isinstance(fcfy, (int, float)) and fcfy > 0.05:
        out.append("fcfy+")
    roe = factors.get("roe")
    pm = factors.get("profitMargin")
    if isinstance(roe, (int, float)) and roe > 0.15 and isinstance(pm, (int, float)) and pm > 0.10:
        out.append("quality")
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


_TOTAL_FACTOR_INPUTS = (
    len(VALUE_FACTORS) + len(QUALITY_FACTORS)
    + 2   # return: sharpe, alpha
    + 3   # momentum: oneMonth, ytd, twelveOneMomentum
)


def _coverage(value_basis: dict, quality_basis: dict, factors: dict) -> dict:
    """Fraction of the 14 possible factor inputs that were actually available."""
    used = len(value_basis) + len(quality_basis)
    for k in ("sharpe", "alpha"):
        if isinstance(factors.get(k), (int, float)):
            used += 1
    for k in ("oneMonth", "ytd", "twelveOneMomentum"):
        if isinstance(factors.get(k), (int, float)):
            used += 1
    return {
        "used": used,
        "total": _TOTAL_FACTOR_INPUTS,
        "ratio": used / _TOTAL_FACTOR_INPUTS if _TOTAL_FACTOR_INPUTS else None,
    }


def _screener_info(symbol: str) -> dict:
    """Full `.info` for scoring — includes fields (pegRatio, earningsGrowth,
    targetMeanPrice, marketCap) the research-service `info` endpoint filters out.
    """
    def produce() -> dict:
        raw = _ticker(symbol).info or {}
        return clean_json(raw)

    try:
        return _analytics_cached(f"screener-info:{symbol.upper()}", produce)
    except Exception:
        return {}


@app.get("/api/screener")
def screener(
    symbols: str | None = Query(
        None,
        description="Comma-separated symbols; defaults to watchlist + holdings",
    ),
) -> dict:
    """Multi-factor screener: composite score + signal chips per symbol.

    Value and Quality score against sector percentiles (hardcoded universe of
    ~45 sector reps). Return/Momentum use absolute scales. Composite is
    coverage-weighted so thin rows can't outrank well-covered ones.
    """
    if symbols:
        syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        wl = _dashboard_service.watchlist()
        holdings_syms = list({h["symbol"] for h in _holdings_raw()})
        syms = sorted(set(wl) | set(holdings_syms))
    syms = syms[:30]
    if not syms:
        return {"rows": [], "benchmark": BENCHMARK_SYMBOL, "period": "1y"}

    universe = sorted(set(syms) | set(SECTOR_UNIVERSE))

    with ThreadPoolExecutor(max_workers=min(len(universe), 12)) as ex:
        info_by_sym = dict(zip(universe, ex.map(_screener_info, universe)))

    sector_stats = _build_sector_stats(list(info_by_sym.values()))

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
        twelve_one = None
        beta = None
        alpha = None
        if len(closes) >= 2:
            sym_returns_by_date = _returns_by_date(closes)
            stats = _stats_from_returns(list(sym_returns_by_date.values()))
            latest = closes[-1][0]
            one_month = _range_return(closes, _month_ago_iso(latest))
            ytd = _range_return(closes, _ytd_start_iso(latest))
            twelve_one = _twelve_one_return(closes)
            if bench_returns_by_date:
                beta, alpha = _beta_alpha(sym_returns_by_date, bench_returns_by_date)

        value_score, value_basis = _score_bucket(info_row, VALUE_FACTORS, sector_stats)
        quality_score, quality_basis = _score_bucket(info_row, QUALITY_FACTORS, sector_stats)

        factors = {
            "forwardPE": info_row.get("forwardPE"),
            "trailingPE": info_row.get("trailingPE"),
            "pegRatio": info_row.get("pegRatio"),
            "priceToBook": info_row.get("priceToBook"),
            "priceToSales": info_row.get("priceToSalesTrailing12Months"),
            "fcfYield": _fcf_yield(info_row),
            "dividendYield": info_row.get("dividendYield"),
            "roe": info_row.get("returnOnEquity"),
            "profitMargin": info_row.get("profitMargins"),
            "revenueGrowth": info_row.get("revenueGrowth"),
            "earningsGrowth": info_row.get("earningsGrowth"),
            "debtToEquity": info_row.get("debtToEquity"),
            "sharpe": stats.get("sharpe"),
            "sortino": stats.get("sortino"),
            "volatility": stats.get("volatility"),
            "maxDrawdown": stats.get("maxDrawdown"),
            "cumulativeReturn": stats.get("cumulativeReturn"),
            "alpha": alpha,
            "beta": beta,
            "oneMonth": one_month,
            "ytd": ytd,
            "twelveOneMomentum": twelve_one,
            "targetMean": target,
            "targetUpside": target_upside,
            "recommendationMean": info_row.get("recommendationMean"),
            "numberOfAnalystOpinions": info_row.get("numberOfAnalystOpinions"),
        }

        scores = {
            "value": value_score,
            "quality": quality_score,
            "return": _score_return(stats.get("sharpe"), alpha),
            "momentum": _score_momentum(one_month, ytd, twelve_one),
        }
        available = [v for v in scores.values() if isinstance(v, (int, float))]
        raw_composite = sum(available) / len(available) if available else None

        # sqrt keeps the coverage penalty gentle — full coverage passes through,
        # 50% scales to ~0.71x, 25% to 0.5x.
        cov = _coverage(value_basis, quality_basis, factors)
        composite = raw_composite
        if raw_composite is not None and isinstance(cov.get("ratio"), (int, float)):
            composite = raw_composite * math.sqrt(cov["ratio"])

        return {
            "symbol": sym,
            "name": info_row.get("shortName") or info_row.get("longName"),
            "sector": info_row.get("sector"),
            "price": price,
            "compositeScore": composite,
            "rawCompositeScore": raw_composite,
            "scores": scores,
            "scoring": {"value": value_basis, "quality": quality_basis},
            "coverage": cov,
            "factors": factors,
            "signals": _signals(factors),
        }

    with ThreadPoolExecutor(max_workers=min(len(syms), 10)) as ex:
        rows = list(ex.map(compute, syms))
    rows.sort(key=lambda r: r.get("compositeScore") or 0.0, reverse=True)

    peers_by_sector = {sec: {k: len(v) for k, v in bucket.items()} for sec, bucket in sector_stats.items()}

    return clean_json(
        {
            "benchmark": BENCHMARK_SYMBOL,
            "period": "1y",
            "sectorPeerCounts": peers_by_sector,
            "rows": rows,
        }
    )


# ---- Watchlist / holdings ----


@app.get("/api/watchlist")
def watchlist_get() -> dict:
    return {"symbols": _dashboard_service.watchlist()}


@app.get("/api/watchlist/quotes")
def watchlist_quotes() -> dict:
    """Latest quotes for every symbol in the watchlist, parallelized."""
    syms = _dashboard_service.watchlist()
    if not syms:
        return {"symbols": [], "quotes": []}

    def fetch(sym: str) -> dict:
        try:
            return _research_service.quote(sym)
        except Exception:
            return {"symbol": sym, "error": True}

    with ThreadPoolExecutor(max_workers=min(len(syms), 10)) as ex:
        results = list(ex.map(fetch, syms))
    return {"symbols": syms, "quotes": results}


@app.get("/api/watchlist/analytics")
def watchlist_analytics() -> dict:
    """Per-symbol 1M return, YTD return, 1Y Sharpe and 1Y annualized vol."""
    syms = _dashboard_service.watchlist()
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
    return clean_json({"symbols": syms, "metrics": metrics})


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


@app.get("/api/portfolio/analytics")
def portfolio_analytics(
    period: str = Query("1y", description="1mo,3mo,6mo,1y,2y,5y,10y,ytd,max"),
) -> dict:
    """Risk-adjusted return metrics + cumulative-return curve vs SPY.

    Assumes current shares held throughout `period` — a standard dashboard
    simplification, not an accurate historical P&L.
    """
    raw = _holdings_raw()
    if not raw:
        return {
            "period": period,
            "benchmark": BENCHMARK_SYMBOL,
            "kpis": {},
            "curve": [],
        }

    unique_syms = list({h["symbol"] for h in raw})
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

    return clean_json(
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
