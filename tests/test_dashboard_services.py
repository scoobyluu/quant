import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import polars as pl

from quant.dashboard.services import DashboardService
from quant.user_data import UserDataRepository


class DashboardServiceTests(unittest.TestCase):
    @patch("quant.dashboard.services.load_portfolio_market_data")
    def test_values_persisted_lots_with_core_analysis(self, load_market_data) -> None:
        load_market_data.return_value = pl.DataFrame(
            {
                "Date": [date(2026, 8, 21)],
                "Symbol": ["AAPL"],
                "Last Price": [125.0],
                "Volume": [1_000],
            }
        )
        with TemporaryDirectory() as directory:
            repository = UserDataRepository(Path(directory) / "quant.db")
            repository.initialize(Path(directory) / "missing.csv")
            repository.add_position(
                "AAPL",
                2,
                100.0,
                account="roth",
                asset_class="equity",
                sector="technology",
            )
            service = DashboardService(repository)

            result = service.holdings()

        holding = result["holdings"][0]
        self.assertEqual(holding["marketValue"], 250.0)
        self.assertEqual(holding["gain"], 50.0)
        self.assertEqual(holding["weightPercent"], 100.0)
        self.assertEqual(holding["account"], "roth")
        self.assertEqual(result["totals"]["gainPercent"], 25.0)

    @patch("quant.dashboard.services.analyze_symbols")
    def test_serializes_core_market_indicators(self, analyze_symbols) -> None:
        analyze_symbols.return_value = pl.DataFrame(
            {
                "Date": [date(2026, 8, 21)],
                "Symbol": ["AAPL"],
                "Close": [125.0],
                "Adjusted Close": [124.0],
                "Daily Change": [1.0],
                "Daily Change %": [0.8],
                "SMA 20": [120.0],
                "Rolling High 20": [130.0],
                "Rolling Low 20": [110.0],
                "Volume SMA 20": [1_000.0],
                "Relative Volume 20": [1.2],
            }
        )
        service = DashboardService()

        result = service.market_analysis("aapl", [20], "adjusted")

        self.assertEqual(result["symbol"], "AAPL")
        self.assertEqual(result["rows"][0]["price"], 124.0)
        self.assertEqual(result["rows"][0]["movingAverages"]["20"], 120.0)


if __name__ == "__main__":
    unittest.main()
