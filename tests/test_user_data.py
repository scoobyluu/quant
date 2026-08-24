import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from quant.user_data import DEFAULT_WATCHLIST, UserDataRepository


class UserDataRepositoryTests(unittest.TestCase):
    def test_initializes_database_and_does_not_reseed_after_deletion(self) -> None:
        with TemporaryDirectory() as directory:
            repository = UserDataRepository(Path(directory) / "quant.db")
            repository.initialize()

            self.assertEqual(repository.list_watchlist(), DEFAULT_WATCHLIST)

            position = repository.add_position("AAPL", 2, 100.0, account="roth")
            repository.remove_position(position["id"])
            repository.initialize()
            self.assertEqual(repository.list_positions(), [])

    def test_adds_and_removes_a_position(self) -> None:
        with TemporaryDirectory() as directory:
            repository = UserDataRepository(Path(directory) / "quant.db")
            repository.initialize()
            position = repository.add_position(
                " msft ", 3, 200.0, account="taxable", acquired="2026-02-01"
            )

            self.assertEqual(position["symbol"], "MSFT")
            self.assertTrue(repository.remove_position(position["id"]))
            self.assertFalse(repository.remove_position(position["id"]))

    def test_exposes_positions_as_the_shared_polars_shape(self) -> None:
        with TemporaryDirectory() as directory:
            repository = UserDataRepository(Path(directory) / "quant.db")
            repository.add_position(
                "aapl",
                2,
                100.0,
                account="roth",
                acquired="2026-01-15",
            )

            frame = repository.positions_frame()

        self.assertEqual(
            frame.select("Symbol", "Quantity", "Average Cost", "Account", "Acquired")
            .to_dicts(),
            [
                {
                    "Symbol": "AAPL",
                    "Quantity": 2.0,
                    "Average Cost": 100.0,
                    "Account": "roth",
                    "Acquired": "2026-01-15",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
