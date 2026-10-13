"""Validate feed observation times without requiring every option to trade at close.

Official CFFEX Last-Modified identifies a whole-file snapshot. Vendor ticktime /
utime identifies a contract update, so its chain maximum is weaker evidence: at
least one contract was updated at close. It is never called a whole-file stamp.
Historical archive adapters do not use these live-feed guards.
"""
from __future__ import annotations

from datetime import date, datetime, time
import re
from typing import Any, Iterable
from zoneinfo import ZoneInfo


BEIJING = ZoneInfo("Asia/Shanghai")
SESSION_CLOSE = time(15, 0)


def _clock(value: datetime) -> datetime:
    return value.replace(tzinfo=BEIJING) if value.tzinfo is None else value.astimezone(BEIJING)


def validate_snapshot_time(
    stamp: datetime, observation_clock: datetime, completed_day: date, label: str,
) -> datetime:
    """Reject future observations and unfinished snapshots of completed sessions."""
    if not isinstance(stamp, datetime) or stamp.tzinfo is None:
        raise RuntimeError(f"{label}缺少带时区的行情更新时间")
    normalized = stamp.astimezone(BEIJING)
    if normalized > _clock(observation_clock):
        raise RuntimeError(f"{label}行情更新时间晚于采集时刻，禁止确认信号")
    if normalized.date() <= completed_day and normalized.time() < SESSION_CLOSE:
        raise RuntimeError(f"{label}最新行情更新时间尚未到收盘，禁止确认收盘信号")
    return normalized


def official_snapshot_evidence(
    stamp: datetime, observation_clock: datetime, completed_day: date, label: str,
) -> dict[str, Any]:
    observed = validate_snapshot_time(stamp, observation_clock, completed_day, label)
    return {
        "source_snapshot_at": observed.isoformat(timespec="seconds"),
        "source_snapshot_kind": "http_last_modified_whole_frame",
    }


def vendor_snapshot_evidence(
    rows: Iterable[dict[str, Any]], observation_clock: datetime,
    completed_day: date, label: str,
) -> dict[str, Any]:
    """Require a closing chain update, while allowing older individual last trades.

Missing row times do not acquire an invented timestamp. At least one dated
timestamp is required for a completed-session feed; every supplied time must
parse, and any future time invalidates the source. Intraday missing-time
behavior is preserved and remains separately subject to existing quote gates.
"""
    stamps: list[datetime] = []
    quote_days: set[date] = set()
    missing = 0
    for row in rows:
        raw_day = row.get("source_date")
        try:
            day = (raw_day if isinstance(raw_day, date) and not isinstance(raw_day, datetime)
                   else date.fromisoformat(str(raw_day)[:10]))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"{label}缺少有效行情日期") from exc
        quote_days.add(day)
        raw_time = str(row.get("source_time", "")).strip()
        if raw_time.lower() in {"", "none", "nan", "nat", "<na>"}:
            missing += 1
            continue
        parsed = None
        for pattern, fmt in ((r"\d{2}:\d{2}:\d{2}", "%H:%M:%S"), (r"\d{6}", "%H%M%S")):
            if not re.fullmatch(pattern, raw_time):
                continue
            try:
                parsed = datetime.strptime(raw_time, fmt).time()
                break
            except ValueError:
                continue
        if parsed is None:
            raise RuntimeError(f"{label}含非法行情更新时间: {raw_time!r}")
        stamps.append(datetime.combine(day, parsed, tzinfo=BEIJING))
    if len(quote_days) != 1:
        raise RuntimeError(f"{label}行情日期为空或不一致")
    quote_day = next(iter(quote_days))
    if not stamps:
        if quote_day <= completed_day:
            raise RuntimeError(f"{label}缺少可证明行情已更新至收盘的时间")
        latest = None
    else:
        # Do not apply the close test to every last-trade row: a quiet contract
        # may retain an earlier time even in a feed that has updated at close.
        latest = validate_snapshot_time(max(stamps), observation_clock, completed_day, label)
    return {
        "source_snapshot_at": latest.isoformat(timespec="seconds") if latest else None,
        "source_snapshot_kind": "latest_available_contract_update",
        "source_snapshot_missing_times": missing,
    }
