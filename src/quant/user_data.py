from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import polars as pl

DEFAULT_USER_DATA_PATH = Path("data/quant.db")
DEFAULT_WATCHLIST = ["AAPL", "MSFT", "NVDA", "GOOGL", "TSLA"]


class UserDataRepository:
    def __init__(self, path: Path = DEFAULT_USER_DATA_PATH) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS positions (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    quantity REAL NOT NULL CHECK (quantity > 0),
                    average_cost REAL NOT NULL CHECK (average_cost >= 0),
                    account TEXT,
                    asset_class TEXT,
                    sector TEXT,
                    acquired TEXT
                );
                CREATE TABLE IF NOT EXISTS watchlist (
                    symbol TEXT PRIMARY KEY,
                    sort_order INTEGER NOT NULL
                );
                """
            )
            if not self._is_seeded(connection, "watchlist"):
                connection.executemany(
                    "INSERT OR IGNORE INTO watchlist(symbol, sort_order) VALUES (?, ?)",
                    [(symbol, index) for index, symbol in enumerate(DEFAULT_WATCHLIST)],
                )
                self._mark_seeded(connection, "watchlist")

    def list_positions(self) -> list[dict]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, symbol, quantity, average_cost, account,
                       asset_class, sector, acquired
                FROM positions
                ORDER BY rowid
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def positions_frame(self) -> pl.DataFrame:
        positions = self.list_positions()
        return pl.DataFrame(
            {
                "ID": [position["id"] for position in positions],
                "Symbol": [position["symbol"] for position in positions],
                "Quantity": [position["quantity"] for position in positions],
                "Average Cost": [position["average_cost"] for position in positions],
                "Account": [position["account"] for position in positions],
                "Asset Class": [position["asset_class"] for position in positions],
                "Sector": [position["sector"] for position in positions],
                "Acquired": [position["acquired"] for position in positions],
            },
            schema={
                "ID": pl.String,
                "Symbol": pl.String,
                "Quantity": pl.Float64,
                "Average Cost": pl.Float64,
                "Account": pl.String,
                "Asset Class": pl.String,
                "Sector": pl.String,
                "Acquired": pl.String,
            },
        )

    def add_position(
        self,
        symbol: str,
        quantity: float,
        average_cost: float,
        account: str | None = None,
        asset_class: str | None = None,
        sector: str | None = None,
        acquired: str | None = None,
    ) -> dict:
        self.initialize()
        position = {
            "id": str(uuid.uuid4()),
            "symbol": symbol.strip().upper(),
            "quantity": float(quantity),
            "average_cost": float(average_cost),
            "account": _clean_optional(account),
            "asset_class": _clean_optional(asset_class),
            "sector": _clean_optional(sector),
            "acquired": _clean_optional(acquired),
        }
        if not position["symbol"]:
            raise ValueError("Symbol is required")
        if position["quantity"] <= 0 or position["average_cost"] < 0:
            raise ValueError("Quantity must be positive and cost non-negative")
        if position["acquired"]:
            date.fromisoformat(position["acquired"])
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO positions(
                    id, symbol, quantity, average_cost, account,
                    asset_class, sector, acquired
                ) VALUES (
                    :id, :symbol, :quantity, :average_cost, :account,
                    :asset_class, :sector, :acquired
                )
                """,
                position,
            )
        return position

    def remove_position(self, position_id: str) -> bool:
        self.initialize()
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM positions WHERE id = ?", (position_id,)
            )
        return cursor.rowcount > 0

    def list_watchlist(self) -> list[str]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT symbol FROM watchlist ORDER BY sort_order, rowid"
            ).fetchall()
        return [row["symbol"] for row in rows]

    def add_watchlist(self, symbol: str) -> list[str]:
        self.initialize()
        symbol = symbol.strip().upper()
        if not symbol:
            raise ValueError("Symbol is required")
        with self._connect() as connection:
            next_order = connection.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM watchlist"
            ).fetchone()[0]
            connection.execute(
                "INSERT OR IGNORE INTO watchlist(symbol, sort_order) VALUES (?, ?)",
                (symbol, next_order),
            )
        return self.list_watchlist()

    def remove_watchlist(self, symbol: str) -> list[str]:
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM watchlist WHERE symbol = ?", (symbol.strip().upper(),)
            )
        return self.list_watchlist()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _is_seeded(connection: sqlite3.Connection, key: str) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM metadata WHERE key = ?", (f"seeded:{key}",)
            ).fetchone()
            is not None
        )

    @staticmethod
    def _mark_seeded(connection: sqlite3.Connection, key: str) -> None:
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, '1')",
            (f"seeded:{key}",),
        )


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None
