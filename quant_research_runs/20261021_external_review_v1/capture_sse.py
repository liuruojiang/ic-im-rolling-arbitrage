from capture_sina import session, OUT
from datetime import datetime, timezone
import hashlib
import json
import requests

receipt = dict(started_at=datetime.now(timezone.utc).isoformat())
try:
    response = session.get("https://yunhq.sse.com.cn:32042/v1/sho/list/tstyle/510500_12",
                          params={"select":"contractid,last,chg_rate,presetpx,exepx", "order":"contractid,ase"},
                          headers={"Referer":"https://www.sse.com.cn/assortment/options/price/"}, timeout=(4,8))
    response.raise_for_status()
    (OUT/"sse_december_chain.json").write_bytes(response.content)
    payload=response.json()
    receipt.update(status=response.status_code, sha256=hashlib.sha256(response.content).hexdigest(),
                   keys=list(payload), rows=len(payload.get("list") or []),
                   adjusted_examples=[r for r in payload.get("list") or [] if "A" in str(r[0])][:3])
except (requests.RequestException,ValueError) as exc:
    receipt.update(error_type=type(exc).__name__,error=str(exc))
(OUT/"sse_capture.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(receipt,ensure_ascii=False))
