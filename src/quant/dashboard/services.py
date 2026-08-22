from __future__ import annotations

from pathlib import Path

import polars as pl

from quant.analysis import analyze_portfolio, summarize_allocation, summarize_portfolio
from quant.market_analysis import DEFAULT_MARKET_ANALYSIS_PATH, analyze_symbols
from quant.portfolio import (
    DEFAULT_PORTFOLIO_MARKET_DATA_PATH,
    load_portfolio_market_data,
)
from quant.storage import DEFAULT_MARKET_DATA_PATH
from quant.user_data import UserDataRepository


class DashboardService:
    def __init__(
        self,
        repository: UserDataRepository | None = None,
        index_cache_path: Path = DEFAULT_MARKET_DATA_PATH,
        portfolio_cache_path: Path = DEFAULT_PORTFOLIO_MARKET_DATA_PATH,
        analysis_cache_path: Path = DEFAULT_MARKET_ANALYSIS_PATH,
    ) -> None:
        self.repository = repository or UserDataRepository()
        self.index_cache_path = index_cache_path
        self.portfolio_cache_path = portfolio_cache_path
        self.analysis_cache_path = analysis_cache_path

    def watchlist(self) -> list[str]:
        return self.repository.list_watchlist()

    def add_watchlist(self, symbol: str) -> list[str]:
        return self.repository.add_watchlist(symbol)

    def remove_watchlist(self, symbol: str) -> list[str]:
        return self.repository.remove_watchlist(symbol)

    def holdings(self, refresh: bool = False) -> dict:
        positions = self.repository.list_positions()
        if not positions:
            return {
                "holdings": [],
                "totals": {
                    "cost": 0.0,
                    "value": 0.0,
                    "gain": 0.0,
                    "gainPercent": None,
                },
                "allocations": {
                    "account": [],
                    "assetClass": [],
                    "sector": [],
                },
            }

        frame = pl.DataFrame(
            {
                "ID": [position["id"] for position in positions],
                "Symbol": [position["symbol"] for position in positions],
                "Quantity": [position["quantity"] for position in positions],
                "Average Cost": [position["average_cost"] for position in positions],
                "Account": [position["account"] for position in positions],
                "Asset Class": [position["asset_class"] for position in positions],
                "Sector": [position["sector"] for position in positions],
                "Acquired": [position["acquired"] for position in positions],
            }
        )
        market_data = load_portfolio_market_data(
            frame.get_column("Symbol").unique(maintain_order=True).to_list(),
            self.index_cache_path,
            self.portfolio_cache_path,
            refresh,
        )
        analysis = analyze_portfolio(frame, market_data)
        summary = summarize_portfolio(analysis).row(0, named=True)
        holdings = [self._holding_response(row) for row in analysis.to_dicts()]
        return {
            "holdings": holdings,
            "totals": {
                "cost": summary["Cost Basis"],
                "value": summary["Market Value"],
                "gain": summary["Gain/Loss"],
                "gainPercent": summary["Gain/Loss %"],
            },
            "allocations": {
                "account": self._allocation_response(analysis, "Account"),
                "assetClass": self._allocation_response(analysis, "Asset Class"),
                "sector": self._allocation_response(analysis, "Sector"),
            },
        }

    def add_holding(
        self,
        symbol: str,
        shares: float,
        cost_basis: float,
        account: str | None = None,
        asset_class: str | None = None,
        sector: str | None = None,
        acquired: str | None = None,
    ) -> dict:
        return self.repository.add_position(
            symbol,
            shares,
            cost_basis,
            account,
            asset_class,
            sector,
            acquired,
        )

    def remove_holding(self, holding_id: str) -> bool:
        return self.repository.remove_position(holding_id)

    def market_analysis(
        self,
        symbol: str,
        windows: list[int],
        price: str,
        refresh: bool = False,
    ) -> dict:
        price_column = "Adjusted Close" if price == "adjusted" else "Close"
        analysis = analyze_symbols(
            [symbol],
            windows,
            price_column,
            self.index_cache_path,
            self.analysis_cache_path,
            refresh,
        )
        rows = []
        for row in analysis.sort("Date").to_dicts():
            rows.append(
                {
                    "date": row["Date"].isoformat(),
                    "price": row[price_column],
                    "dailyChange": row["Daily Change"],
                    "dailyChangePercent": row["Daily Change %"],
                    "movingAverages": {
                        str(window): row[f"SMA {window}"] for window in windows
                    },
                    "rollingHighs": {
                        str(window): row[f"Rolling High {window}"]
                        for window in windows
                    },
                    "rollingLows": {
                        str(window): row[f"Rolling Low {window}"]
                        for window in windows
                    },
                    "volumeAverages": {
                        str(window): row[f"Volume SMA {window}"]
                        for window in windows
                    },
                    "relativeVolumes": {
                        str(window): row[f"Relative Volume {window}"]
                        for window in windows
                    },
                }
            )
        return {
            "symbol": symbol.upper(),
            "priceBasis": price,
            "windows": windows,
            "rows": rows,
        }

    @staticmethod
    def _holding_response(row: dict) -> dict:
        return {
            "id": row["ID"],
            "symbol": row["Symbol"],
            "shares": row["Quantity"],
            "costBasis": row["Average Cost"],
            "price": row["Last Price"],
            "marketValue": row["Market Value"],
            "costValue": row["Cost Basis"],
            "gain": row["Gain/Loss"],
            "gainPercent": row["Gain/Loss %"],
            "weightPercent": row["Weight %"],
            "asOf": row["As Of"].isoformat(),
            "account": row["Account"],
            "assetClass": row["Asset Class"],
            "sector": row["Sector"],
            "acquired": row["Acquired"],
        }

    @staticmethod
    def _allocation_response(analysis: pl.DataFrame, dimension: str) -> list[dict]:
        allocation = summarize_allocation(analysis, dimension)
        return [
            {
                "name": row[dimension],
                "cost": row["Cost Basis"],
                "value": row["Market Value"],
                "gain": row["Gain/Loss"],
                "weightPercent": row["Weight %"],
            }
            for row in allocation.to_dicts()
        ]
