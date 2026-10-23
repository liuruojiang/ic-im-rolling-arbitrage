"""Bounded public quote capture, never imports the strategy or ledger."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import re
import requests

HERE = Path(__file__).resolve().parent
OUT = HERE / "raw"
OUT.mkdir(exist_ok=True)
headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://stock.finance.sina.com.cn/"}
session = requests.Session()
# The inherited proxy points to an unavailable local service. Direct public
# requests still pass through the host's network policy; no global change.
session.trust_env = False

def fetch(name, url, params=None):
    started = datetime.now(timezone.utc).isoformat()
    try:
        response = session.get(url, params=params, headers=headers, timeout=(4, 8))
        response.raise_for_status()
        data = response.content
        (OUT / name).write_bytes(data)
        result = dict(started_at=started, completed_at=datetime.now(timezone.utc).isoformat(),
                      status=response.status_code, url=response.url, bytes=len(data),
                      sha256=hashlib.sha256(data).hexdigest(), path=name)
        return result, data.decode("gbk", errors="replace")
    except requests.RequestException as exc:
        return dict(started_at=started, error_type=type(exc).__name__, error=str(exc)), ""

if __name__ == "__main__":
    receipt = {}
    receipt["listing"], text = fetch("sina_listing.bin", "https://hq.sinajs.cn/",
                                      {"list": "OP_DOWN_5105002611"})
    ids = sorted(set(re.findall(r"CON_OP_(\d+)", text)))[:3]
    if ids:
        # Sina treats a percent-encoded comma as part of the symbol name.
        receipt["quotes"], text = fetch("sina_quotes_literal.bin", "https://hq.sinajs.cn/list=" +
            ",".join("CON_OP_" + x for x in ids))
        receipt["contracts"] = ids
        receipt["field_counts"] = [len(payload.split(",")) for payload in re.findall(r'="([^"]*)"', text)]
    (OUT / "capture.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))
