import unittest
from unittest.mock import patch

from quant.market_data import get_latest_market_data


class FakeSeries:
    def __init__(self, value, date) -> None:
        self.value = value
        self.index = [date]
        self.empty = False
        self.iloc = self

    def dropna(self):
        return self

    def __getitem__(self, index):
        return self.value


class FakeColumns:
    def __init__(self, values, date) -> None:
        self.values = values
        self.date = date
        self.columns = values.keys()
        self.at = self

    def __getitem__(self, key):
        if isinstance(key, tuple):
            _, symbol = key
            return self.values[symbol]
        return FakeSeries(self.values[key], self.date)


class FakeHistory:
    empty = False

    def __init__(self) -> None:
        date = "2026-08-21"
        self.columns = {
            "Close": FakeColumns({"BRK-B": 500.25, "AAPL": 225.50}, date),
            "Volume": FakeColumns({"BRK-B": 1_000_000, "AAPL": 2_000_000}, date),
        }

    def __getitem__(self, column):
        return self.columns[column]


class LatestMarketDataTests(unittest.TestCase):
    @patch("quant.market_data.yf.download", return_value=FakeHistory())
    def test_downloads_all_symbols_in_one_batch(self, download) -> None:
        result = get_latest_market_data(["BRK.B", "AAPL"])

        download.assert_called_once()
        self.assertEqual(download.call_args.kwargs["tickers"], ["BRK-B", "AAPL"])
        self.assertEqual(
            result.to_dicts(),
            [
                {"Symbol": "BRK.B", "Last Price": 500.25, "Volume": 1_000_000},
                {"Symbol": "AAPL", "Last Price": 225.50, "Volume": 2_000_000},
            ],
        )


if __name__ == "__main__":
    unittest.main()
