import unittest
from unittest.mock import Mock

from quant.dashboard.research import YahooResearchService, clean_json


class YahooResearchServiceTests(unittest.TestCase):
    def test_calculates_and_caches_quote(self) -> None:
        service = YahooResearchService()
        ticker = Mock()
        ticker.info = {
            "shortName": "Apple",
            "regularMarketPrice": 125.0,
            "regularMarketPreviousClose": 100.0,
            "currency": "USD",
        }
        service._ticker = Mock(return_value=ticker)

        first = service.quote("aapl")
        second = service.quote("AAPL")

        self.assertEqual(first["change"], 25.0)
        self.assertEqual(first["changePercent"], 25.0)
        self.assertEqual(second, first)
        service._ticker.assert_called_once_with("AAPL")

    def test_serves_stale_cache_when_refresh_fails(self) -> None:
        service = YahooResearchService(cache_ttl_seconds=0, stale_ttl_seconds=60)
        ticker = Mock()
        ticker.info = {
            "regularMarketPrice": 125.0,
            "regularMarketPreviousClose": 100.0,
        }
        service._ticker = Mock(return_value=ticker)
        first = service.quote("AAPL")
        service._ticker.side_effect = RuntimeError("rate limited")

        second = service.quote("AAPL")

        self.assertEqual(second, first)

    def test_clean_json_removes_non_finite_values(self) -> None:
        self.assertEqual(
            clean_json({"nan": float("nan"), "infinity": float("inf"), "ok": 1.0}),
            {"nan": None, "infinity": None, "ok": 1.0},
        )


if __name__ == "__main__":
    unittest.main()
