import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl

from quant.user_data import DEFAULT_WATCHLIST, UserDataRepository


class UserDataRepositoryTests(unittest.TestCase):
    def test_seeds_metadata_and_does_not_reseed_after_deletion(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            portfolio_path = root / "portfolio.csv"
            database_path = root / "quant.db"
            pl.DataFrame(
                {
                    "ticker": ["AAPL"],
                    "quantity": [2],
                    "cost": [100.0],
                    "account": ["roth"],
                    "asset_class": ["equity"],
                    "sector": ["technology"],
                    "acquired": ["2026-01-15"],
                }
            ).write_csv(portfolio_path)
            repository = UserDataRepository(database_path)
            repository.initialize(portfolio_path)

            position = repository.list_positions()[0]
            self.assertEqual(position["symbol"], "AAPL")
            self.assertEqual(position["account"], "roth")
            self.assertEqual(position["asset_class"], "equity")
            self.assertEqual(position["acquired"], "2026-01-15")
            self.assertEqual(repository.list_watchlist(), DEFAULT_WATCHLIST)

            repository.remove_position(position["id"])
            repository.initialize(portfolio_path)
            self.assertEqual(repository.list_positions(), [])

    def test_adds_and_removes_a_position(self) -> None:
        with TemporaryDirectory() as directory:
            repository = UserDataRepository(Path(directory) / "quant.db")
            repository.initialize(Path(directory) / "missing.csv")
            position = repository.add_position(
                " msft ", 3, 200.0, account="taxable", acquired="2026-02-01"
            )

            self.assertEqual(position["symbol"], "MSFT")
            self.assertTrue(repository.remove_position(position["id"]))
            self.assertFalse(repository.remove_position(position["id"]))


if __name__ == "__main__":
    unittest.main()
