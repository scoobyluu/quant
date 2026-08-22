import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl

from quant.market_analysis import get_index_symbols
from quant.storage import save_market_data


class MarketAnalysisUniverseTests(unittest.TestCase):
    def test_uses_cached_index_symbols_without_network_access(self) -> None:
        cached = pl.DataFrame(
            {
                "Date": [date(2026, 8, 21), date(2026, 8, 21)],
                "Symbol": ["AAPL", "MSFT"],
            }
        )

        with TemporaryDirectory() as directory:
            path = Path(directory) / "index.parquet"
            save_market_data(cached, path)

            self.assertEqual(get_index_symbols(path), ["AAPL", "MSFT"])


if __name__ == "__main__":
    unittest.main()
