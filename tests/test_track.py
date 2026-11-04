import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('track', ROOT / 'scripts/track.py')
track = importlib.util.module_from_spec(spec)
sys.modules['track'] = track
spec.loader.exec_module(track)

class Tests(unittest.TestCase):
    def test_discount_and_expiry(self):
        self.assertAlmostEqual(track.discount(6000, 5940, 30)['annualized'], .1216666667)
        self.assertIsNone(track.discount(6000, 5940, 0)['annualized'])
        self.assertLess(track.discount(6000, 6060, 30)['discount_points'], 0)
        for price in [0, -1, float('nan'), float('inf')]:
            with self.assertRaises(ValueError): track.discount(price, 5940, 30)

    def test_spread_and_unknown_cost(self):
        self.assertEqual(track.roll(6054.8, 5900, None)['net_points'], None)
        self.assertAlmostEqual(track.roll(6054.8, 5900, 40)['net_points'], 154.6)
        self.assertAlmostEqual(track.roll(6054.8, 5900, None)['gross_yuan_per_lot'], 30960)

    def test_freshness(self):
        now = datetime(2026, 11, 2, 10, 30, tzinfo=track.CST)
        self.assertTrue(track.fresh([now-timedelta(seconds=20), now], now, 180, 60))
        self.assertTrue(track.fresh([now+timedelta(seconds=5)], now, 180, 60))
        self.assertFalse(track.fresh([now-timedelta(days=1)], now, 180, 60))
        self.assertFalse(track.fresh([now-timedelta(seconds=90), now], now, 180, 60))
        self.assertFalse(track.fresh([now+timedelta(seconds=30)], now, 180, 60))

    def test_schedule(self):
        self.assertFalse(track.in_session(datetime(2026,10,31,10,30,tzinfo=track.CST)))
        self.assertFalse(track.in_session(datetime(2026,11,2,12,0,tzinfo=track.CST)))
        self.assertTrue(track.in_session(datetime(2026,11,2,14,0,tzinfo=track.CST)))
        self.assertTrue(track.slot_started(datetime(2026,11,2,11,0,tzinfo=track.CST),'10:30'))
        self.assertTrue(track.slot_started(datetime(2026,11,2,14,12,tzinfo=track.CST),'14:00'))
        self.assertFalse(track.slot_started(datetime(2026,11,2,13,59,tzinfo=track.CST),'14:00'))
        self.assertEqual(str(track.estimated_expiry('IC2709')), '2027-09-17')

    def test_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = track.connect(Path(tmp)/'history.sqlite')
            self.assertTrue(track.save(db,'2026-11-02','10:30',{'test':1}))
            self.assertFalse(track.save(db,'2026-11-02','10:30',{'test':2}))
            self.assertEqual(db.execute('select count(*) from snapshots').fetchone()[0],1)
            db.close()

    def test_quote_parser_missing_book_and_schema_change(self):
        index = ['0']*33
        index[0],index[3],index[30],index[31] = '中证500','6000','2026-11-02','10:30:00'
        future = ['0']*38
        for pos,value in {3:'5900',4:'200',6:'300',16:'5899',17:'2',26:'5901',27:'3',36:'2026-11-02',37:'10:30:00'}.items():
            future[pos] = value
        def body():
            return 'var hq_str_sh000905="'+','.join(index)+'";var hq_str_nf_IC2709="'+','.join(future)+'";'
        spot,quotes = track.parse_quotes(body(),['IC2709'])
        self.assertEqual(spot['price'],6000)
        self.assertEqual(quotes[0]['bid'],5899)
        future[26] = '0'
        _,quotes = track.parse_quotes(body(),['IC2709'])
        self.assertIsNone(quotes[0]['bid'])
        self.assertEqual(quotes[0]['price'],5900)
        with self.assertRaises(ValueError): track.parse_quotes('var broken="";',['IC2709'])

if __name__ == '__main__': unittest.main()
