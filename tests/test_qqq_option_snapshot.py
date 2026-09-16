import tempfile
import unittest
from pathlib import Path
import qqq_option_snapshot as snapshot


class SnapshotTests(unittest.TestCase):
    def row(self, **changes):
        row=dict(run_time='2026-09-15 15:00:00',expiration='2026-09-15',strike=705,
                 spot=705.5,bid=1.0,ask=1.2,iv=0.25,delta=0.5,gamma=0.1,theta=-0.2,volume=100)
        row.update(changes)
        return row

    def test_real_snapshot_units_and_missing_side(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'options.json'
            self.assertTrue(snapshot.write_option_snapshot([self.row()],[self.row(delta=-0.5)],path))
            data=json.loads(path.read_text()); contract=data['contracts'][0]
            self.assertEqual(contract['id'],'2026-09-15-call-705')
            self.assertEqual(contract['iv'],25)
            self.assertAlmostEqual(contract['mid'],1.1)
            self.assertEqual(data['generated_utc'],'2026-09-15T15:00:00+00:00')
            before=path.read_bytes()
            self.assertFalse(snapshot.write_option_snapshot([self.row()],[],path))
            self.assertEqual(path.read_bytes(),before)

    def test_invalid_quotes_and_greeks_stay_missing(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'options.json'
            snapshot.write_option_snapshot([self.row(bid=2,ask=1,delta='',gamma=float('nan'))],
                                           [self.row(bid=0)],path)
            contracts=json.loads(path.read_text())['contracts']
            self.assertIsNone(contracts[0]['mid'])
            self.assertIsNone(contracts[0]['delta'])
            self.assertIsNone(contracts[0]['gamma'])
            self.assertIsNone(contracts[1]['mid'])

if __name__=='__main__': unittest.main()
