import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import sync_kdocs as sync


class Tests(unittest.TestCase):
    def test_decode_nested_response_and_error(self):
        self.assertEqual(sync.unwrap({'code':0,'data':{'code':0,'data':{'x':1}}}),{'x':1})
        with self.assertRaises(RuntimeError): sync.unwrap({'code':0,'data':{'code':403,'msg':'forbidden'}})

    def test_keys_and_no_probe(self):
        q=dict(symbol='IC9912',price=5900,bid=5899,ask=5901,expiry='2099-12-18',expiry_status='估算',
               days=30,quote_time='2099-11-18T10:30:00+08:00',discount_points=100,
               discount_ratio=1/60,annualized=365/1800,volume=100,open_interest=200)
        snap=dict(fetched_at='2099-11-18T10:30:20+08:00',quality_ok=True,spot={'price':6000,'quote_time':q['quote_time']},
                  quotes=[q],pairs=[],holding='',source='test')
        rows,pairs=sync.rows_from_snapshots([('2099-11-18','10:30',snap)])
        self.assertEqual(rows[0][0],'2099-11-18|10:30|IC9912')
        self.assertAlmostEqual(rows[0][12],100/6000*100)
        snap['quality_ok']=False
        with self.assertRaises(ValueError): sync.rows_from_snapshots([('2099-11-18','10:30',snap)])

    def test_merge_keeps_remote_and_deduplicates(self):
        old=[['key','v'],['a','1']]
        result=sync.merge_rows(old,[['a','1'],['b','2']],['key','v'])
        self.assertEqual(result,[['key','v'],['a','1'],['b','2']])
        self.assertEqual(sync.merge_rows(old,[],['key','v']),old)
        with self.assertRaises(ValueError): sync.merge_rows(old,[['a','3']],['key','v'])

    def test_interrupted_row_can_finish_without_overwriting(self):
        self.assertEqual(sync.merge_rows([['key','v'],['a','']],[['a',3]],['key','v']),
                         [['key','v'],['a',3]])

    def test_chart_keeps_actual_contracts(self):
        rows=[['key','date','slot','time','contract','price'],
              ['a','2099-01-01','10:30','t','IC9903',100],
              ['b','2099-01-02','10:30','t','IC9906',200]]
        result=sync.curve(rows,4,5)
        self.assertEqual(result[0],['采样时点（北京时间）','IC9903','IC9906'])
        self.assertEqual(result[1][1:],[100,''])
        self.assertEqual(result[2][1:],['',200])

    def test_chart_converts_cloud_numeric_text(self):
        rows=[['key','date','slot','time','contract','discount'],
              ['a','2099-01-01','10:30','t','IC9903','142.5']]
        self.assertEqual(sync.curve(rows,4,5)[1][1],142.5)

    def test_chart_listing_accepts_live_cli_shape(self):
        response={'result':{'charts':[{'index':1,'chartTitle':'价格（指数点）'}]}}
        self.assertEqual(sync.chart_items(response)[0]['index'],1)

    def test_chart_rejects_prices_in_discount_series(self):
        matrix=[['time','IC2703'],['2026-11-05 14:00',142.0]]
        chart={'seriesDetails':[{'name':'IC2703','xValues':['2026-11-05 14:00'],'values':[6450.8]}]}
        self.assertFalse(sync.chart_matches(chart,matrix))
        chart['seriesDetails'][0]['values']=[142.0]
        self.assertTrue(sync.chart_matches(chart,matrix))
        chart['seriesDetails'][0]['xValues']=['wrong time']
        self.assertFalse(sync.chart_matches(chart,matrix))

    def test_chart_source_is_qualified_for_existing_and_new_charts(self):
        matrix=[['time','IC2703'],['t',142.0]]
        verified={'index':1,'chartTitle':'discount','seriesDetails':[
            {'name':'IC2703','xValues':['t'],'values':[142.0]}]}
        for initial in ([],[{'index':1,'chartTitle':'discount'}]):
            calls=[]
            responses=iter([{'charts':initial},{'charts':[verified]}])
            book=object.__new__(sync.Book)
            def api(action,**params):
                calls.append((action,params))
                return next(responses) if action=='get-chart-information' else {}
            book.api=api
            book.chart({'sheetId':6,'sheetName':'Discount'},matrix,'discount')
            writes=[p for a,p in calls if a in ('update-chart','add-chart')]
            self.assertEqual(writes[0]['sourceAddress'],"'Discount'!A1:B2")

    def test_chart_updates_each_stale_duplicate(self):
        matrix=[['time','IC2703'],['t',142.0]]
        stale=[{'index':1,'chartTitle':'discount'},{'index':2,'chartTitle':'discount'}]
        good=[dict(c,seriesDetails=[{'name':'IC2703','xValues':['t'],'values':[142.0]}]) for c in stale]
        responses=iter([{'charts':stale},{'charts':good}])
        calls=[]
        book=object.__new__(sync.Book)
        def api(action,**params):
            calls.append((action,params))
            return next(responses) if action=='get-chart-information' else {}
        book.api=api
        book.chart({'sheetId':6,'sheetName':'Discount'},matrix,'discount')
        self.assertEqual([p['index'] for a,p in calls if a=='update-chart'],[1,2])

if __name__=='__main__': unittest.main()
