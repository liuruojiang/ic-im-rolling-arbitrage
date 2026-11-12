"""Hash-bound public-holiday calendar accepted for research from 2026-11-12.

This is a user-approved derivation, not a claim of exchange confirmation.
Weekend make-up workdays remain non-trading days. No network or ledger writes.
"""
from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import re

REVISION = "cn_state_council_2027_approved_20261112_v1"
EFFECTIVE_SIGNAL_DATE = date(2026, 11, 12)
DATA_PATH = Path(__file__).resolve().parent / "data/exchange_calendar/state_council_2027_v1.json"
NOTICE_PATH = DATA_PATH.with_name("state_council_2027_notice_search_snapshot.txt")
EXPECTED_DATA_SHA256 = "da68c14c0ab24fc720f5aab1cceaa49646441b00e8162a0d552d10612cfdacca"
EXPECTED_NOTICE_SHA256 = "05f1bece35e93bfadfa93f811a135d9cffb1950766352a1aa7e6bc2cf43c47b7"


class CalendarEvidenceError(RuntimeError):
    pass


def _day(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise CalendarEvidenceError("日历日期格式无效")
    return date.fromisoformat(value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CalendarEvidenceError("日历JSON含重复键")
        result[key] = value
    return result


@dataclass(frozen=True)
class Calendar:
    year: int
    holidays: frozenset[date]
    makeup_workdays: frozenset[date]
    source_url: str

    def trading_day(self, day: date) -> bool:
        if day.year != self.year:
            raise CalendarEvidenceError("日历年度不匹配")
        return day.weekday() < 5 and day not in self.holidays

    def evidence(self) -> dict:
        return {"year": self.year, "source": "state_council_derived", "policy_revision": REVISION,
                "accepted_for_research": True, "exchange_notice_verified": False,
                "source_url": self.source_url, "source_published_date": "2026-11-05",
                "source_capture_kind": "search_service_retrieved_text",
                "source_text_sha256": EXPECTED_NOTICE_SHA256, "calendar_data_sha256": EXPECTED_DATA_SHA256}


def load() -> Calendar:
    """Recheck bytes on every load; a warm process cannot hide changed source files."""
    try:
        raw = DATA_PATH.read_bytes()
        notice = NOTICE_PATH.read_bytes()
        if len(raw) > 16384 or len(notice) > 32768:
            raise CalendarEvidenceError("日历来源超过大小上限")
        if hashlib.sha256(raw).hexdigest() != EXPECTED_DATA_SHA256:
            raise CalendarEvidenceError("日历数据SHA-256不符")
        if hashlib.sha256(notice).hexdigest() != EXPECTED_NOTICE_SHA256:
            raise CalendarEvidenceError("国务院通知文本SHA-256不符")
        value = json.loads(raw, object_pairs_hook=_unique_object)
        if (value["schema"] != "icim_state_council_calendar_v1" or type(value["year"]) is not int
                or value["year"] != 2027 or value["revision"] != REVISION
                or value["acceptance_effective_date"] != EFFECTIVE_SIGNAL_DATE.isoformat()
                or value["exchange_notice_verified"] is not False or value["weekend_trading"] is not False):
            raise CalendarEvidenceError("日历身份或来源级别无效")
        source = value["source"]
        if (source["issuer"] != "国务院办公厅" or source["document_number"] != "国办发明电〔2026〕10号"
                or source["published_date"] != "2026-11-05"
                or source["capture_kind"] != "search_service_retrieved_text"
                or source["text_file"] != NOTICE_PATH.name or source["text_sha256"] != EXPECTED_NOTICE_SHA256
                or source["url"] != "https://www.gov.cn/zhengce/content/202611/content_7085101.htm"):
            raise CalendarEvidenceError("国务院通知身份无效")
        periods = value["holidays"]
        if not isinstance(periods, list) or len(periods) != 7 or len({p["name"] for p in periods}) != 7:
            raise CalendarEvidenceError("假期集合不完整")
        holidays = set()
        for period in periods:
            start, end = _day(period["start"]), _day(period["end"])
            if start.year != 2027 or end.year != 2027 or not 0 <= (end-start).days <= 14:
                raise CalendarEvidenceError("假期区间无效")
            cursor = start
            while cursor <= end:
                if cursor in holidays:
                    raise CalendarEvidenceError("假期区间重叠")
                holidays.add(cursor)
                cursor += timedelta(days=1)
        makeup = [_day(v) for v in value["makeup_workdays"]]
        if len(makeup) != 5 or len(set(makeup)) != 5 or any(d.year != 2027 or d.weekday() < 5 for d in makeup):
            raise CalendarEvidenceError("调休周末集合无效")
        return Calendar(2027, frozenset(holidays), frozenset(makeup), source["url"])
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise CalendarEvidenceError(f"国务院推导日历来源不可用：{type(exc).__name__}: {exc}") from exc


def for_day(day: date, decision_day: date) -> Calendar | None:
    if day.year != 2027 or decision_day < EFFECTIVE_SIGNAL_DATE:
        return None
    return load()


def description() -> str:
    return ("2027交易日历采用国务院2026-11-05假期通知＋周末休市推导（用户批准）；"
            "调休周末不开市，尚未标为交易所年度公告确认。")
