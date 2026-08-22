import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import polars as pl

from quant.portfolio import load_portfolio, load_portfolio_market_data
from quant.storage import load_market_data, save_market_data


class PortfolioInputTests(unittest.TestCase):
    def test_combines_duplicate_positions_at_weighted_average_cost(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "portfolio.csv"
            pl.DataFrame(
                {
                    "ticker": ["aapl", " AAPL "],
                    "quantity": [2, 1],
                    "cost": [100.0, 130.0],
                }
            ).write_csv(path)

            result = load_portfolio(path)

        self.assertEqual(result.to_dicts(), [
            {"Symbol": "AAPL", "Quantity": 3.0, "Average Cost": 110.0}
        ])

    @patch("quant.quotes.get_market_history")
    def test_downloads_and_caches_only_missing_symbols(self, get_market_history) -> None:
        primary_data = pl.DataFrame(
            {
                "Date": [date(2026, 8, 21)],
                "Symbol": ["AAPL"],
                "Company": ["Apple Inc."],
                "Last Price": [225.0],
                "Volume": [2_000_000],
            }
        )
        downloaded = pl.DataFrame(
            {
                "Date": [date(2026, 8, 21)],
                "Symbol": ["VOO"],
                "Last Price": [700.0],
                "Volume": [3_000_000],
            }
        )
        get_market_history.return_value = downloaded

        with TemporaryDirectory() as directory:
            primary_path = Path(directory) / "market.parquet"
            portfolio_path = Path(directory) / "portfolio.parquet"
            save_market_data(primary_data, primary_path)

            result = load_portfolio_market_data(
                ["AAPL", "VOO"], primary_path, portfolio_path
            )

            get_market_history.assert_called_once_with(["VOO"])
            self.assertEqual(set(result.get_column("Symbol")), {"AAPL", "VOO"})
            self.assertEqual(
                load_market_data(portfolio_path).get_column("Symbol").to_list(),
                ["VOO"],
            )

            get_market_history.reset_mock()
            load_portfolio_market_data(["AAPL", "VOO"], primary_path, portfolio_path)
            get_market_history.assert_not_called()


if __name__ == "__main__":
    unittest.main()
