#!/usr/bin/env python3
"""Free IC quotes, timestamp validation, SQLite history and offline HTML charts."""
import argparse
import html
import json
import math
import re
import sqlite3
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))
LIST_URL = ('https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
            'Market_Center.getHQFuturesData?page=1&num=100&node=zzgz_qh&base=futures')
QUOTE_URL = 'https://hq.sinajs.cn/list='
COLORS = ['#2563eb', '#db2777', '#059669', '#d97706', '#7c3aed', '#475569']


def number(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('价格缺失、非有限数或不大于零')
    return value


def discount(spot, future, days):
    spot, future = number(spot), number(future)
    ratio = (spot - future) / spot
    return dict(discount_points=spot-future, discount_ratio=ratio,
                annualized=ratio*365/days if days > 0 else None)


def roll(sell_bid, buy_ask, cost):
    points = number(sell_bid) - number(buy_ask)
    if cost is not None and (not math.isfinite(float(cost)) or float(cost) < 0):
        raise ValueError('移仓总成本应为非负元/手，未知用 null')
    return dict(gross_points=points, gross_yuan_per_lot=points*200,
                net_points=None if cost is None else points-float(cost)/200)


def estimated_expiry(symbol):
    if not re.fullmatch(r'IC\d{4}', symbol):
        raise ValueError('需要实际 IC 合约代码')
    first = date(2000+int(symbol[2:4]), int(symbol[4:6]), 1)
    return first + timedelta(days=(4-first.weekday()) % 7 + 14)


def expiry(symbol, config):
    item = config['expiry_overrides'].get(symbol)
    if item:
        if not item.get('source'):
            raise ValueError('已核实到期日需要 source 来源链接')
        return date.fromisoformat(item['date']), '已核实'
    return estimated_expiry(symbol), '估算，未核实节假日调整'


def in_session(now):
    clock = now.strftime('%H:%M:%S')
    return now.weekday() < 5 and ('09:30:00' <= clock <= '11:30:00' or
                                 '13:00:00' <= clock <= '15:00:00')


def fresh(stamps, now, max_age, max_skew):
    if not stamps:
        return False
    return (all(0 <= (now-stamp).total_seconds() <= max_age for stamp in stamps)
            and (max(stamps)-min(stamps)).total_seconds() <= max_skew)


def fetch(url):
    request = urllib.request.Request(url, headers={
        'Referer': 'https://finance.sina.com.cn/', 'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode('gb18030')


def parse_quotes(body, symbols):
    values = dict(re.findall(r'var hq_str_(\w+)="([^"\r\n]*)";', body))
    fields = values.get('sh000905', '').split(',')
    if len(fields) < 32 or '中证500' not in fields[0]:
        raise ValueError('指数行情结构异常')
    spot = {'price': number(fields[3]), 'quote_time': fields[30]+'T'+fields[31]+'+08:00'}
    datetime.fromisoformat(spot['quote_time'])
    quotes = []
    for symbol in symbols:
        f = values.get('nf_'+symbol, '').split(',')
        if len(f) < 38:
            raise ValueError(symbol+' 行情缺失或字段结构改变')
        quote = dict(symbol=symbol, price=number(f[3]), bid=None, ask=None,
                     volume=float(f[4]), open_interest=float(f[6]),
                     quote_time=f[36]+'T'+f[37]+'+08:00')
        datetime.fromisoformat(quote['quote_time'])
        try:
            bid, ask = number(f[16]), number(f[26])
            if bid <= ask and float(f[17]) > 0 and float(f[27]) > 0:
                quote.update(bid=bid, ask=ask)
        except (ValueError, TypeError):
            pass  # Preserve valid last prices, but never invent executable quotes.
        quotes.append(quote)
    return spot, quotes


def get_snapshot(config, raw_dir):
    listing = fetch(LIST_URL)
    listed = json.loads(listing)
    symbols = sorted({x['symbol'] for x in listed if re.fullmatch(r'IC\d{4}', x.get('symbol',''))})
    if len(symbols) != 4:
        raise ValueError('实际合约数量不是4，需检查数据源或挂牌规则；不使用主连代替')
    body = fetch(QUOTE_URL+','.join(['nf_'+s for s in symbols]+['sh000905']))
    now = datetime.now(CST)
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / (now.strftime('%Y%m%d-%H%M%S-%f')+'.json')).write_text(
        json.dumps(dict(fetched_at=now.isoformat(), listing=listing, quotes=body,
                        listing_url=LIST_URL, quote_url=QUOTE_URL), ensure_ascii=False), encoding='utf-8')
    spot, quotes = parse_quotes(body, symbols)
    for q in quotes:
        end, status = expiry(q['symbol'], config)
        days = (end-date.fromisoformat(q['quote_time'][:10])).days
        q.update(expiry=end.isoformat(), expiry_status=status, days=days,
                 **discount(spot['price'], q['price'], days))
    pairs = []
    held = next((q for q in quotes if q['symbol'] == config['holding']), None)
    if held:
        for target in quotes:
            interval = (date.fromisoformat(target['expiry'])-date.fromisoformat(held['expiry'])).days
            if interval <= 0:
                continue
            pair = dict(pair=held['symbol']+' → '+target['symbol'], interval_days=interval,
                        gross_points=None, gross_yuan_per_lot=None, net_points=None,
                        marginal_annualized=None)
            if held['bid'] is not None and target['ask'] is not None:
                pair.update(roll(held['bid'], target['ask'], config['round_trip_cost_yuan_per_lot']))
                # Indicative curve slope, not guaranteed return or realized P&L.
                pair['marginal_annualized'] = pair['gross_points']/spot['price']*365/interval
            pairs.append(pair)
    stamps = [datetime.fromisoformat(q['quote_time']) for q in quotes+[spot]]
    quality = fresh(stamps, now, config['max_quote_age_seconds'], config['max_quote_skew_seconds'])
    return dict(fetched_at=now.isoformat(), source='新浪公开免费行情', spot=spot,
                quotes=quotes, pairs=pairs, holding=config['holding'], holding_found=bool(held),
                quality_ok=quality, cost_yuan=config['round_trip_cost_yuan_per_lot'])


def connect(path):
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE IF NOT EXISTS snapshots (day TEXT, slot TEXT, payload TEXT, PRIMARY KEY(day,slot))')
    return db


def save(db, day, slot, snapshot):
    with db:
        result = db.execute('INSERT OR IGNORE INTO snapshots VALUES (?,?,?)',
                            (day, slot, json.dumps(snapshot, ensure_ascii=False, allow_nan=False)))
    return result.rowcount == 1


def fmt(value, percent=False):
    return '—' if value is None else (f'{value*100:.2f}%' if percent else f'{value:,.2f}')


def chart(history, field, title, pairs=False, percent=False):
    series = {}
    for snap in history:
        t = datetime.fromisoformat(snap['fetched_at']).timestamp()
        for item in snap['pairs' if pairs else 'quotes']:
            value = item.get(field)
            if value is not None:
                key = item['pair' if pairs else 'symbol']
                series.setdefault(key, []).append((t, value*(100 if percent else 1)))
        if field == 'price':
            series.setdefault('中证500指数', []).append((t, snap['spot']['price']))
    points = [p for values in series.values() for p in values]
    if not points:
        return '<section><h2>'+title+'</h2><p>暂无有效样本；不会用模拟行情填充。</p></section>'
    xmin, xmax = min(p[0] for p in points), max(p[0] for p in points)
    ymin, ymax = min(p[1] for p in points), max(p[1] for p in points)
    pad = max((ymax-ymin)*.1, .1)
    ymin, ymax = ymin-pad, ymax+pad
    sx = lambda x: 75+(x-xmin)/max(xmax-xmin,1)*835
    sy = lambda y: 250-(y-ymin)/(ymax-ymin)*215
    svg = ['<svg viewBox="0 0 950 300" role="img" aria-label="'+title+'">']
    for i in range(5):
        y = ymin+(ymax-ymin)*i/4
        svg.append(f'<path d="M75 {sy(y):.1f} H910" stroke="#e2e8f0"/><text x="67" y="{sy(y)+4:.1f}" text-anchor="end">{y:.2f}</text>')
    legends = []
    for i, (key, values) in enumerate(sorted(series.items())):
        color = COLORS[i % len(COLORS)]
        coords = ' '.join(f'{sx(x):.1f},{sy(y):.1f}' for x,y in values)
        svg.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="2"/>')
        for x,y in values:
            label = html.escape(key+' '+datetime.fromtimestamp(x,CST).strftime('%m-%d %H:%M')+f' {y:.2f}')
            svg.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="3" fill="{color}"><title>{label}</title></circle>')
        legends.append(f'<span style="color:{color}">● {html.escape(key)}</span>')
    for x, anchor in [(xmin,'start'), (xmax,'end')]:
        svg.append(f'<text x="{sx(x):.1f}" y="278" text-anchor="{anchor}">{datetime.fromtimestamp(x,CST):%m-%d %H:%M}</text>')
    svg.append('</svg>')
    return '<section><h2>'+title+'</h2><p class="legend">'+' · '.join(legends)+'</p>'+''.join(svg)+'</section>'


def report(db, data_dir, status):
    history = [json.loads(row[0]) for row in db.execute('SELECT payload FROM snapshots ORDER BY day,slot')]
    last = history[-1] if history else None
    content = ['<h1>IC 移仓观察</h1><p>北京时间 10:30 / 14:00 · 免费行情 · 长期多头</p>',
               '<aside>本次运行：'+html.escape(status['message'])+'</aside>']
    if last:
        content += ['<p>最近有效采样：'+html.escape(last['fetched_at'])+'；当时持仓：'+last['holding']+'</p>',
                    '<p>历史样本 '+str(len(history))+' 个。以下为最近有效数据，不代表此刻可成交报价。</p>',
                    '<table><tr><th>合约</th><th>最新价</th><th>贴水点</th><th>贴水率</th><th>年化贴水</th><th>到期日 / 口径</th></tr>']
        for q in last['quotes']:
            content.append('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in [
                q['symbol'], fmt(q['price']), fmt(q['discount_points']), fmt(q['discount_ratio'],True),
                fmt(q['annualized'],True), q['expiry']+' / '+q['expiry_status']])+'</tr>')
        content.append('</table><h2>现持合约 → 更远月</h2><table><tr><th>合约对</th><th>扣费前价差 / 点</th><th>对应元/手</th><th>扣费后 / 点</th><th>区间年化价差*</th></tr>')
        for p in last['pairs']:
            content.append('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in [
                p['pair'],fmt(p['gross_points']),fmt(p['gross_yuan_per_lot']),fmt(p['net_points']),
                fmt(p['marginal_annualized'],True)])+'</tr>')
        content.append('</table><p>* 区间年化价差按指数名义金额及两到期日间隔折算，仅描述期限价差。成本未知时扣费后留空。正价差并非即时现金盈利。</p>')
        if not last['holding_found']:
            content.append('<aside>持仓合约未在挂牌列表中找到，请核对实际持仓；不会自动替换。</aside>')
    else:
        content.append('<p>等待交易时段的第一笔有效采样。休市、陈旧或不同步行情不进入曲线。</p>')
    for field,title,pairs,percent in [
        ('price','价格 / 指数点',False,False),
        ('discount_points','贴水 / 点（正数为贴水）',False,False),
        ('annualized','年化贴水 / %（未核实到期日时为估算）',False,True),
        ('gross_points','移仓扣费前价差 / 点',True,False)]:
        content.append(chart(history,field,title,pairs,percent))
    content.append('<footer>按真实合约分别画线；空档连线仅辅助观察，不代表中间时点有报价。数据尚未形成经回测验证的换月阈值。多头仍承担指数涨跌风险；贴水包含分红、资金与供求因素，不能视为无风险收益。</footer>')
    document = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>IC 移仓观察</title><style>
body{font:16px/1.6 system-ui,"Microsoft YaHei",sans-serif;color:#172033;background:#f4f7fb;max-width:1100px;margin:32px auto;padding:0 20px}h1{font-size:32px}h2{font-size:20px}section,table{background:white;border-radius:12px}section{padding:20px;margin:22px 0}table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:12px;text-align:right;border-bottom:1px solid #e2e8f0}th:first-child,td:first-child{text-align:left}aside{background:#fff4d9;padding:14px;border-radius:8px}svg{width:100%;font-size:12px;fill:#64748b}.legend,footer{font-size:13px}footer{margin:24px 0;color:#64748b}</style>'''+''.join(content)+'</html>'
    (data_dir/'report.html').write_text(document,encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['collect','probe','report'])
    parser.add_argument('--data-dir',type=Path,required=True)
    parser.add_argument('--slot',choices=['10:30','14:00'])
    args = parser.parse_args()
    folder = args.data_dir.resolve()
    folder.mkdir(parents=True,exist_ok=True)
    config_path = folder/'config.json'
    if not config_path.exists():
        template = Path(__file__).resolve().parents[1]/'config.json'
        config_path.write_text(template.read_text(encoding='utf-8'),encoding='utf-8')
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    if config['holding'] and not re.fullmatch(r'IC\d{4}',config['holding']):
        raise ValueError('配置 holding 需要实际 IC 合约代码；留空可只追踪所有合约')
    db = connect(folder/'history.sqlite')
    now = datetime.now(CST)
    status = dict(at=now.isoformat(), mode=args.mode, message='', recorded=False)
    code = 0
    try:
        if args.mode == 'report':
            old = folder/'status.json'
            status = json.loads(old.read_text(encoding='utf-8')) if old.exists() else status
            status['message'] = status['message'] or '尚未采集'
        elif args.mode == 'collect' and not args.slot:
            raise ValueError('collect 必须指定 --slot 10:30 或 14:00')
        elif args.mode == 'collect' and not in_session(now):
            status['message'] = '非交易时段或周末，跳过采集'
        else:
            if args.mode == 'collect':
                scheduled = datetime.fromisoformat(now.strftime('%Y-%m-%d')+'T'+args.slot+':00+08:00')
                delay = (now-scheduled).total_seconds()
                if not 0 <= delay <= config['max_schedule_lateness_seconds']:
                    raise ValueError('已错过该时点，不能用现在行情回填；仅可单独 probe 查看')
            snap = get_snapshot(config,folder/'raw')
            (folder/'last_fetch.json').write_text(json.dumps(snap,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
            if args.mode == 'probe':
                status['message'] = '接口联通检查成功；未写入正式历史。报价日期：'+snap['quotes'][0]['quote_time']
            elif not snap['quality_ok']:
                status['message'] = '报价过期、跨日或不同步；休市或数据源异常，未入库，也不生成移仓判断'
                code = 2
            else:
                status['recorded'] = save(db,now.date().isoformat(),args.slot,snap)
                status['message'] = '已记录有效采样' if status['recorded'] else '该交易日该时点已记录，本次不重复入库'
                if not snap['holding_found']:
                    status['message'] += '；当前持仓不在挂牌列表，请核对'
    except Exception as exc:
        status['message'] = '采集失败，未以旧行情补齐：'+str(exc)
        code = 1
    finally:
        (folder/'status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
        with (folder/'runs.jsonl').open('a',encoding='utf-8') as out:
            out.write(json.dumps(status,ensure_ascii=False)+'\n')
        report(db,folder,status)
        db.close()
    print(json.dumps(status,ensure_ascii=True))
    if args.mode == 'collect' and (folder/'kdocs.json').exists():
        import subprocess
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('sync_kdocs.py')),
                                 'sync', '--data-dir', str(folder)])
        if result.returncode and code == 0:
            code = 3  # Cloud failure never rolls back a valid local snapshot.
    return code


if __name__ == '__main__':
    sys.exit(main())
