import unittest
from datetime import date

import polars as pl
from polars.testing import assert_frame_equal

from quant.analysis import analyze_portfolio, summarize_portfolio


class PortfolioAnalysisTests(unittest.TestCase):
    def test_calculates_position_and_portfolio_values(self) -> None:
        positions = pl.DataFrame(
            {
                "Symbol": ["AAA", "BBB"],
                "Quantity": [10.0, 5.0],
                "Average Cost": [8.0, 20.0],
            }
        )
        market_history = pl.DataFrame(
            {
                "Date": [date(2026, 8, 21), date(2026, 8, 21)],
                "Symbol": ["AAA", "BBB"],
                "Last Price": [10.0, 10.0],
                "Volume": [1_000, 2_000],
            }
        )

        result = analyze_portfolio(positions, market_history)
        summary = summarize_portfolio(result)

        self.assertEqual(result.get_column("Symbol").to_list(), ["AAA", "BBB"])
        self.assertAlmostEqual(result.item(0, "Weight %"), 100 / 1.5)
        self.assertAlmostEqual(result.item(1, "Weight %"), 100 / 3)
        self.assertEqual(result.get_column("Gain/Loss").to_list(), [20.0, -50.0])
        assert_frame_equal(
            summary,
            pl.DataFrame(
                {
                    "Cost Basis": [180.0],
                    "Market Value": [150.0],
                    "Gain/Loss": [-30.0],
                    "Gain/Loss %": [-100 / 6],
                }
            ),
        )

    def test_rejects_missing_market_data(self) -> None:
        positions = pl.DataFrame(
            {"Symbol": ["AAA"], "Quantity": [1.0], "Average Cost": [10.0]}
        )
        market_history = pl.DataFrame(
            schema={
                "Date": pl.Date,
                "Symbol": pl.String,
                "Last Price": pl.Float64,
            }
        )

        with self.assertRaisesRegex(ValueError, "Missing market data for: AAA"):
            analyze_portfolio(positions, market_history)
