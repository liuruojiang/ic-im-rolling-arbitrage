"""Capture current December M/A coexistence without touching the strategy."""
from capture_sina import fetch, OUT
import json
import re

receipt = {}
receipt["listing"], text = fetch("sina_december_listing.bin", "https://hq.sinajs.cn/",
                                  {"list": "OP_DOWN_5105002612"})
ids = sorted(set(re.findall(r"CON_OP_(\d+)", text)))
if ids:
    receipt["quotes"], text = fetch("sina_december_quotes.bin", "https://hq.sinajs.cn/list=" +
                                   ",".join("CON_OP_" + i for i in ids))
    receipt["styles"] = {}
    receipt["adjusted_examples"] = []
    for security, payload in re.findall(r'hq_str_CON_OP_(\d+)="([^"]*)"', text):
        fields = payload.split(",")
        if len(fields) <= 46:
            continue
        style = fields[43]
        receipt["styles"][style] = receipt["styles"].get(style, 0) + 1
        if style == "A":
            receipt["adjusted_examples"].append(dict(security_id=security, strike=fields[7], name=fields[37],
                                                       expiry=fields[46], timestamp=fields[32]))
(OUT/"adjusted_capture.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(receipt,ensure_ascii=False))
