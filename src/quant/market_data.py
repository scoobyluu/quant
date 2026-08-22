from collections.abc import Iterable
import math
from urllib.request import Request, urlopen

from lxml import html as lxml_html
import polars as pl
import yfinance as yf


S_AND_P_500_CONSTITUENTS_URL = (
    "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
)


def get_sp500_constituents() -> pl.DataFrame:
    request = Request(
        S_AND_P_500_CONSTITUENTS_URL,
        headers={"User-Agent": "quant/0.1 (portfolio research)"},
    )
    with urlopen(request, timeout=30) as response:
        html = response.read()

    document = lxml_html.fromstring(html)
    table = document.get_element_by_id("constituents")
    rows = []
    for row in table.xpath(".//tbody/tr"):
        cells = row.xpath("./td")
        if len(cells) < 2:
            continue
        rows.append(
            {
                "Symbol": "".join(cells[0].itertext()).strip(),
                "Company": "".join(cells[1].itertext()).strip(),
            }
        )

    return pl.DataFrame(
        rows,
        schema={"Symbol": pl.String, "Company": pl.String},
    )


def get_latest_market_data(symbols: Iterable[str]) -> pl.DataFrame:
    symbols = list(dict.fromkeys(symbols))
    if not symbols:
        return pl.DataFrame(
            schema={
                "Symbol": pl.String,
                "Last Price": pl.Float64,
                "Volume": pl.Int64,
            }
        )

    # Yahoo uses dashes for share classes while the index uses dots.
    yahoo_to_index_symbol = {symbol.replace(".", "-"): symbol for symbol in symbols}
    history = yf.download(
        tickers=list(yahoo_to_index_symbol),
        period="5d",
        interval="1d",
        auto_adjust=False,
        progress=False,
        threads=True,
        group_by="column",
    )
    if history.empty:
        raise RuntimeError("No market data returned for the S&P 500 constituents")

    closes = history["Close"]
    volumes = history["Volume"]
    rows = []
    for yahoo_symbol, index_symbol in yahoo_to_index_symbol.items():
        if yahoo_symbol not in closes.columns:
            rows.append({"Symbol": index_symbol, "Last Price": None, "Volume": None})
            continue

        prices = closes[yahoo_symbol].dropna()
        if prices.empty:
            rows.append({"Symbol": index_symbol, "Last Price": None, "Volume": None})
            continue

        latest_date = prices.index[-1]
        volume = volumes.at[latest_date, yahoo_symbol]
        rows.append(
            {
                "Symbol": index_symbol,
                "Last Price": float(prices.iloc[-1]),
                "Volume": (
                    None
                    if volume is None or math.isnan(float(volume))
                    else int(volume)
                ),
            }
        )

    return pl.DataFrame(
        rows,
        schema={
            "Symbol": pl.String,
            "Last Price": pl.Float64,
            "Volume": pl.Int64,
        },
    )


def get_sp500_market_data() -> pl.DataFrame:
    constituents = get_sp500_constituents()
    market_data = get_latest_market_data(
        constituents.get_column("Symbol").to_list()
    )
    return constituents.join(
        market_data,
        on="Symbol",
        how="left",
        validate="1:1",
    )
