"""Dated, dual-source close marks for an already held standard IC Put.

This validator has no network or ledger writes.  A dated daily close is not a
fresh intraday quote and must never acquire a fabricated 15:00 timestamp.  The
caller retains the original HTTP requests and responses in the evidence store.
"""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
from zoneinfo import ZoneInfo

REVISION = "held_ic_put_daily_close_20261113_v1"
EFFECTIVE_SIGNAL_DATE = date(2026, 11, 13)
BEIJING = ZoneInfo("Asia/Shanghai")


class HeldPutCloseUnavailable(RuntimeError):
    """The requested held leg has insufficient dated close evidence."""


class HeldPutCloseConflict(RuntimeError):
    """Positive identity, price or source evidence contradicts another input."""


def _day(value):
    if type(value) is date:
        return value
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{8}", value):
        raise HeldPutCloseUnavailable("收盘备用缺少明确日期")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HeldPutCloseUnavailable("收盘备用日期无效") from exc


def _positive(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise HeldPutCloseUnavailable("收盘备用价格不是有效数值")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise HeldPutCloseUnavailable("收盘备用价格不是有效数值") from exc
    if not result.is_finite() or result <= 0:
        raise HeldPutCloseUnavailable("收盘备用价格必须有限且为正")
    try:
        floating = float(result)
    except (OverflowError, ValueError) as exc:
        raise HeldPutCloseUnavailable("收盘备用价格超出可表示范围") from exc
    if not math.isfinite(floating) or floating <= 0:
        raise HeldPutCloseUnavailable("收盘备用价格超出可表示范围")
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise HeldPutCloseConflict("收盘备用JSON含重复字段")
        result[key] = value
    return result


def _json(raw):
    if not isinstance(raw, bytes) or not raw or len(raw) > 12 * 1024 * 1024:
        raise HeldPutCloseUnavailable("收盘备用原始响应缺失或超过上限")
    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError) as exc:
        raise HeldPutCloseUnavailable("收盘备用JSON无法解码") from exc


def _packet(raw, kind, security_id):
    if not isinstance(raw, bytes) or not raw or len(raw) > 64 * 1024:
        raise HeldPutCloseUnavailable("持仓Put身份原始响应缺失或超过上限")
    try:
        text = raw.decode("gb18030", errors="strict")
    except UnicodeError as exc:
        raise HeldPutCloseUnavailable("持仓Put身份响应无法解码") from exc
    match = re.fullmatch(r'\s*var\s+hq_str_' + kind + r'_([0-9]{8})="([^"\r\n]*)";?\s*', text)
    if match is None:
        raise HeldPutCloseUnavailable("持仓Put身份响应不是唯一完整证券报文")
    if match.group(1) != security_id:
        raise HeldPutCloseConflict("持仓Put身份响应证券ID与已审持仓不一致")
    return match.group(2).split(",")


def _ohlc(opening, high, low, close):
    opening, high, low, close = map(_positive, (opening, high, low, close))
    if low > high or not low <= opening <= high or not low <= close <= high:
        raise HeldPutCloseConflict("期权日线OHLC关系无效")
    return close


def _sina_close(raw, signal_day):
    if not isinstance(raw, bytes) or not raw or len(raw) > 12 * 1024 * 1024:
        raise HeldPutCloseUnavailable("新浪日线原始响应缺失或超过上限")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise HeldPutCloseUnavailable("新浪日线无法解码") from exc
    # The native endpoint has a fixed HTML-looking JavaScript comment.  It is
    # inert source text here; only the one complete JSON array is consumed.
    match = re.fullmatch(r"\s*(?:/\*.*?\*/\s*)?\((\[.*\])\)\s*;?\s*", text, flags=re.DOTALL)
    if match is None:
        raise HeldPutCloseUnavailable("新浪日线封套不完整")
    rows = _json(match.group(1).encode("utf-8"))
    if not isinstance(rows, list) or not rows:
        raise HeldPutCloseUnavailable("新浪日线为空")
    days, prices = [], {}
    for row in rows:
        if not isinstance(row, dict):
            raise HeldPutCloseUnavailable("新浪日线行格式无效")
        day = _day(row.get("d"))
        if day in prices or day > signal_day:
            raise HeldPutCloseConflict("新浪日线日期重复或晚于信号日")
        prices[day] = _ohlc(row.get("o"), row.get("h"), row.get("l"), row.get("c"))
        days.append(day)
    if days != sorted(days):
        raise HeldPutCloseUnavailable("新浪日线日期乱序")
    if signal_day not in prices:
        raise HeldPutCloseUnavailable("新浪日线缺少所需当日收盘")
    return prices[signal_day]


def _eastmoney_close(raw, security_id, signal_day):
    payload = _json(raw)
    if not isinstance(payload, dict) or type(payload.get("rc")) is not int or payload["rc"] != 0:
        raise HeldPutCloseUnavailable("东方财富日线状态无效")
    data = payload.get("data")
    if (not isinstance(data, dict) or data.get("code") != security_id
            or type(data.get("market")) is not int or data["market"] != 10):
        raise HeldPutCloseConflict("东方财富期权证券ID或市场与已审持仓不一致")
    rows = data.get("klines")
    if not isinstance(rows, list) or not rows:
        raise HeldPutCloseUnavailable("东方财富期权日线为空")
    days, prices = [], {}
    for row in rows:
        if not isinstance(row, str):
            raise HeldPutCloseUnavailable("东方财富期权日线行无效")
        fields = row.split(",")
        if len(fields) < 7:
            raise HeldPutCloseUnavailable("东方财富期权日线字段不足")
        day = _day(fields[0])
        if day in prices or day > signal_day:
            raise HeldPutCloseConflict("东方财富期权日线日期重复或晚于信号日")
        prices[day] = _ohlc(fields[1], fields[3], fields[4], fields[2])
        days.append(day)
    if days != sorted(days):
        raise HeldPutCloseUnavailable("东方财富期权日线日期乱序")
    if signal_day not in prices:
        raise HeldPutCloseUnavailable("东方财富期权日线缺少所需当日收盘")
    return prices[signal_day]


def _check_master(payload, security_id, contract, strike, expiry, signal_day):
    if payload is None:
        return None
    if (not isinstance(payload, dict)
            or payload.get("sqlId") != "SSE_ZQPZ_YSP_GGQQZSXT_XXPL_DRHY_SEARCH_L"
            or not (payload.get("isPagination") is False or payload.get("isPagination") == "false")
            or type(payload.get("actionErrors")) is not list or payload["actionErrors"]
            or type(payload.get("fieldErrors")) is not dict or payload["fieldErrors"]
            or not isinstance(payload.get("result"), list) or not payload["result"]):
        raise HeldPutCloseUnavailable("已有官方主表格式不足，不能判断积极冲突")
    rows = payload["result"]
    dates = {_day(row.get("TIMESAVE")) for row in rows if isinstance(row, dict)}
    if len(dates) != 1 or len([row for row in rows if isinstance(row, dict)]) != len(rows):
        raise HeldPutCloseConflict("官方主表日期混合或行格式无效")
    snapshot_day = next(iter(dates))
    if snapshot_day > signal_day:
        raise HeldPutCloseConflict("官方主表日期晚于信号日")
    selected = [row for row in rows if row.get("SECURITY_ID") == security_id]
    if len(selected) != 1:
        raise HeldPutCloseConflict("官方主表未唯一对应已审持仓证券ID")
    row = selected[0]
    if (row.get("CONTRACT_ID") != contract or row.get("CALL_OR_PUT") != "认沽"
            or row.get("OPTION_TYPE") != "E" or row.get("DELISTFLAG") != "否"
            or _positive(row.get("EXERCISE_PRICE")) != strike
            or _positive(row.get("CONTRACT_UNIT")) != Decimal(10000)
            or any(_day(row.get(key)) != expiry for key in ("EXPIRE_DATE", "EXERCISE_DATE", "END_DATE"))):
        raise HeldPutCloseConflict("官方既有主表与持仓身份、行权价、乘数或到期日冲突")
    return snapshot_day.isoformat()


def validate_held_put_close(*, anchor, signal_day, expected_previous_day, current_ledger_day,
                            expected_expiry, observed_at, detail_raw, identity_raw,
                            sina_daily_raw, eastmoney_daily_raw, monthly_reset=False,
                            official_master=None):
    """Return a dated held-leg mark and evidence, never an executable target.

    All raw payloads must be obtained for the explicitly requested security ID
    through the caller's original-response capture path.  Historical replay
    supplies the original observation time, not a fresh later-day observation.
    """
    if any(type(value) is not date for value in (signal_day, expected_previous_day, current_ledger_day, expected_expiry)):
        raise HeldPutCloseUnavailable("收盘备用需要正式日历给出的精确日期")
    if signal_day < EFFECTIVE_SIGNAL_DATE:
        raise HeldPutCloseUnavailable("收盘备用不改写采用日前的历史规则")
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise HeldPutCloseUnavailable("收盘备用缺少真实带时区采集时刻")
    observed = observed_at.astimezone(BEIJING)
    if observed.date() != signal_day or observed.time().replace(tzinfo=None) < time(15, 30):
        raise HeldPutCloseUnavailable("收盘备用仅限同一信号日15:30后的真实采集")
    if not isinstance(anchor, dict) or monthly_reset is not False:
        raise HeldPutCloseUnavailable("月度重置或新选约不能使用已持仓收盘备用")
    if (anchor.get("v14_route_state") != "future"
            or _day(anchor.get("last_verified_day")) != expected_previous_day
            or current_ledger_day != expected_previous_day or expected_previous_day >= signal_day):
        raise HeldPutCloseUnavailable("收盘备用未绑定正式前一交易日已审核心持仓")
    qty = anchor.get("v14_core_put_qty")
    try:
        valid_qty = (not isinstance(qty, bool) and isinstance(qty, (int, float))
                     and math.isfinite(qty) and qty > 0 and int(qty) == qty)
    except (OverflowError, ValueError, TypeError):
        valid_qty = False
    if not valid_qty:
        raise HeldPutCloseUnavailable("收盘备用仅用于已存在的正整数核心Put持仓")
    contract, security_id = anchor.get("v14_core_put_contract"), anchor.get("v14_core_put_security_id")
    match = re.fullmatch(r"510500P([0-9]{2})([0-9]{2})M([0-9]{5})", str(contract or ""))
    if not match or not isinstance(security_id, str) or not re.fullmatch(r"[0-9]{8}", security_id):
        raise HeldPutCloseUnavailable("收盘备用只接受已审标准M合约及八位证券ID")
    strike = Decimal(match.group(3)) / 1000
    if (expected_expiry <= signal_day or expected_expiry.year != 2000 + int(match.group(1))
            or expected_expiry.month != int(match.group(2))):
        raise HeldPutCloseConflict("持仓Put实际到期日与合约或信号日冲突")
    identity = _packet(identity_raw, "CON_SO", security_id)
    if (len(identity) <= 16 or identity[12] != contract or identity[16] != "M"
            or _positive(identity[13]) != strike):
        raise HeldPutCloseConflict("当前逐证券完整交易代码与已审持仓冲突")
    detail = _packet(detail_raw, "CON_OP", security_id)
    if (len(detail) <= 46 or detail[36] != "510500" or detail[43] != "M" or detail[45] != "P"
            or _positive(detail[7]) != strike or _day(detail[46]) != expected_expiry):
        raise HeldPutCloseConflict("当前逐证券标的、类型、行权价或完整到期日冲突")
    try:
        detail_stamp = datetime.strptime(detail[32], "%Y-%m-%d %H:%M:%S").replace(tzinfo=BEIJING)
    except ValueError as exc:
        raise HeldPutCloseUnavailable("当前逐证券身份缺少明确源日期") from exc
    if detail_stamp.date() != signal_day or detail_stamp > observed:
        raise HeldPutCloseConflict("当前逐证券身份源日期不是信号日或晚于采集")
    master_day = _check_master(official_master, security_id, contract, strike, expected_expiry, signal_day)
    sina_close = _sina_close(sina_daily_raw, signal_day)
    eastmoney_close = _eastmoney_close(eastmoney_daily_raw, security_id, signal_day)
    if abs(sina_close - eastmoney_close) > Decimal("0.000000000001"):
        raise HeldPutCloseConflict("新浪与东方财富当日期权收盘价冲突")
    raw_hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in (
        ("detail", detail_raw), ("identity", identity_raw),
        ("sina_daily", sina_daily_raw), ("eastmoney_daily", eastmoney_daily_raw))}
    if official_master is not None:
        raw_hashes["official_master_semantic"] = hashlib.sha256(json.dumps(
            official_master, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return {
        "row": {"contract": contract, "security_id": security_id, "strike": float(strike),
                "last": float(sina_close), "expiry": expected_expiry, "contract_unit": 10000},
        "evidence": {
            "revision": REVISION, "quote_kind": "daily_close", "signal_day": signal_day.isoformat(),
            "anchor_day": expected_previous_day.isoformat(), "observed_at": observed.isoformat(),
            "source": "新浪与东方财富当日期权日线一致",
            "sources": ["Sina:daily_close", "Eastmoney:daily_close"],
            "contract": contract, "security_id": security_id, "close": float(sina_close),
            "strike": float(strike), "contract_unit": 10000, "expiry": expected_expiry.isoformat(),
            "quote_time": None, "iv_monitor_eligible": False,
            "identity_source_stamp": detail_stamp.isoformat(),
            "identity_time_used_for_freshness": False,
            "official_master_snapshot_day": master_day,
            "official_master_conflict_check_only": official_master is not None,
            "new_listing_or_candidate_certified": False, "raw_sha256": raw_hashes,
        },
    }
