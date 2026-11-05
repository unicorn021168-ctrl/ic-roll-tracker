#!/usr/bin/env python3
"""Synchronize validated IC history to Kdocs using its authenticated CLI."""
import argparse
import json
import math
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

TITLE = 'IC贴水数据监测'
CST = timezone(timedelta(hours=8))
QUOTE_HEADERS = ['记录键','交易日','计划时点','实际采样时间','合约','最新价','中证500指数','买一','卖一',
                 '到期日','剩余自然日','贴水点数','贴水率(%)','年化贴水率(%)','期货报价时间','指数报价时间',
                 '到期日口径','当时持仓','成交量','持仓量','来源']
ROLL_HEADERS = ['记录键','交易日','计划时点','实际采样时间','移仓合约对','扣费前价差(点)',
                '对应金额(元/手)','扣费后价差(点)','区间年化价差(%)','到期间隔(天)','两腿成本(元/手)']
CURVES = [('价格曲线',5,'价格（指数点）'),('贴水曲线',11,'贴水（点）'),
          ('年化贴水曲线',13,'年化贴水（%，到期未核实时为估算）'),
          ('移仓价差曲线',5,'移仓扣费前价差（点）')]


def unwrap(value):
    while isinstance(value,dict):
        if value.get('code',0) not in (0,'0',None):
            raise RuntimeError(f"Kdocs {value.get('code')}: {value.get('msg',value.get('message','调用失败'))}")
        if 'data' not in value:
            break
        value = value['data']
    return value


def call(service, action, payload):
    binary = shutil.which('kdocs-cli')
    if not binary:
        raise RuntimeError('未找到 kdocs-cli，请安装官方 kdocs 技能并使用 auth login 登录')
    time.sleep(1.2)  # Serial, modest request rate; never retry a rejected request.
    proc = subprocess.run([binary,service,action,'-','--compact'],
                          input=json.dumps(payload,ensure_ascii=False),encoding='utf-8',
                          capture_output=True,timeout=90)
    if proc.returncode:
        # No retries: permission/rate limits must be handled by the calling agent.
        try:
            unwrap(json.loads(proc.stdout))
        except json.JSONDecodeError:
            detail=proc.stderr.strip()[:1000]
            raise RuntimeError(f'kdocs-cli {action} 失败（未自动重试）：{detail}') from None
        raise RuntimeError('kdocs-cli 调用失败，未自动重试')
    return unwrap(json.loads(proc.stdout))


def same(a,b):
    if a is None: a=''
    if b is None: b=''
    if str(a)==str(b): return True
    try:
        return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=1e-9)
    except (ValueError,TypeError): return False


def merge_rows(existing, new_rows, headers):
    if existing and existing[0] != headers:
        raise ValueError('在线表头与技能定义不一致；停止写入以免覆盖手工内容')
    result = [headers] + [row[:] for row in existing[1:] if row and row[0]] if existing else [headers]
    index = {row[0]:row for row in result[1:]}
    if len(index) != len(result)-1:
        raise ValueError('在线数据有重复记录键，需先核对')
    for row in new_rows:
        if row[0] in index:
            prior=index[row[0]]
            if len(prior)!=len(row) or not all(a in ('',None) or same(a,b) for a,b in zip(prior,row)):
                raise ValueError('同一记录键的数据冲突；保留云端原值，请核对重复调度或配置：'+row[0])
            for col,value in enumerate(row):
                if prior[col] in ('',None): prior[col]=value
        else:
            result.append(row)
            index[row[0]]=row
    return result


def rows_from_snapshots(snapshots):
    quotes,rolls=[],[]
    pct=lambda value: '' if value is None else value*100
    blank=lambda value: '' if value is None else value
    for day,slot,s in snapshots:
        if not s.get('quality_ok'):
            raise ValueError('正式历史包含质量检查不通过的样本，停止同步')
        for q in s['quotes']:
            quotes.append([day+'|'+slot+'|'+q['symbol'],day,slot,s['fetched_at'],q['symbol'],q['price'],
                           s['spot']['price'],blank(q['bid']),blank(q['ask']),q['expiry'],q['days'],
                           q['discount_points'],pct(q['discount_ratio']),pct(q['annualized']),q['quote_time'],
                           s['spot']['quote_time'],q['expiry_status'],s['holding'],q['volume'],q['open_interest'],s['source']])
        for p in s['pairs']:
            rolls.append([day+'|'+slot+'|'+p['pair'],day,slot,s['fetched_at'],p['pair'],
                          blank(p['gross_points']),blank(p['gross_yuan_per_lot']),blank(p['net_points']),
                          pct(p['marginal_annualized']),p['interval_days'],blank(s.get('cost_yuan'))])
    return quotes,rolls


def curve(rows, label_col, value_col):
    labels=sorted({r[label_col] for r in rows[1:]})
    by_time={}
    for r in rows[1:]:
        key=r[1]+' '+r[2]
        by_time.setdefault(key,{})[r[label_col]]=r[value_col]
    return [['采样时点（北京时间）']+labels]+[
        [time]+[by_time[time].get(label,'') for label in labels] for time in sorted(by_time)]


def col_name(index):
    result=''
    while index>=0:
        index,rem=divmod(index,26)
        result=chr(65+rem)+result
        index-=1
    return result


def chart_items(response):
    """Accept both documented and actual CLI chart-response envelopes."""
    body = response.get('detail', response)
    body = body.get('result', body)
    return body.get('charts', [])


def chart_matches(chart, matrix):
    """Verify plotted values, not just titles (the live API omits sourceAddress)."""
    series = chart.get('seriesDetails', [])
    if len(series) != len(matrix[0])-1:
        return False
    times = [row[0] for row in matrix[1:]]
    for col, item in enumerate(series, 1):
        values = item.get('values', [])
        if item.get('name') != matrix[0][col] or item.get('xValues') != times:
            return False
        expected = [row[col] for row in matrix[1:]]
        if len(values) != len(expected) or not all(same(a,b) for a,b in zip(values,expected)):
            return False
    return True


class Book:
    def __init__(self,file_id):
        self.file_id=file_id
        self.refresh()

    def api(self,action,**params):
        return call('sheet',action,dict(file_id=self.file_id,**params))

    def refresh(self):
        info=self.api('get-sheets-info')
        self.sheets={s['sheetName']:s for s in info.get('detail',info)['sheetsInfo']}

    def ensure(self,name):
        if name not in self.sheets:
            self.api('add-sheet',name=name,end=True,count=1)
            self.refresh()  # IDs are always read from the API, never assumed.
        return self.sheets[name]

    def read(self,sheet,width):
        end=sheet.get('rowTo',0)
        if sheet.get('isEmpty'): return []
        result=[]
        for start in range(0,end+1,250):
            last=min(end,start+249)
            response=self.api('get-range-data',worksheet_id=sheet['sheetId'],
                              range={'rowFrom':start,'rowTo':last,'colFrom':0,'colTo':width-1})
            cells=response.get('detail',{}).get('rangeData',[])
            block=[['']*width for _ in range(last-start+1)]
            for cell in cells:
                r,c=cell['rowFrom'],cell['colFrom']
                if start<=r<=last and 0<=c<width:
                    block[r-start][c]=cell.get('originalCellValue',cell.get('cellText',''))
            result.extend(block)
        while result and not any(v not in ('',None) for v in result[-1]): result.pop()
        return result

    def write(self,name,matrix,existing=None):
        sheet=self.ensure(name)
        width=max(len(row) for row in matrix)
        if existing is None: existing=self.read(sheet,width)
        changes=[]
        for r,row in enumerate(matrix):
            for c,value in enumerate(row):
                before=existing[r][c] if r<len(existing) and c<len(existing[r]) else ''
                if same(before,value): continue
                literal=str(value) if isinstance(value,(int,float)) else "'"+str(value)
                changes.append(dict(op_type='cell_operation_type_formula',row_from=r,row_to=r,
                                    col_from=c,col_to=c,formula=literal))
        for start in range(0,len(changes),300):
            chunk=changes[start:start+300]
            self.api('range-data-batch-update',worksheet_id=sheet['sheetId'],range_data=chunk)
            rmin,rmax=min(x['row_from'] for x in chunk),max(x['row_to'] for x in chunk)
            cmin,cmax=min(x['col_from'] for x in chunk),max(x['col_to'] for x in chunk)
            got=self.api('get-range-data',worksheet_id=sheet['sheetId'],
                         range={'rowFrom':rmin,'rowTo':rmax,'colFrom':cmin,'colTo':cmax})
            values={(x['rowFrom'],x['colFrom']):x.get('originalCellValue',x.get('cellText',''))
                    for x in got.get('detail',{}).get('rangeData',[])}
            for change in chunk:
                pos=(change['row_from'],change['col_from'])
                if not same(values.get(pos,''),matrix[pos[0]][pos[1]]):
                    raise RuntimeError('云端回读验证失败：'+name+' '+str(pos))
        print(json.dumps({'sheet':name,'changed_cells':len(changes)},ensure_ascii=True),flush=True)
        return sheet

    def chart(self,sheet,matrix,title):
        if len(matrix)<2 or len(matrix[0])<2: return
        # Unqualified addresses can resolve against the active worksheet.
        source="'"+sheet['sheetName'].replace("'", "''")+"'!A1:"+col_name(len(matrix[0])-1)+str(len(matrix))
        response=self.api('get-chart-information',worksheet_id=sheet['sheetId'])
        charts=chart_items(response)
        match=next((c for c in charts if c.get('title',c.get('chartTitle'))==title),None)
        if match:
            if match.get('sourceAddress')!=source:
                self.api('update-chart',worksheet_id=sheet['sheetId'],index=match['index'],
                         updateType='data_source',sourceAddress=source,plotBy='columns')
        else:
            # To the right of data; do not cover the first columns.
            start=max(8,len(matrix[0])+1)
            self.api('add-chart',worksheet_id=sheet['sheetId'],chartType='line_markers',
                     sourceAddress=source,rectAddress=col_name(start)+'2:'+col_name(start+10)+'22',
                     title=title,plotBy='columns')
        response=self.api('get-chart-information',worksheet_id=sheet['sheetId'])
        check=chart_items(response)
        verified=next((c for c in check if c.get('title',c.get('chartTitle'))==title),None)
        if verified is None or not chart_matches(verified,matrix):
            raise RuntimeError('图表数据系列回读验证失败：'+title)


def load_history(folder):
    path=folder/'history.sqlite'
    if not path.exists(): return []
    db=sqlite3.connect(path)
    try: return [(day,slot,json.loads(payload)) for day,slot,payload in db.execute('SELECT day,slot,payload FROM snapshots ORDER BY day,slot')]
    finally: db.close()


def init_binding(folder,file_id=None):
    path=folder/'kdocs.json'
    if path.exists(): return json.loads(path.read_text(encoding='utf-8'))
    if file_id:
        raise ValueError('绑定已有文档需先确认其结构与权限，再将 file_id、link_url 写入本地 kdocs.json')
    found=call('drive','search-files',dict(keyword=TITLE,type='file_name',page_size=100))
    if any(item.get('file',{}).get('name','').rsplit('.',1)[0]==TITLE for item in found.get('items',[])):
        raise ValueError('已存在同名文档。请核对后绑定到本地 kdocs.json；不会重复创建')
    created=call('drive','create-file-with-content',dict(name=TITLE,file_extension='xlsx',sheet_name='监测总览',
                 rangeData=[dict(row_from=0,row_to=0,col_from=0,col_to=1,formula=[['项目','说明']])]))
    identifier=created.get('file_id') or created.get('id')
    if not identifier:
        raise RuntimeError('创建结果未返回文件ID；先查找创建结果，禁止直接重试新建')
    binding=dict(file_id=str(identifier),link_url=created.get('link_url',''),enabled=True)
    # Persist before subsequent writes so a partial failure cannot duplicate the file.
    path.write_text(json.dumps(binding,ensure_ascii=False,indent=2),encoding='utf-8')
    return binding


def run(folder,initialize=False):
    path=folder/'kdocs.json'
    if initialize: binding=init_binding(folder)
    elif path.exists(): binding=json.loads(path.read_text(encoding='utf-8'))
    else: raise ValueError('尚未初始化金山文档，先运行 init')
    if not binding.get('enabled',True): return {'status':'disabled'}
    book=Book(binding['file_id'])
    config=json.loads((folder/'config.json').read_text(encoding='utf-8-sig'))
    local_quotes,local_rolls=rows_from_snapshots(load_history(folder))
    merged=[]
    # Preserve remote history even when another agent/machine has an empty local database.
    for name,headers,local in [('合约数据',QUOTE_HEADERS,local_quotes),('移仓数据',ROLL_HEADERS,local_rolls)]:
        sheet=book.ensure(name)
        remote=book.read(sheet,len(headers))
        matrix=merge_rows(remote,local,headers)
        book.write(name,matrix,remote)
        merged.append(matrix)
    quotes,rolls=merged
    samples=len({r[1]+' '+r[2] for r in quotes[1:]})
    status_path=folder/'status.json'
    status=json.loads(status_path.read_text(encoding='utf-8')) if status_path.exists() else {}
    overview=[['项目','说明'],['当前持仓',config.get('holding') or '未配置'],['采样计划','北京时间每交易日10:30、14:00'],
              ['正式采样次数',samples],['本地最近运行',status.get('message','尚未采样')],
              ['云端同步时间',datetime.now(CST).isoformat(timespec='seconds')],
              ['数据状态','等待第一笔有效样本；不把休市旧报价写入曲线' if not samples else '仅同步通过质量检查的正式历史'],
              ['计算口径','正数为贴水；年化按365/剩余自然日；%列中10代表10%'],
              ['移仓口径','现持买一减远月卖一；两腿成本未知时扣费后留空'],
              ['到期口径','未核实到期日使用第三个周五估算，未包含节假日调整'],
              ['使用边界','暂无已回测的最优阈值；多头仍承担指数风险；不自动交易'],
              ['维护方式','各表为技能管理区域，请将个人备注放在另建工作表；同时只启用一个采集调度器']]
    probe=folder/'last_fetch.json'
    if probe.exists():
        last=json.loads(probe.read_text(encoding='utf-8'))
        overview += [['参考报价（非正式监测）','以下仅为最后一次接口返回；可能过期或不同步，不纳入曲线'],
                     ['代码','最新价','源报价时间']]
        overview += [[q['symbol'],q['price'],q['quote_time']] for q in last['quotes']]
        overview += [['中证500指数',last['spot']['price'],last['spot']['quote_time']]]
    book.write('监测总览',overview)
    for name,idx,title in CURVES:
        matrix=curve(rolls if name=='移仓价差曲线' else quotes,4,idx)
        if len(matrix[0])==1: matrix[0].append('等待有效历史')
        sheet=book.write(name,matrix)
        book.chart(sheet,matrix,title)
    return dict(status='verified',file_id=binding['file_id'],link_url=binding.get('link_url',''),
                quote_rows=len(quotes)-1,roll_rows=len(rolls)-1,samples=samples)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['init','sync'])
    parser.add_argument('--data-dir',required=True,type=Path)
    args=parser.parse_args()
    folder=args.data_dir.resolve()
    if not (folder/'config.json').exists():
        raise SystemExit('请先运行 track.py probe 初始化数据目录和持仓配置')
    try:
        result=run(folder,initialize=args.mode=='init')
        code=0
    except Exception as exc:
        result=dict(status='failed',error=str(exc))
        code=1
    result['at']=datetime.now(CST).isoformat()
    (folder/'kdocs-status.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True),flush=True)
    return code


if __name__=='__main__': sys.exit(main())
