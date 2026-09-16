import importlib.util
from pathlib import Path
import unittest
spec = importlib.util.spec_from_file_location('sync', Path(__file__).parents[1] / 'scripts/sync_local_history.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
HEADER = 'run_time,expiration,strike,spot,delta,gamma\n'
A = '2026-09-15 14:00:00,2026-09-15,700,705,-0.5,0.02\n'
B = '2026-09-15 14:01:00,2026-09-15,700,706,-0.4,0.02\n'
class SyncTests(unittest.TestCase):
    def test_union_preserves_remote_and_local_records(self):
        self.assertEqual(sync.merge_csv(HEADER+B, HEADER+A), HEADER+A+B)
    def test_idempotent(self):
        self.assertEqual(sync.merge_csv(HEADER+A+B, HEADER+A), HEADER+A+B)
    def test_existing_remote_quote_wins_conflict(self):
        self.assertEqual(sync.merge_csv(HEADER+A, HEADER+A.replace('705','999')), HEADER+A)
    def test_partial_append_is_not_published(self):
        self.assertEqual(sync.merge_csv(HEADER+A, HEADER+A+'2026-09-15 14:02:00,'), HEADER+A)
    def test_header_mismatch_fails(self):
        with self.assertRaises(ValueError):
            sync.merge_csv(HEADER+A, 'bad,csv\n')
