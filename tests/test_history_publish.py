import unittest
from unittest.mock import patch
from types import SimpleNamespace
import qqq_greeks_logger_alpaca as logger

class HistoryPublishTests(unittest.TestCase):
    def test_intraday_publish_includes_both_history_sides(self):
        calls = []
        def git(*args):
            calls.append(args)
            return SimpleNamespace(stdout='', stderr='', returncode=0)
        with patch.object(logger, 'CI_PUSH', True), patch.object(logger, '_git', git), \
             patch.object(logger, 'output_path', side_effect=lambda kind: 'data/session-'+kind+'.csv'), \
             patch.object(logger.os.path, 'exists', return_value=True):
            logger.push_live_snapshots()
        staged = next(args for args in calls if args[0] == 'add')
        self.assertIn('data/session-call.csv', staged)
        self.assertIn('data/session-put.csv', staged)
        self.assertIn('market-dash/options_latest.json', staged)
        self.assertIn(('push',), calls)

    def test_local_runs_do_not_publish_files(self):
        with patch.object(logger, 'CI_PUSH', False), patch.object(logger, '_git') as git:
            logger.push_live_snapshots()
            git.assert_not_called()
