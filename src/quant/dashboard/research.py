from __future__ import annotations

import math
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import yfinance as yf


class YahooResearchService:
    def __init__(
        self,
        cache_ttl_seconds: int = 30,
        stale_ttl_seconds: int = 60 * 60,
    ) -> None:
        self.cache_ttl_seconds = cache_ttl_seconds
        self.stale_ttl_seconds = stale_ttl_seconds
        self._cache: dict[str, tuple[float, Any]] = {}
        try:
            from curl_cffi import requests as curl_requests  # type: ignore

            self._session = curl_requests.Session(impersonate="chrome")
        except Exception:
            self._session = None

    def quotes(self, symbols: list[str]) -> dict:
        results = []
        for symbol in symbols[:20]:
            try:
                results.append(self.quote(symbol))
            except Exception:
                results.append({"symbol": symbol.upper(), "error": True})
        return {"quotes": results}

    def quote(self, symbol: str) -> dict:
        symbol = symbol.upper()

        def produce() -> dict:
            info = self._ticker(symbol).info or {}
            price = (
                info.get("regularMarketPrice")
                or info.get("currentPrice")
                or info.get("previousClose")
            )
            previous_close = info.get("regularMarketPreviousClose") or info.get(
                "previousClose"
            )
            change = price - previous_close if price is not None and previous_close else None
            return clean_json(
                {
                    "symbol": symbol,
                    "name": info.get("shortName") or info.get("longName"),
                    "price": price,
                    "previousClose": previous_close,
                    "change": change,
                    "changePercent": (
                        change / previous_close * 100
                        if change is not None and previous_close
                        else None
                    ),
                    "currency": info.get("currency"),
                    "marketState": info.get("marketState"),
                    "dayHigh": info.get("dayHigh"),
                    "dayLow": info.get("dayLow"),
                    "volume": info.get("volume") or info.get("regularMarketVolume"),
                }
            )

        return self._cached(f"quote:{symbol}", produce)

    def history(self, symbol: str, period: str, interval: str) -> dict:
        symbol = symbol.upper()

        def produce() -> dict:
            frame = self._ticker(symbol).history(
                period=period, interval=interval, auto_adjust=False
            )
            if frame.empty:
                return {
                    "symbol": symbol,
                    "period": period,
                    "interval": interval,
                    "candles": [],
                }
            frame = frame.reset_index()
            date_column = "Datetime" if "Datetime" in frame.columns else "Date"
            candles = [
                {
                    "t": (
                        row[date_column].isoformat()
                        if hasattr(row[date_column], "isoformat")
                        else str(row[date_column])
                    ),
                    "open": row.get("Open"),
                    "high": row.get("High"),
                    "low": row.get("Low"),
                    "close": row.get("Close"),
                    "volume": row.get("Volume"),
                }
                for _, row in frame.iterrows()
            ]
            return clean_json(
                {
                    "symbol": symbol,
                    "period": period,
                    "interval": interval,
                    "candles": candles,
                }
            )

        return self._cached(f"history:{symbol}:{period}:{interval}", produce)

    def info(self, symbol: str) -> dict:
        symbol = symbol.upper()
        keys = [
            "shortName", "longName", "symbol", "sector", "industry", "country",
            "website", "longBusinessSummary", "fullTimeEmployees", "marketCap",
            "enterpriseValue", "trailingPE", "forwardPE", "priceToBook",
            "priceToSalesTrailing12Months", "trailingEps", "forwardEps",
            "dividendRate", "dividendYield", "payoutRatio",
            "fiveYearAvgDividendYield", "beta", "52WeekChange",
            "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "fiftyDayAverage",
            "twoHundredDayAverage", "profitMargins", "operatingMargins",
            "grossMargins", "ebitdaMargins", "returnOnAssets", "returnOnEquity",
            "totalRevenue", "revenuePerShare", "revenueGrowth", "grossProfits",
            "ebitda", "netIncomeToCommon", "totalCash", "totalDebt",
            "debtToEquity", "currentRatio", "quickRatio", "freeCashflow",
            "operatingCashflow", "sharesOutstanding", "floatShares",
            "heldPercentInsiders", "heldPercentInstitutions", "averageVolume",
            "averageVolume10days",
        ]

        def produce() -> dict:
            raw = self._ticker(symbol).info or {}
            return clean_json({key: raw.get(key) for key in keys})

        return self._cached(f"info:{symbol}", produce)

    def analyst(self, symbol: str) -> dict:
        symbol = symbol.upper()

        def produce() -> dict:
            ticker = self._ticker(symbol)
            raw = ticker.info or {}
            result: dict[str, Any] = {
                "symbol": symbol,
                "recommendationKey": raw.get("recommendationKey"),
                "recommendationMean": raw.get("recommendationMean"),
                "numberOfAnalystOpinions": raw.get("numberOfAnalystOpinions"),
                "targetHigh": raw.get("targetHighPrice"),
                "targetLow": raw.get("targetLowPrice"),
                "targetMean": raw.get("targetMeanPrice"),
                "targetMedian": raw.get("targetMedianPrice"),
            }
            try:
                recommendations = ticker.recommendations
                result["recommendations"] = (
                    recommendations.tail(25).reset_index().to_dict(orient="records")
                    if recommendations is not None and not recommendations.empty
                    else []
                )
            except Exception:
                result["recommendations"] = []
            try:
                changes = ticker.upgrades_downgrades
                result["upgradesDowngrades"] = (
                    changes.head(25).reset_index().to_dict(orient="records")
                    if changes is not None and not changes.empty
                    else []
                )
            except Exception:
                result["upgradesDowngrades"] = []
            return clean_json(result)

        return self._cached(f"analyst:{symbol}", produce)

    def earnings(self, symbol: str) -> dict:
        symbol = symbol.upper()

        def produce() -> dict:
            ticker = self._ticker(symbol)
            next_earnings = None
            eps_estimate: dict[str, Any] = {}
            revenue_estimate: dict[str, Any] = {}
            dividend_date = None
            ex_dividend_date = None
            try:
                calendar = ticker.calendar
                if isinstance(calendar, dict):
                    dates = calendar.get("Earnings Date")
                    if isinstance(dates, list) and dates:
                        next_earnings = _date_string(dates[0])
                    for low, high, average, output in (
                        ("Earnings Low", "Earnings High", "Earnings Average", eps_estimate),
                        ("Revenue Low", "Revenue High", "Revenue Average", revenue_estimate),
                    ):
                        output.update(
                            low=calendar.get(low),
                            high=calendar.get(high),
                            average=calendar.get(average),
                        )
                    dividend_date = _date_string(calendar.get("Dividend Date"))
                    ex_dividend_date = _date_string(calendar.get("Ex-Dividend Date"))
            except Exception:
                pass
            history = []
            try:
                earnings_dates = ticker.earnings_dates
                if earnings_dates is not None and not earnings_dates.empty:
                    frame = earnings_dates.reset_index()
                    date_column = frame.columns[0]
                    history = [
                        {
                            "date": _date_string(row[date_column]),
                            "epsEstimate": row.get("EPS Estimate"),
                            "epsReported": row.get("Reported EPS"),
                            "surprisePercent": row.get("Surprise(%)"),
                        }
                        for _, row in frame.head(12).iterrows()
                    ]
            except Exception:
                pass
            return clean_json(
                {
                    "symbol": symbol,
                    "nextEarnings": next_earnings,
                    "epsEstimate": eps_estimate,
                    "revenueEstimate": revenue_estimate,
                    "dividendDate": dividend_date,
                    "exDividendDate": ex_dividend_date,
                    "history": history,
                }
            )

        return self._cached(f"earnings:{symbol}", produce)

    def options(self, symbol: str, expiration: str | None = None) -> dict:
        symbol = symbol.upper()

        def produce() -> dict:
            ticker = self._ticker(symbol)
            try:
                expirations = list(ticker.options or [])
            except Exception:
                expirations = []
            chosen = expiration or (expirations[0] if expirations else None)
            calls: list = []
            puts: list = []
            if chosen:
                try:
                    chain = ticker.option_chain(chosen)
                    calls = (
                        chain.calls.to_dict(orient="records")
                        if chain.calls is not None
                        else []
                    )
                    puts = (
                        chain.puts.to_dict(orient="records")
                        if chain.puts is not None
                        else []
                    )
                except Exception:
                    pass
            return clean_json(
                {
                    "symbol": symbol,
                    "expirations": expirations,
                    "expiration": chosen,
                    "calls": calls,
                    "puts": puts,
                }
            )

        return self._cached(f"options:{symbol}:{expiration or ''}", produce)

    def news(self, symbol: str) -> dict:
        symbol = symbol.upper()

        def produce() -> dict:
            try:
                items = self._ticker(symbol).news or []
            except Exception:
                items = []
            normalized = []
            for item in items[:25]:
                content = item.get("content") if isinstance(item, dict) else None
                if isinstance(content, dict):
                    provider = content.get("provider")
                    canonical = content.get("canonicalUrl")
                    normalized.append(
                        {
                            "title": content.get("title"),
                            "publisher": (
                                provider.get("displayName")
                                if isinstance(provider, dict)
                                else content.get("publisher")
                            ),
                            "link": (
                                canonical.get("url")
                                if isinstance(canonical, dict)
                                else content.get("link")
                            ),
                            "publishedAt": content.get("pubDate")
                            or content.get("displayTime"),
                            "summary": content.get("summary"),
                            "thumbnail": _pick_thumbnail(content.get("thumbnail")),
                        }
                    )
                elif isinstance(item, dict):
                    normalized.append(
                        {
                            "title": item.get("title"),
                            "publisher": item.get("publisher"),
                            "link": item.get("link"),
                            "publishedAt": item.get("providerPublishTime"),
                            "summary": item.get("summary"),
                            "thumbnail": _pick_thumbnail(item.get("thumbnail")),
                        }
                    )
            return clean_json({"symbol": symbol, "items": normalized})

        return self._cached(f"news:{symbol}", produce)

    def news_feed(self, symbols: list[str]) -> dict:
        symbols = [symbol.upper() for symbol in symbols[:10]]

        def produce() -> dict:
            merged = []
            seen = set()
            for symbol in symbols:
                try:
                    items = self.news(symbol).get("items") or []
                except Exception:
                    items = []
                for item in items:
                    key = item.get("link") or item.get("title")
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    merged.append({**item, "symbol": symbol})
            merged.sort(key=_news_timestamp, reverse=True)
            return clean_json({"symbols": symbols, "items": merged[:30]})

        return self._cached(f"feed:{','.join(symbols)}", produce)

    def search(self, query: str) -> dict:
        def produce() -> dict:
            try:
                quotes = yf.Search(query, max_results=10).quotes or []
            except Exception:
                quotes = []
            return clean_json(
                {
                    "query": query,
                    "results": [
                        {
                            "symbol": item.get("symbol"),
                            "name": item.get("shortname") or item.get("longname"),
                            "exchange": item.get("exchDisp") or item.get("exchange"),
                            "type": item.get("typeDisp") or item.get("quoteType"),
                        }
                        for item in quotes
                    ],
                }
            )

        return self._cached(f"search:{query}", produce)

    def _ticker(self, symbol: str) -> yf.Ticker:
        if self._session is not None:
            return yf.Ticker(symbol, session=self._session)
        return yf.Ticker(symbol)

    def _cached(self, key: str, producer: Callable[[], Any]) -> Any:
        now = time.time()
        hit = self._cache.get(key)
        if hit and now - hit[0] < self.cache_ttl_seconds:
            return hit[1]
        try:
            value = producer()
        except Exception:
            if hit and now - hit[0] < self.stale_ttl_seconds:
                return hit[1]
            raise
        self._cache[key] = (now, value)
        return value


def clean_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, (int, str, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): clean_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(item) for item in value]
    return str(value)


def _pick_thumbnail(thumbnail: Any) -> str | None:
    if not isinstance(thumbnail, dict):
        return None
    resolutions = thumbnail.get("resolutions") or []
    candidates = [
        item
        for item in resolutions
        if isinstance(item, dict) and item.get("url")
    ]
    preferred = [
        item
        for item in candidates
        if isinstance(item.get("width"), (int, float))
        and 100 <= item["width"] <= 400
    ]
    if preferred:
        return min(preferred, key=lambda item: item["width"])["url"]
    if candidates:
        return candidates[0]["url"]
    return thumbnail.get("originalUrl") or thumbnail.get("url")


def _date_string(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _news_timestamp(item: dict) -> float:
    value = item.get("publishedAt")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return 0.0
