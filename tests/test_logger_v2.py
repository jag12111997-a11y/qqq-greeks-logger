import datetime
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import qqq_greeks_logger_alpaca as logger


def snap(bid, ask, trade_p, trade_t, bar_v, bar_t):
    return {"latestQuote": {"bp": bid, "ap": ask, "t": trade_t},
            "latestTrade": {"p": trade_p, "t": trade_t},
            "dailyBar": {"v": bar_v, "t": bar_t}}


class StaleDataTests(unittest.TestCase):
    """Alpaca keeps yesterday's bar and trade until a contract trades today."""

    def rows(self, snapshots, now):
        class FakeDT(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return now if tz else now.replace(tzinfo=None)
        with patch.object(logger, "get_option_chain", return_value=snapshots), \
             patch.object(logger, "get_open_interest", return_value={}), \
             patch.object(logger.datetime, "datetime", FakeDT):
            return {r["strike"]: r for r in logger.build_snapshot_rows("call", spot=750.0)}

    def test_only_todays_volume_and_trade_count(self):
        now = datetime.datetime(2026, 10, 8, 14, 0, tzinfo=datetime.timezone.utc)   # 10:00 ET
        rows = self.rows({
            "QQQ261008C00751000": snap(1.0, 1.2, 1.1, "2026-10-08T13:59:00Z", 10, "2026-10-08T04:00:00Z"),
            "QQQ261008C00752000": snap(0.0, 0.0, 5.8, "2026-10-07T19:59:00Z", 7013, "2026-10-07T04:00:00Z"),
        }, now)
        self.assertEqual(rows[751.0]["volume"], 10)
        self.assertEqual(rows[752.0]["volume"], 0)          # yesterday's 7,013 is not today's
        self.assertEqual(rows[752.0]["last"], None)         # yesterday's trade is not a price today
        self.assertEqual(rows[752.0]["iv"], "")             # so no IV from it; the smile fills it

    def test_new_york_date_after_5pm_pacific(self):
        seen = {}
        def chain(low, high, today, opt_type="call"):
            seen["day"] = today
            return {}
        now = datetime.datetime(2026, 10, 8, 1, 30, tzinfo=datetime.timezone.utc)   # Oct 7, 9:30 PM ET
        class FakeDT(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return now
        with patch.object(logger, "get_option_chain", side_effect=chain), \
             patch.object(logger.datetime, "datetime", FakeDT):
            logger.build_snapshot_rows("call", spot=750.0)
        self.assertEqual(seen["day"], "2026-10-07")


class WallStateTests(unittest.TestCase):
    def gx(self, stamp, call_gex):
        return {"generated_utc": stamp, "levels": {"call_wall": max(call_gex, key=call_gex.get), "put_wall": 740.0},
                "strikes": [{"strike": k, "call_gex": v, "net_gex": v if k != 740.0 else -100.0}
                            for k, v in call_gex.items()]}

    def test_count_continues_across_separate_runs(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(logger, "GEX_LIVE_PATH", os.path.join(tmp, "g.json")), \
             patch.object(logger, "GEX_INTRADAY_PATH", os.path.join(tmp, "i.json")):
            stamps = ["2026-10-08 14:0%d:00" % i for i in range(4)]
            # run 1 establishes 755; runs 2-4 are separate processes where 762 beats it by >10%
            for i, stamp in enumerate(stamps):
                logger._WALLS.update({"date": None})            # a new process each time
                vals = {740.0: 1.0, 755.0: 100.0, 762.0: 50.0} if i == 0 else {740.0: 1.0, 755.0: 100.0, 762.0: 120.0}
                gx = logger.stabilize_walls(self.gx(stamp, vals))
                with open(logger.GEX_LIVE_PATH, "w") as f:
                    json.dump(gx, f)
                expect = 755.0 if i < 3 else 762.0
                self.assertEqual(gx["levels"]["call_wall"], expect, f"run {i + 1}")


class SingleSnapshotPublishTests(unittest.TestCase):
    def test_refresh_run_publishes_through_the_safe_publisher(self):
        with patch.object(logger, "API_KEY", "k"), patch.object(logger, "API_SECRET", "s"), \
             patch.object(logger, "CI_PUSH", True), patch.object(logger, "INTERVAL_SECONDS", 0), \
             patch.object(logger, "snapshot_and_write"), patch.object(logger, "get_spot_price", return_value=1.0), \
             patch.object(logger, "push_live_snapshots", return_value=True) as push, \
             patch("qqq_candle_history.update_history", side_effect=RuntimeError("skip")):
            logger.main()
        push.assert_called_once()


if __name__ == "__main__":
    unittest.main()
