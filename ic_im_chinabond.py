"""Official ChinaBond 10Y daily input; no VIP/browser dependency."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
import time as time_module
from datetime import date, datetime, time
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SOURCE = "https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/more?locale=cn_zh"
FILE_ENV = "ICIM_CHINABOND_SNAPSHOT_FILE"
BEIJING = ZoneInfo("Asia/Shanghai")
REVISION = "chinabond_10y_daily_v1"


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self.table = None
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(re.sub(r"\s+", "", "".join(self.cell)))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None


def parse_official_html(html: str) -> dict:
    parser = _Tables()
    parser.feed(html)
    matches = []
    for table in parser.tables:
        if not table or not table[0]:
            continue
        header = table[0]
        stamp = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\(%\)", header[0])
        if stamp is None or header.count("10年") != 1:
            continue
        for row in table[1:]:
            if not row or row[0] != "中债国债收益率曲线":
                continue
            if len(row) != len(header):
                raise ValueError("中债期限列不完整")
            source_day = date.fromisoformat(stamp[1])
            percent = float(row[header.index("10年")])
            if not math.isfinite(percent) or not -5 <= percent <= 30:
                raise ValueError("中债10年期数值无效")
            matches.append({"source_date": source_day.isoformat(), "yield_decimal": percent / 100.0})
    if len(matches) != 1:
        raise ValueError("未找到唯一的中债国债10年期百分比报价")
    return matches[0]


def fetch_daily(expected_day: date, *, attempts: int = 3) -> dict:
    """At most three direct official-page requests; all failures are explicit."""
    if not 1 <= attempts <= 3:
        raise ValueError("attempts must be 1..3")
    result = {"revision": REVISION, "status": "unavailable", "source": SOURCE,
              "expected_date": expected_day.isoformat(), "source_date": None,
              "reason": "中债当日数据未取得"}
    for attempt in range(1, attempts + 1):
        result["attempts"] = attempt
        try:
            with requests.get(SOURCE, timeout=(5, 15), headers={
                "User-Agent": "Mozilla/5.0 ICIM-research-digest",
                "Cache-Control": "no-cache",
            }) as response:
                response.raise_for_status()
                raw = response.content
                if len(raw) > 1_000_000:
                    raise ValueError("官方页面超出预期大小")
                observed = parse_official_html(raw.decode("utf-8-sig"))
            result.update(observed, page_sha256=hashlib.sha256(raw).hexdigest())
            if observed["source_date"] == expected_day.isoformat():
                result.update(status="ok", reason="当日中债官方10年期数据校验通过")
                break
            result["reason"] = "中债公布日期与信号日不一致（未更新或未来日期）"
        except (requests.RequestException, ValueError, UnicodeError):
            result["reason"] = "中债官方访问失败或页面/期限/单位校验失败"
        if attempt < attempts:
            time_module.sleep(2)
    result["fetched_at"] = datetime.now(BEIJING).isoformat()
    return result


def resolve(market_day: date, clock: datetime, frozen_rate: float, anchor_day: date) -> dict:
    result = {"revision": REVISION, "mode": "frozen_fallback", "yield_decimal": float(frozen_rate),
              "used_date": anchor_day.isoformat(), "observed_date": None,
              "reason": "未接入中债当日快照", "source": SOURCE}
    path = os.environ.get(FILE_ENV, "")
    if not path:
        return result
    try:
        file = Path(path)
        if file.stat().st_size > 16384:
            raise ValueError("oversize")
        value = json.loads(file.read_text(encoding="utf-8-sig"))
        if value["revision"] != REVISION or value["source"] != SOURCE:
            raise ValueError("wrong source")
        stamp = date.fromisoformat(value["source_date"]) if value.get("source_date") else None
        result["observed_date"] = stamp.isoformat() if stamp else None
        if value["status"] != "ok":
            result["reason"] = "中债抓取失败、未更新或日期校验未通过"
            return result
        if stamp != market_day or value["expected_date"] != market_day.isoformat():
            result["reason"] = "中债数据日期与信号日不一致"
            return result
        fetched = datetime.fromisoformat(value["fetched_at"])
        publication = datetime.combine(market_day, time(17, 30), BEIJING)
        if fetched.tzinfo is None or fetched > clock or fetched < publication or clock < publication:
            result["reason"] = "中债日终数据在本次计算时点尚不可用"
            return result
        rate = value["yield_decimal"]
        if type(rate) not in (int, float) or not math.isfinite(rate) or not -0.05 <= rate <= 0.30:
            raise ValueError("invalid decimal rate")
        digest = value["page_sha256"]
        if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
            raise ValueError("invalid evidence")
        result.update(mode="official_actual", yield_decimal=float(rate), used_date=stamp.isoformat(),
                      reason="当日中债官方10年期数据", fetched_at=value["fetched_at"], page_sha256=digest)
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        result["reason"] = "中债快照不可读或格式校验失败"
    return result


def disclosure(value: dict) -> str:
    rate = float(value["yield_decimal"])
    if value["mode"] == "official_actual":
        return f"国债利率：中债官方10年期 {rate:.4%}，数据日期 {value['used_date']}（估值与期权定价共用）。"
    return (f"国债利率回退：{value['reason']}；沿用 {value['used_date']} 冻结值 {rate:.4%}，"
            f"非当日利率；本次源日期 {value.get('observed_date') or '未取得'}（估值与期权定价共用）。")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-date", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--require-current", action="store_true", help="Smoke-check only: fail unless actual")
    args = parser.parse_args()
    day = date.fromisoformat(args.expected_date)
    result = fetch_daily(day)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=output.parent, prefix=".chinabond-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        os.replace(temp, output)
    finally:
        Path(temp).unlink(missing_ok=True)
    os.environ[FILE_ENV] = str(output)
    chosen = resolve(day, datetime.now(BEIJING), 0.016964, date(2026, 8, 14))
    print(disclosure(chosen))
    return int(args.require_current and chosen["mode"] != "official_actual")


if __name__ == "__main__":
    raise SystemExit(main())
