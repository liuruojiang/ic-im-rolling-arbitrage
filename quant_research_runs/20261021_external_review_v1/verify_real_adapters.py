"""Verify saved exchange inputs through the changed live adapters, no state IO."""
from pathlib import Path
from datetime import date, datetime
from unittest.mock import patch
import hashlib
import json
import sys
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import poe_ic_im_mainline_v1_4_bot as bot
from capture_sina import session

result={}
url="https://www.cffex.com.cn/sj/historysj/202609/zip/202609.zip"
try:
    response=session.get(url,timeout=(4,12))
    response.raise_for_status()
    raw=response.content
    (HERE/"raw/cffex_202609.zip").write_bytes(raw)
    bot._CFFEX_MONTH_CACHE["202609"]=raw
    frame=bot._cffex_historical_quote_frame("IC",date(2026,9,30),datetime(2026,9,30,16,tzinfo=bot.BEIJING))
    result["cffex"]=dict(url=url,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),
                         contracts=frame.instrument.tolist(),rows=len(frame),verified=True)
except Exception as exc:
    result["cffex"]=dict(verified=False,error_type=type(exc).__name__,reason=str(exc))

payload=json.loads((HERE/"raw/sse_december_chain.json").read_bytes())
chain=pd.DataFrame(payload["list"],columns=["contract","last","chg_rate","pre_settle","strike"])
stamp=dict(date=str(payload["date"]),time=str(payload["time"]).zfill(6),source="上交所行情接口（保存报文复核）")
anchor=json.loads((HERE/"backup/runtime/ic_im_v1_4_r1/latest.json").read_text(encoding="utf-8"))["products"]["IC"]
clock=datetime(2026,10,21,13,30,tzinfo=bot.BEIJING)
signal=dict(market_date=clock.date(),core_put_target_delta=anchor["verified_core_put_delta"],
            option_monthly_reset_due=False,put_target_momentum_qty=0)
with patch.object(bot,"fetch_510500_chain_with_failover",return_value=(chain,stamp)),patch.object(bot,"_now_beijing",return_value=clock):
    bot._v14_ic_core_overlay(signal,anchor)
evidence=signal["iv_monitor_evidence"]
row=chain.loc[chain.contract.eq(anchor["v14_core_put_contract"])].iloc[0]
assert evidence["contract"]==row["contract"] and evidence["option_price"]==row["last"]
assert evidence["strike"]==row["strike"] and evidence["quote_time"]==stamp["time"]
result["ic_monitor_binding"]=dict(verified=True,contract=evidence["contract"],option_price=evidence["option_price"],
    strike=evidence["strike"],quote_date=str(evidence["quote_date"]),quote_time=evidence["quote_time"],
    note="Bound saved real SSE row only; no ETF/IV or performance claim from unmatched observations")
(HERE/"real_adapter_verification.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(result,ensure_ascii=False,indent=2))
