"""Daily VIP PE/PB input with explicit, date-exact proxy fallback.

Never log the environment payload. Public ledger provenance omits VIP values.
The supplier's PB/TTM fields do not refresh dividend, bond yield or thresholds.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

REVISION = "legulegu_daily_v1"
ENV = "ICIM_LEGULEGU_DAILY_SNAPSHOT"
FILE_ENV = "ICIM_LEGULEGU_SNAPSHOT_FILE"
DEFAULT_FILE = Path(__file__).resolve().parent / "runtime/legulegu/latest.json"
BEIJING = ZoneInfo("Asia/Shanghai")
CODES = {"IC": "000905", "IM": "000852"}
SOURCE_URLS = [
    "https://legulegu.com/stockdata/zz1000-pb",
    "https://legulegu.com/stockdata/zz1000-ttm-lyr",
    "https://legulegu.com/stockdata/zz500-pb",
    "https://legulegu.com/stockdata/zz500-ttm-lyr",
]


def validate_snapshot(value: object) -> dict:
    """Strict allowlist prevents accidental credential/full-history uploads."""
    keys = {"schema_version", "valuation_date", "fetched_at", "indices",
            "source_urls", "evidence_sha256"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("快照字段不符合最小数据格式")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("快照版本无效")
    day = date.fromisoformat(value["valuation_date"])
    if day.isoformat() != value["valuation_date"]:
        raise ValueError("数据日期无效")
    fetched = datetime.fromisoformat(value["fetched_at"])
    if fetched.tzinfo is None or fetched.astimezone(BEIJING) < datetime.combine(day, time(15), BEIJING):
        raise ValueError("抓取时间缺少时区或早于数据日收盘")
    if sorted(value["source_urls"]) != sorted(SOURCE_URLS):
        raise ValueError("数据来源不完整")
    digest = value["evidence_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("证据摘要无效")
    indices = value["indices"]
    if not isinstance(indices, dict) or set(indices) != set(CODES.values()):
        raise ValueError("必须同时包含中证500和中证1000")
    for fields in indices.values():
        if not isinstance(fields, dict) or set(fields) != {"pe_aggregate_ttm", "pb_aggregate"}:
            raise ValueError("估值口径字段不完整")
        for number in fields.values():
            if type(number) not in (int, float) or not math.isfinite(number) or number <= 0:
                raise ValueError("PE/PB必须为有限正数")
    return value


def resolve(product: str, market_day: date, clock: datetime, *, price: float,
            frozen: dict, anchor_day: date) -> dict:
    ratio = price / float(frozen["price"])
    result = {
        "pe": float(frozen["pe"]) * ratio,
        "pb": float(frozen["pb"]) * ratio,
        "provenance": {
            "revision": REVISION, "mode": "proxy", "market_date": market_day.isoformat(),
            "real_data_date": None, "proxy_anchor_date": anchor_day.isoformat(),
            "reason": "未收到VIP估值快照", "auxiliary_inputs": "国债收益率、股息和相对估值阈值沿用原冻结口径",
        },
    }
    provenance = result["provenance"]
    raw = os.environ.get(ENV, "").strip()
    try:
        path = Path(os.environ[FILE_ENV]) if os.environ.get(FILE_ENV) else DEFAULT_FILE
        if not raw and (os.environ.get(FILE_ENV) or path.is_file()):
            if path.stat().st_size > 16384:
                raise ValueError("快照超出大小上限")
            raw = path.read_text(encoding="utf-8-sig")
        if not raw:
            return result
        if len(raw.encode("utf-8")) > 16384:
            raise ValueError("快照超出大小上限")
        snapshot = validate_snapshot(json.loads(raw))
        provenance["real_data_date"] = snapshot["valuation_date"]
        if snapshot["valuation_date"] != market_day.isoformat():
            provenance["reason"] = "VIP数据日期与信号日不一致（过期或未来日期）"
            return result
        fetched = datetime.fromisoformat(snapshot["fetched_at"])
        if clock.astimezone(BEIJING) < datetime.combine(market_day, time(15), BEIJING) or fetched > clock:
            provenance["reason"] = "尚未收盘或快照抓取时间晚于本次计算时点"
            return result
        fields = snapshot["indices"][CODES[product]]
        result.update(pe=float(fields["pe_aggregate_ttm"]), pb=float(fields["pb_aggregate"]))
        provenance.update(mode="vip_actual", reason="当日完整VIP估值校验通过",
                          snapshot_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                          fetched_at=snapshot["fetched_at"])
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        # Never echo arbitrary invalid content, exception text, or local paths.
        provenance["reason"] = "VIP快照不可读或格式/完整性校验失败"
    return result


def disclosure(provenance: dict | None) -> str:
    p = provenance or {}
    if p.get("mode") == "vip_actual":
        text = f"真实PE/PB：乐咕VIP，数据日期 {p.get('real_data_date')}。"
    else:
        text = ("代理估值／非当日真实PE、PB："
                f"{p.get('reason', '旧账本未记录VIP接入，保留原代理结果')}；"
                f"可用真实数据日期 {p.get('real_data_date') or '未收到/未通过校验'}；"
                f"代理锚点 {p.get('proxy_anchor_date', '2026-08-14')}，按指数价格比例推算。")
    return text + p.get("auxiliary_inputs", "国债收益率、股息和相对估值阈值沿用原冻结口径") + "。"
