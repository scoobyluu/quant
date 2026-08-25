import unittest
from unittest.mock import patch

from quant.dashboard import server


class DashboardApiTests(unittest.TestCase):
    def test_health(self) -> None:
        self.assertEqual(server.health(), {"ok": True})

    @patch("quant.dashboard.server._dashboard_service")
    def test_holdings_route_delegates_to_service(self, service) -> None:
        service.holdings.return_value = {"holdings": [], "totals": {}}

        result = server.holdings_list(refresh=True)

        self.assertEqual(result, {"holdings": [], "totals": {}})
        service.holdings.assert_called_once_with(True)

    @patch("quant.dashboard.server._dashboard_service")
    def test_analysis_route_requires_and_parses_explicit_windows(self, service) -> None:
        service.market_analysis.return_value = {"symbol": "AAPL", "rows": []}

        result = server.technical_analysis("AAPL", "20,50,20", "adjusted")

        self.assertEqual(result["symbol"], "AAPL")
        service.market_analysis.assert_called_once_with(
            "AAPL", [20, 50], "adjusted", False
        )

    @patch("quant.dashboard.server._research_service")
    def test_research_routes_delegate_to_provider(self, service) -> None:
        service.quote.return_value = {"symbol": "AAPL", "price": 125.0}
        service.history.return_value = {"symbol": "AAPL", "candles": []}

        quote = server.quote("aapl")
        history = server.history("AAPL", "1mo", "1d")

        self.assertEqual(quote["price"], 125.0)
        self.assertEqual(history["candles"], [])
        service.quote.assert_called_once_with("aapl")
        service.history.assert_called_once_with("AAPL", "1mo", "1d")


if __name__ == "__main__":
    unittest.main()
