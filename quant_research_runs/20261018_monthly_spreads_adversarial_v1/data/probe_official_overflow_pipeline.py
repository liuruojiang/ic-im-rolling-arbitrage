"""Offline actual official parser -> router -> diagnostics -> saved renderer probe."""
from pathlib import Path
import sys
import json
from datetime import date, datetime
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import poe_ic_im_mainline_v1_4_bot as bot
import ic_im_quarter_roll_v1_3 as q
import poe_ic_im_v1_4_state as state
import run_ic_im_v1_4_github_digest as digest

day = date(2026,10,15)
clock = datetime(2026,10,15,16,tzinfo=bot.BEIJING)
body = ('instrument,lastprice,volume,position\n'
        'IM2610,1.0,100,100\nIM2611,1e308,100,100\n'
        'IM2612,6000,100,100\nIM2703,5900,100,100\n').encode()
fake = SimpleNamespace(content=body, headers={'Last-Modified':'Thu, 15 Oct 2026 07:15:00 GMT'},
                       encoding='utf-8',raise_for_status=lambda:None)
with patch.object(bot.requests,'get',return_value=fake), \
     patch.object(bot,'_fetch_sina_future_quotes',side_effect=bot.requests.ConnectionError('offline probe')), \
     patch.object(bot,'_fetch_eastmoney_future_quotes',side_effect=bot.requests.ConnectionError('offline probe')):
    f=bot.fetch_cffex_quotes('IM',clock)
values=q.monthly_spreads('IM',f,day,lambda c:bot._third_friday(*bot._contract_month(c)))
saved=state._jsonable(values)
result=dict(prices=f[['instrument','lastprice']].to_dict('records'), attrs=state._jsonable(f.attrs),
            values=values,saved=saved)
try:
    report=digest.render_stored_close_report(dict(verified_day=str(day),sequence=0,digest='audit',
        signals={'IC':{},'IM':{'market_date':str(day),**saved}}))
    assert saved['month_next_spread']['status']=='unavailable'
    assert saved['month_quarter_spread']['status']=='ok'
    assert saved['month_quarter_spread']['points']==-5999.0
    assert all(marker in report for marker in ['当月－次月','当月－当季','N/A','IM2610','IM2612','-5999.00'])
    result['stored_report']=report
    result['renderer']='PASS'
except Exception as e:
    result['renderer_exception']=type(e).__name__+': '+str(e)
target=Path(__file__).with_name(sys.argv[1] if len(sys.argv)>1 else 'official_overflow_pipeline.json')
target.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
