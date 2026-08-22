import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl
from polars.testing import assert_frame_equal

from quant.storage import load_market_data, save_market_data


class MarketDataStorageTests(unittest.TestCase):
    def test_round_trips_market_data_through_parquet(self) -> None:
        market_data = pl.DataFrame(
            {
                "Date": [date(2026, 8, 21)],
                "Symbol": ["AAPL"],
                "Company": ["Apple Inc."],
                "Last Price": [225.50],
                "Volume": [2_000_000],
            }
        )

        with TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "market_data.parquet"
            save_market_data(market_data, path)

            self.assertTrue(path.exists())
            assert_frame_equal(load_market_data(path), market_data)


if __name__ == "__main__":
    unittest.main()
