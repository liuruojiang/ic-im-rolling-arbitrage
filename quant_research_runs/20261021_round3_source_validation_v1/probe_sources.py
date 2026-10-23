"""Bounded read-only public-feed probes; never import strategy or StateStore."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import requests


HERE = Path(__file__).resolve().parent
LIMIT = 2 * 1024 * 1024
HEADERS = {
    "User-Agent": "Mozilla/5.0 POE-IC-IM-Research/1.0",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}
PROBES = [
    ("sse_etf", "https://yunhq.sse.com.cn:32042/v1/sh1/list/self/510500",
     {"select": "code,cpxxextendname,last,change,chg_rate,amp_rate,volume,amount,prev_close"},
     "https://www.sse.com.cn/assortment/options/price/"),
    ("sse_months", "https://yunhq.sse.com.cn:32042/v1/sho/list/exchange/stockexpire",
     {"select": "stockid,expiremonth"}, "https://www.sse.com.cn/assortment/options/price/"),
    ("sina_etf", "https://hq.sinajs.cn/", {"list": "sh510500"},
     "https://finance.sina.com.cn/"),
    ("sina_months", "https://stock.finance.sina.com.cn/futures/api/openapi.php/StockOptionService.getStockName",
     {"exchange": "null", "cate": "510500"}, "https://stock.finance.sina.com.cn/"),
]


def probe(item):
    name, url, params, referer = item
    started = datetime.now(timezone.utc)
    result = {"name": name, "url": url, "params": params,
              "started_at": started.isoformat(), "purpose": "actual_public_feed_probe",
              "preopen_or_intraday_oracle": False}
    start = time.monotonic()
    try:
        with requests.get(url, params=params, headers={**HEADERS, "Referer": referer},
                          stream=True, timeout=(4, 6)) as response:
            result.update(status_code=response.status_code,
                          headers={k: v for k, v in response.headers.items() if k.lower() in {
                              "content-type", "date", "etag", "last-modified", "cache-control"}})
            chunks = []
            size = 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > LIMIT:
                    raise ValueError("response exceeded 2 MiB capture limit")
                if time.monotonic() - start > 15:
                    raise TimeoutError("capture exceeded 15 second deadline")
                chunks.append(chunk)
            body = b"".join(chunks)
            target = HERE / "raw" / (name + ".bin")
            if target.exists():
                raise FileExistsError("refusing to overwrite original probe bytes")
            target.write_bytes(body)
            result.update(body_file=target.relative_to(HERE).as_posix(), bytes=len(body),
                          sha256=hashlib.sha256(body).hexdigest(),
                          status="response_received" if response.ok else "http_error")
    except Exception as exc:
        # Public endpoint errors only; no environment, credentials or local state.
        result.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:1800])
    result.update(finished_at=datetime.now(timezone.utc).isoformat(),
                  elapsed_seconds=round(time.monotonic() - start, 3))
    return result


def main():
    (HERE / "raw").mkdir(parents=True, exist_ok=True)
    result_path = HERE / "probe_results.json"
    if result_path.exists():
        raise FileExistsError("probe has already run; use a new version for another attempt")
    fixture = HERE.parents[1] / "tests/fixtures/icim_external_review_round2_20261021/sse_december_identity.json"
    payload = json.loads(fixture.read_text(encoding="utf-8-sig"))
    rows = payload.get("list", [])
    put = next((row for row in rows if isinstance(row, list)
                and str(row[0]).startswith("510500P") and str(row[-1]).isdigit()), None)
    probes = list(PROBES)
    if put is not None:
        probes.append(("sina_dated_open", "https://stock.finance.sina.com.cn/futures/api/jsonp_v2.php//StockOptionDaylineService.getSymbolInfo",
                       {"symbol": str(put[-1])}, "https://stock.finance.sina.com.cn/"))
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(probe, probes))
    result_path.write_text(json.dumps({"results": results,
        "strategy_ledger_written": False, "market_phase_assertion": "actual acquisition only; not an intraday oracle"},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps([{k: row[k] for k in ["name", "status", "status_code", "bytes", "elapsed_seconds", "error_type"] if k in row}
                      for row in results], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
