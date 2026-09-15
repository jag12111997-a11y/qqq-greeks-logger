import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import qqq_candle_history as history


class HistoryTests(unittest.TestCase):
    def row(self, stamp='2026-09-14T13:30:00Z'):
        return dict(t=stamp, o=100, h=103, l=99, c=102, v=50)

    def test_regular_session_filter_and_validation(self):
        rows = [self.row(), self.row('2026-09-14T13:29:00Z'),
                self.row('2026-09-14T20:00:00Z'), self.row('2026-09-13T13:30:00Z'),
                dict(self.row(), t='2026-09-14T13:31:00Z', h=90)]
        candles = history.normalize_bars(rows, intraday=True)
        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0]['high'], 103)
        self.assertEqual(candles[0]['volume'], 50)

    @patch.object(history.requests, 'get')
    def test_pagination(self, get):
        responses = [dict(bars=[self.row()], next_page_token='next'),
                     dict(bars=[self.row('2026-09-14T13:31:00Z')])]
        get.side_effect = [Mock(json=Mock(return_value=value)) for value in responses]
        now = dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc)
        self.assertEqual(len(history.fetch_bars({}, '1Min', now, now)), 2)
        self.assertEqual(get.call_args.kwargs['params']['page_token'], 'next')
        self.assertEqual(get.call_args.kwargs['params']['feed'], 'iex')

    @patch.object(history, 'fetch_bars')
    def test_atomic_write_and_last_good_history(self, fetch):
        fetch.return_value = [self.row()]
        now = dt.datetime(2026, 9, 15, 15, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'history.json'
            data = history.update_history({}, path, now)
            self.assertEqual(len(data['daily']), 1)
            self.assertEqual(len(data['minute']), 1)
            original = path.read_bytes()
            end = fetch.call_args_list[0].args[3]
            self.assertEqual(end.astimezone(history.NY).date().isoformat(), '2026-09-14')
            fetch.return_value = []
            with self.assertRaises(RuntimeError):
                history.update_history({}, path, now)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(json.loads(original)['feed'], 'iex')


if __name__ == '__main__':
    unittest.main()
