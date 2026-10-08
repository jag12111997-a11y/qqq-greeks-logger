import datetime as dt
import unittest
from unittest.mock import patch

import gex_history as H


class FailingApi:
    calls = 0

    def stock_bars(self, day):
        raise RuntimeError("alpaca down")


class HistoryTests(unittest.TestCase):
    def test_alpaca_error_keeps_logged_minutes(self):
        logged = H.load_logged("2026-10-07")
        if not logged:
            self.skipTest("no saved data for 2026-10-07 in this checkout")
        doc, rep = H.build_day("2026-10-07", FailingApi())
        self.assertIn("alpaca_error", rep)
        self.assertGreater(len(doc["frames"]), 100)
        self.assertEqual(doc["sources"]["rebuilt"], 0)

    def test_today_never_rebuilds_past_what_history_has(self):
        logged = H.load_logged("2026-10-07")
        if not logged:
            self.skipTest("no saved data for 2026-10-07 in this checkout")

        class Bars:
            trades = {}

            def spot_at(self, m):
                return 750.0

            def state(self, kind, k, m):
                return (None, None, 0)

        class FakeNow(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return dt.datetime(2026, 10, 7, 11, 0, tzinfo=H.NY)       # 11:00 ET

        seen = []
        with patch.object(H, "fetch_day_bars", return_value=(Bars(), [])), \
             patch.object(H.dt, "datetime", FakeNow), \
             patch.object(H, "rebuild_minute", side_effect=lambda day, m, bars, oi: seen.append(m)):
            H.build_day("2026-10-07", object())
        self.assertTrue(seen)
        self.assertLessEqual(max(seen), 11 * 60 - 16)


if __name__ == "__main__":
    unittest.main()
