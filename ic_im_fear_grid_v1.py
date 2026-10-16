"""Source-paired IC/IM grid rule and public Fear/Greed data provenance."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from datetime import date, datetime, time, timedelta
from html import unescape
from typing import Any
from zoneinfo import ZoneInfo

import requests


EFFECTIVE_SIGNAL_DATE = date(2026, 10, 8)
INTRADAY_FALLBACK_EFFECTIVE_DATE = date(2026, 10, 14)
INTRADAY_POLICY_REVISION = "ic_im_intraday_fear_previous_session_20261014_v1"
INTRADAY_FALLBACK_STATUS = "intraday_previous_session"
CLOSE_FALLBACK_EFFECTIVE_DATE = date(2026, 10, 14)
CLOSE_POLICY_REVISION = "ic_im_close_fear_previous_session_20261015_v1"
CLOSE_FALLBACK_STATUS = "close_previous_session"
ENTRY_MAX = 25.0
EXIT_MIN = 50.0
GRID_UNITS = 0.5
SOURCE_NONE = "none"
SOURCES = frozenset({SOURCE_NONE, "valuation_only", "fear_only", "both"})
CSV_URL = "https://baifenwei.com/data/fear-greed/fear-greed-full.csv"
PAGE_URL = "https://baifenwei.com/indicator/fear-greed/"
BEIJING = ZoneInfo("Asia/Shanghai")
RULES = {
    "IC": {"entry": 0.5, "exit": 1.0},
    "IM": {"entry": 1.6, "exit": 2.0},
}


def source_for_legacy_units(units: float) -> str:
    """Existing nonzero grid positions were entered by valuation only."""
    if units == 0.0:
        return SOURCE_NONE
    if units == GRID_UNITS:
        return "valuation_only"
    raise ValueError(f"legacy grid units outside fix9 domain: {units}")


def transition(
    product: str, current_units: float, current_source: str,
    valuation_score: float, fear_score: float | None,
) -> tuple[float, str, str]:
    """Return next-open units, frozen entry source, and transition reason."""
    if product not in RULES:
        raise ValueError(f"unsupported grid product: {product}")
    if current_units not in {0.0, GRID_UNITS} or current_source not in SOURCES:
        raise ValueError("invalid grid holding/source")
    if (current_units == 0.0) != (current_source == SOURCE_NONE):
        raise ValueError("grid units and frozen entry source disagree")
    valuation = float(valuation_score)
    if not math.isfinite(valuation):
        raise ValueError("nonfinite valuation score")
    fear = None if fear_score is None else float(fear_score)
    if fear is not None and (not math.isfinite(fear) or not 0.0 <= fear <= 100.0):
        raise ValueError("invalid Fear/Greed score")

    rule = RULES[product]
    if current_units == 0.0:
        valuation_entry = valuation <= rule["entry"]
        fear_entry = fear is not None and fear <= ENTRY_MAX
        if valuation_entry or fear_entry:
            source = ("both" if valuation_entry and fear_entry else
                      "valuation_only" if valuation_entry else "fear_only")
            return GRID_UNITS, source, f"enter_{source}"
        return 0.0, SOURCE_NONE, "hold_flat"

    valuation_exit = current_source in {"valuation_only", "both"} and valuation >= rule["exit"]
    fear_exit = current_source in {"fear_only", "both"} and fear is not None and fear >= EXIT_MIN
    if valuation_exit or fear_exit:
        reason = "both" if valuation_exit and fear_exit else "valuation" if valuation_exit else "fear"
        return 0.0, SOURCE_NONE, f"exit_{reason}"
    return GRID_UNITS, current_source, "hold_grid"


def previous_exchange_session(signal_day: date) -> date:
    """Reuse the formal calendar, including its fail-closed coverage boundary."""
    # The strategy imports this module; resolve its calendar only at call time.
    import poe_ic_im_mainline_v1_4_bot as strategy

    strategy._require_official_exchange_calendar(signal_day, "恐慌盘中沿用日历")
    if not strategy._is_exchange_trading_day(signal_day):
        raise RuntimeError("恐慌盘中沿用的信号日不是交易日")
    prior = signal_day - timedelta(days=1)
    strategy._require_official_exchange_calendar(prior, "恐慌上一交易日日历")
    return strategy._roll_backward_exchange_day(prior, require_official=True)


def _validation_clock() -> datetime:
    return datetime.now(BEIJING)


def validate_observation_clock(
    signal: dict[str, Any], *, validation_clock: datetime | None = None,
) -> None:
    """Bind new evidence to completed retrieval, never a frozen request clock."""
    if signal.get("fear_source_policy_revision") is not None and not flexible_source(signal):
        raise RuntimeError("恐贪取数修订身份无效")
    if flexible_source(signal):
        validate_source_evidence(signal, validation_clock=validation_clock)
        return
    day = date.fromisoformat(str(signal.get("market_date"))[:10])
    if day < INTRADAY_FALLBACK_EFFECTIVE_DATE:
        return
    status = signal.get("fear_data_status")
    carried = status in {INTRADAY_FALLBACK_STATUS, CLOSE_FALLBACK_STATUS}
    applied = signal.get("fear_fallback_applied")
    if applied is not None and type(applied) is not bool:
        raise RuntimeError("恐慌沿用标识必须是布尔值")
    if not carried and (applied is True or signal.get("fear_fallback_reason") is not None
                        or signal.get("fear_fallback_timing") is not None):
        raise RuntimeError("恐慌非沿用状态夹带矛盾的沿用证明")
    value = signal.get("fear_greed_index")
    if value is not None and (isinstance(value, bool) or not math.isfinite(float(value))
                              or not 0 <= float(value) <= 100):
        raise RuntimeError("恐慌读数不是合格的有限数值")
    stamps = {}
    fields = ["fear_retrieved_at", "fear_page_updated_at"]
    if carried or signal.get("fear_fetch_started_at") is not None:
        fields.append("fear_fetch_started_at")
    if signal.get("signal_generated_at") is not None:
        fields.append("signal_generated_at")
    for field in fields:
        stamp = datetime.fromisoformat(str(signal.get(field)))
        if stamp.tzinfo is None:
            raise RuntimeError("恐慌采集与生成时钟必须含时区")
        stamps[field] = stamp.astimezone(BEIJING)
    retrieved = stamps["fear_retrieved_at"]
    if stamps["fear_page_updated_at"] > retrieved:
        raise RuntimeError("恐慌页面更新晚于实际采集完成")
    checked = validation_clock or _validation_clock()
    if checked.tzinfo is None or retrieved > checked.astimezone(BEIJING):
        raise RuntimeError("恐慌采集时刻晚于独立校验时钟")
    if "fear_fetch_started_at" in stamps and stamps["fear_fetch_started_at"] > retrieved:
        raise RuntimeError("恐慌采集开始晚于实际完成")
    if signal.get("close_confirmed") is False and (
        retrieved.date() != day or retrieved.time() >= time(15)
    ):
        raise RuntimeError("恐慌盘中采集必须在信号日15:00前完成")
    generated = stamps.get("signal_generated_at")
    if generated is not None and not retrieved <= generated <= checked.astimezone(BEIJING):
        raise RuntimeError("恐慌生成时间不在实际采集至独立校验区间")
    if (retrieved.date() > day or (generated is not None and generated.date() > day)) and (generated is None
            or signal.get("signal_generation_timing") != "retrospective_replay"):
        raise RuntimeError("恐慌跨日补录必须保留真实生成时间和回放标识")


def validate_intraday_fallback(
    signal: dict[str, Any], *, validation_clock: datetime | None = None,
) -> None:
    """Independently bind an intraday carried value to the exact prior session."""
    if flexible_source(signal):
        validate_source_evidence(signal, validation_clock=validation_clock)
        if signal.get("fear_data_status") != INTRADAY_FALLBACK_STATUS or signal.get("close_confirmed") is not False:
            raise RuntimeError("盘中沿用状态非法")
        return
    day = date.fromisoformat(str(signal.get("market_date"))[:10])
    if day < INTRADAY_FALLBACK_EFFECTIVE_DATE or signal.get("close_confirmed") is not False:
        raise RuntimeError("恐慌上一交易日沿用仅限新授权后的盘中预估，不得确认收盘")
    validate_observation_clock(signal, validation_clock=validation_clock)
    prior = previous_exchange_session(day).isoformat()
    if (
        signal.get("fear_data_status") != INTRADAY_FALLBACK_STATUS
        or signal.get("fear_intraday_policy_revision") != INTRADAY_POLICY_REVISION
        or signal.get("fear_fallback_applied") is not True
        or signal.get("fear_fallback_reason") != "same_day_not_published"
        or any(str(signal.get(field))[:10] != prior for field in (
            "fear_data_date", "fear_previous_exchange_date",
            "fear_latest_available_date", "fear_page_data_date",
        ))
    ):
        raise RuntimeError("恐慌沿用值必须绑定正式日历上一交易日及来源日期/修订")
    value = signal.get("fear_greed_index")
    if isinstance(value, bool) or value is None or not math.isfinite(float(value)) or not 0 <= float(value) <= 100:
        raise RuntimeError("恐慌沿用值不是合格的有限读数")
    if signal.get("fear_source_url") != CSV_URL or re.fullmatch(
        r"[0-9a-f]{64}", str(signal.get("fear_csv_sha256", ""))
    ) is None:
        raise RuntimeError("恐慌沿用值缺少合格来源及哈希")
    stamps = {}
    for field in ("fear_retrieved_at", "fear_page_updated_at"):
        stamp = datetime.fromisoformat(str(signal.get(field)))
        if stamp.tzinfo is None:
            raise RuntimeError("恐慌沿用值采集/更新时间必须含时区")
        stamps[field] = stamp.astimezone(BEIJING)
    retrieved = stamps["fear_retrieved_at"]
    updated = stamps["fear_page_updated_at"]
    if (
        retrieved.date() != day or retrieved.time() >= time(15, 0)
        or updated > retrieved
        or updated <= datetime.combine(date.fromisoformat(prior), time(15, 0), tzinfo=BEIJING)
    ):
        raise RuntimeError("恐慌沿用值缺少本信号日15:00前的合格采集证据")


def validate_close_fallback(
    signal: dict[str, Any], *, validation_clock: datetime | None = None,
) -> None:
    """Verify the approved lagged close input without relabeling its data day."""
    if flexible_source(signal):
        validate_source_evidence(signal, validation_clock=validation_clock)
        if signal.get("fear_data_status") != CLOSE_FALLBACK_STATUS or signal.get("close_confirmed") is not True:
            raise RuntimeError("收盘沿用状态非法")
        return
    day = date.fromisoformat(str(signal.get("market_date"))[:10])
    if day < CLOSE_FALLBACK_EFFECTIVE_DATE or signal.get("close_confirmed") is not True:
        raise RuntimeError("恐慌收盘沿用仅限新授权后的收盘模型信号")
    validate_observation_clock(signal, validation_clock=validation_clock)
    prior = previous_exchange_session(day).isoformat()
    reason = signal.get("fear_fallback_reason")
    source_day = prior if reason == "same_day_not_published" else day.isoformat()
    if (
        signal.get("fear_data_status") != CLOSE_FALLBACK_STATUS
        or signal.get("fear_close_policy_revision") != CLOSE_POLICY_REVISION
        or signal.get("fear_fallback_applied") is not True
        or reason not in {"same_day_not_published", "same_day_not_close_confirmed"}
        or any(str(signal.get(field))[:10] != prior for field in (
            "fear_data_date", "fear_previous_exchange_date",
        ))
        or any(str(signal.get(field))[:10] != source_day for field in (
            "fear_latest_available_date", "fear_page_data_date",
        ))
    ):
        raise RuntimeError("恐慌收盘沿用必须绑定正式上一session及来源日期/修订")
    value = signal.get("fear_greed_index")
    if isinstance(value, bool) or value is None or not math.isfinite(float(value)) or not 0 <= float(value) <= 100:
        raise RuntimeError("恐慌收盘沿用值不是合格的有限读数")
    if signal.get("fear_source_url") != CSV_URL or re.fullmatch(
        r"[0-9a-f]{64}", str(signal.get("fear_csv_sha256", ""))
    ) is None:
        raise RuntimeError("恐慌收盘沿用值缺少合格来源及哈希")
    stamps = {}
    for field in ("fear_retrieved_at", "fear_page_updated_at"):
        stamp = datetime.fromisoformat(str(signal.get(field)))
        if stamp.tzinfo is None:
            raise RuntimeError("恐慌收盘沿用值时间必须含时区")
        stamps[field] = stamp.astimezone(BEIJING)
    retrieved, updated = stamps["fear_retrieved_at"], stamps["fear_page_updated_at"]
    if reason == "same_day_not_close_confirmed" and (updated.date() != day or updated > datetime.combine(day, time(15), tzinfo=BEIJING)):
        raise RuntimeError("恐慌已有同日收盘后更新，不得伪造未确认沿用原因")
    if (
        retrieved.date() < day or (retrieved.date() == day and retrieved.time() <= time(15, 0))
        or updated > retrieved
        or updated <= datetime.combine(date.fromisoformat(prior), time(15, 0), tzinfo=BEIJING)
    ):
        raise RuntimeError("恐慌收盘沿用缺少信号日收盘后的真实采集证据")
    expected_timing = "retrospective_replay" if retrieved.date() > day else "same_day_post_close"
    if signal.get("fear_fallback_timing") != expected_timing:
        raise RuntimeError("恐慌收盘沿用的真实采集/回放时点标识不一致")
    if expected_timing == "retrospective_replay":
        generated = datetime.fromisoformat(str(signal.get("signal_generated_at")))
        if generated.tzinfo is None or generated.astimezone(BEIJING) < retrieved or signal.get(
            "signal_generation_timing"
        ) != "retrospective_replay":
            raise RuntimeError("恐慌收盘沿用补录必须保留真实生成时间及retrospective_replay标记")


def publication_view(signal: dict[str, Any]) -> dict[str, Any]:
    """Hide unconfirmed targets without changing the calculated model state."""
    view = dict(signal)
    day = signal.get("market_date")
    if day is None or date.fromisoformat(str(day)[:10]) < EFFECTIVE_SIGNAL_DATE:
        return view
    status = signal.get("fear_data_status")
    try:
        validate_observation_clock(signal)
    except (TypeError, ValueError, RuntimeError):
        status = "invalid_observation_clock"
    if flexible_source(signal) and status != "invalid_observation_clock" and signal.get("fear_greed_index") is not None:
        return view
    if status == INTRADAY_FALLBACK_STATUS:
        try:
            validate_intraday_fallback(signal)
            return view
        except (TypeError, ValueError, RuntimeError):
            pass
    if status == CLOSE_FALLBACK_STATUS:
        try:
            validate_close_fallback(signal)
            return view
        except (TypeError, ValueError, RuntimeError):
            pass
    # A proven hole in published history retains its existing policy semantics.
    if status == "missing_published_history":
        try:
            retrieved_day = date.fromisoformat(str(signal.get("fear_retrieved_at"))[:10])
            latest_day = date.fromisoformat(
                str(signal.get("fear_latest_available_date") or retrieved_day)[:10]
            )
            if date.fromisoformat(str(day)[:10]) < min(retrieved_day, latest_day):
                return view
        except (TypeError, ValueError):
            pass
    try:
        score = float(signal.get("fear_greed_index"))
        confirmed = (
            math.isfinite(score) and 0 <= score <= 100
            and str(signal.get("fear_data_date"))[:10] == str(day)[:10]
            and status in {
                "intraday_provisional", "same_day_post_close", "retrospective_replay"
            }
        )
    except (TypeError, ValueError):
        confirmed = False
    if confirmed:
        return view
    for key in (
        "grid_target", "grid_action", "grid_entry_source_target",
        "grid_transition_reason", "total_units_target", "total_units_change",
    ):
        view["reference_" + key] = view.get(key)
        view[key] = None
    view.update(
        grid_action="UNCONFIRMED",
        grid_target_confirmed=False,
        total_units_target_confirmed=False,
        signal_completeness="partial",
        incomplete_components=["grid", "total_futures"],
        grid_unconfirmed_reason=signal.get("fear_unavailable_reason") or
        "当日恐慌读数及上一交易日沿用值未核验；网格及完整总期货目标未确认",
    )
    return view


def _bounded_get(url: str, maximum: int) -> bytes:
    response = requests.get(
        url, timeout=8,
        headers={"User-Agent": "ICIM-Fix10-Research-Signal/1.0"},
    )
    response.raise_for_status()
    data = response.content
    if not data or len(data) > maximum:
        raise RuntimeError(f"恐贪来源文件大小异常: {url}")
    return data


def _parse_page(page: bytes) -> tuple[date, float, datetime]:
    html = unescape(page.decode("utf-8"))
    day_match = re.search(
        r'<p class="fg-stat-label">最新日期</p>\s*<p class="fg-stat-value"[^>]*>(\d{4}-\d{2}-\d{2})</p>',
        html,
    )
    score_match = re.search(
        r'<p class="fg-stat-label">最新指数值</p>\s*<p class="fg-stat-value[^\"]*">([0-9]+(?:\.[0-9]+)?)</p>',
        html,
    )
    update_match = re.search(
        r'<p class="fg-update">数据日 (\d{4}-\d{2}-\d{2}) · 更新 (\d{4}-\d{2}-\d{2} \d{2}:\d{2})</p>',
        html,
    )
    if not day_match or not score_match or not update_match:
        raise RuntimeError("恐贪指标页缺少可核验的数据日、分数或更新时间")
    day = date.fromisoformat(day_match.group(1))
    if update_match.group(1) != day.isoformat():
        raise RuntimeError("恐贪指标页数据日互相冲突")
    score = float(score_match.group(1))
    updated = datetime.strptime(update_match.group(2), "%Y-%m-%d %H:%M").replace(tzinfo=BEIJING)
    return day, score, updated


def _fetch_score_legacy(signal_day: date, mode: str, fetch_started: datetime | None = None) -> dict[str, Any]:
    """Prefer T; approved intraday/close policies may carry exactly T-1."""
    if mode not in {"intraday", "close"}:
        raise ValueError("unsupported Fear fetch mode")
    fetch_started = fetch_started or datetime.now(BEIJING)
    raw_csv = _bounded_get(CSV_URL, 1024 * 1024)
    rows = list(csv.DictReader(io.StringIO(raw_csv.decode("utf-8-sig"))))
    if not rows or set(rows[0]) != {"date", "fear_greed_index"}:
        raise RuntimeError("恐贪 CSV 字段不符")
    values: dict[date, float] = {}
    previous: date | None = None
    for row in rows:
        day = date.fromisoformat(row["date"])
        value = float(row["fear_greed_index"])
        if (previous is not None and day <= previous) or not math.isfinite(value) or not 0.0 <= value <= 100.0:
            raise RuntimeError("恐贪 CSV 日期顺序或读数非法")
        values[day] = value
        previous = day
    latest = previous
    if latest is None:
        raise RuntimeError("恐贪 CSV 为空")
    page_day, page_score, page_updated = _parse_page(_bounded_get(PAGE_URL, 512 * 1024))
    fetched = datetime.now(BEIJING)
    if fetched < fetch_started:
        raise RuntimeError("恐慌实际采集完成早于开始")
    if page_day != latest or not math.isclose(page_score, values[latest], abs_tol=0.005, rel_tol=0.0):
        raise RuntimeError("恐贪网页与 CSV 最新数据不一致，暂停确认信号")
    if latest > fetched.date() or page_updated > fetched or signal_day > fetched.date() or page_updated.date() < page_day:
        raise RuntimeError("恐贪来源出现未来日期或更新时间，暂停确认信号")

    value = values.get(signal_day)
    close_today_unconfirmed = (
        mode == "close" and signal_day >= CLOSE_FALLBACK_EFFECTIVE_DATE
        and value is not None and latest == signal_day
        and page_updated <= datetime.combine(signal_day, time(15), tzinfo=BEIJING)
    )
    if close_today_unconfirmed:
        value = None
    if signal_day > latest or close_today_unconfirmed:
        status = "close_unpublished" if mode == "close" else "intraday_unpublished"
    elif value is None:
        # A later published date proves this is an actual hole in the public series.
        status = "missing_published_history"
    elif signal_day < fetched.date() or signal_day < latest:
        status = "retrospective_replay"
    elif mode == "intraday":
        status = "intraday_provisional"
    else:
        if page_updated.date() != signal_day or page_updated.time() <= time(15, 0):
            raise RuntimeError(f"恐贪 {signal_day} 指标页尚无收盘后更新，暂停确认信号")
        status = "same_day_post_close"
    data_day = signal_day if value is not None else None
    fallback: dict[str, Any] = {}
    if (
        mode == "intraday" and signal_day >= INTRADAY_FALLBACK_EFFECTIVE_DATE
        and signal_day == fetched.date() and fetched.time() < time(15, 0)
    ):
        fallback.update(fear_intraday_policy_revision=INTRADAY_POLICY_REVISION,
                        fear_fallback_applied=False)
        if value is None:
            prior = previous_exchange_session(signal_day)
            fallback["fear_previous_exchange_date"] = prior.isoformat()
            if latest == prior and prior in values:
                value, data_day = values[prior], prior
                status = INTRADAY_FALLBACK_STATUS
                fallback.update(fear_fallback_applied=True,
                                fear_fallback_reason="same_day_not_published")
            else:
                fallback["fear_unavailable_reason"] = (
                    f"当日恐慌未发布；正式上一交易日{prior}无合格值，"
                    f"来源最新数据日为{latest}，禁止继续沿用更早值"
                )
    if mode == "close" and value is None and (signal_day > latest or close_today_unconfirmed):
        if signal_day >= CLOSE_FALLBACK_EFFECTIVE_DATE:
            prior = previous_exchange_session(signal_day)
            if (latest == prior or close_today_unconfirmed) and prior in values:
                value, data_day = values[prior], prior
                status = CLOSE_FALLBACK_STATUS
                fallback.update(
                    fear_close_policy_revision=CLOSE_POLICY_REVISION,
                    fear_fallback_applied=True, fear_fallback_reason=(
                        "same_day_not_close_confirmed" if close_today_unconfirmed else "same_day_not_published"),
                    fear_previous_exchange_date=prior.isoformat(),
                    fear_fallback_timing="retrospective_replay" if fetched.date() > signal_day else "same_day_post_close",
                )
        if value is None:
            raise RuntimeError(
                f"恐贪 {signal_day} 收盘数据尚未发布，且无合格上一实际交易日值，暂停确认信号"
            )
    evidence = {
        "fear_greed_index": value,
        "fear_data_date": data_day.isoformat() if data_day is not None else None,
        "fear_data_status": status,
        "fear_retrieved_at": fetched.isoformat(timespec="seconds"),
        "fear_fetch_started_at": fetch_started.isoformat(timespec="seconds"),
        "fear_page_updated_at": page_updated.isoformat(timespec="minutes"),
        "fear_csv_sha256": hashlib.sha256(raw_csv).hexdigest(),
        "fear_source_url": CSV_URL,
        "fear_latest_available_date": latest.isoformat(),
        "fear_page_data_date": page_day.isoformat(),
        **fallback,
    }
    if mode == "intraday" and signal_day >= INTRADAY_FALLBACK_EFFECTIVE_DATE and (
        fetched.date() != signal_day or fetched.time() >= time(15)
    ):
        evidence.update(fear_greed_index=None, fear_data_date=None,
                        fear_data_status="intraday_window_closed", fear_fallback_applied=False,
                        fear_unavailable_reason="真实采集完成不在信号日15:00前；盘中窗口已结束")
        return evidence
    if status == INTRADAY_FALLBACK_STATUS:
        validate_intraday_fallback({**evidence, "market_date": signal_day,
                                    "close_confirmed": False}, validation_clock=fetched)
    if status == CLOSE_FALLBACK_STATUS:
        validate_close_fallback({**evidence, "market_date": signal_day,
                                 "close_confirmed": True,
                                 "signal_generated_at": fetched.isoformat(timespec="seconds"),
                                 "signal_generation_timing": "retrospective_replay" if
                                 fetched.date() > signal_day else "close_confirmed"}, validation_clock=fetched)
    return evidence


SOURCE_POLICY_REVISION = "baifenwei_either_source_20261016_v1"
SOURCE_POLICY_DATE = date(2026, 10, 16)
JIU_URL = "https://ai2.jiucaishuo.com/api/fear-greed"
JIU_PAGE_URL = "https://jiucaishuo.com/indicators?tab=fg"


def flexible_source(signal: dict[str, Any]) -> bool:
    return signal.get("fear_source_policy_revision") == SOURCE_POLICY_REVISION


def _parse_csv(raw: bytes) -> dict[date, float]:
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    if not rows or set(rows[0]) != {"date", "fear_greed_index"}:
        raise ValueError("恐贪CSV字段不符")
    values: dict[date, float] = {}
    previous = None
    for row in rows:
        day, value = date.fromisoformat(row["date"]), float(row["fear_greed_index"])
        if (previous is not None and day <= previous) or not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError("恐贪CSV日期顺序或读数非法")
        values[day] = value
        previous = day
    return values


def source_disclosure(signal: dict[str, Any]) -> str:
    return str(signal.get("fear_source_warning") or (
        "百分位CSV与网页读数一致，已完成双侧验证。"
        if signal.get("fear_cross_validation") == "matched" else
        "百分位来源验证状态以原始记录为准。"))


def reference_disclosure(signal: dict[str, Any]) -> str:
    value = signal.get("jiu_fear_greed_index")
    if value is None:
        return "9圈恐慌贪婪指数：暂未取得；仅供参考，不参与网格信号。"
    mismatch = "；与百分位数据日不同" if signal.get("jiu_data_date") != signal.get("fear_data_date") else ""
    return (f"9圈恐慌贪婪指数：{float(value):g}；数据日 {signal.get('jiu_data_date')}"
            f"；采集 {signal.get('jiu_retrieved_at')}{mismatch}；"
            f"仅供参考，不参与网格信号。官网：{JIU_PAGE_URL}")


def fetch_jiu_reference(signal_day: date) -> dict[str, Any]:
    """Reference-only snapshot, never an input to transition or its entry source."""
    result = {"jiu_fear_greed_index": None, "jiu_data_date": None,
              "jiu_reference_only": True, "jiu_source_url": JIU_URL,
              "jiu_page_url": JIU_PAGE_URL, "jiu_sha256": None}
    try:
        raw = _bounded_get(JIU_URL, 2 * 1024 * 1024)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("9圈响应必须为对象")
        rows = payload.get("history", [])
        if not isinstance(rows, list):
            raise ValueError("9圈历史字段异常")
        rows = [*rows, {"date": payload.get("date"), "value": payload.get("value")}]
        values = {}
        now = datetime.now(BEIJING)
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("9圈记录字段异常")
            day = date.fromisoformat(str(row.get("date")))
            value = row.get("value")
            if isinstance(value, bool) or value is None or not math.isfinite(float(value)) or not 0 <= float(value) <= 100 or day > now.date():
                raise ValueError("9圈日期或读数非法")
            if day in values and values[day] != float(value):
                raise ValueError("9圈同日记录冲突")
            values[day] = float(value)
        eligible = [d for d in values if d <= signal_day]
        if not eligible:
            raise ValueError("9圈无不晚于信号日的参考读数")
        latest = max(eligible)
        result.update(jiu_fear_greed_index=values[latest], jiu_data_date=latest.isoformat(),
                      jiu_sha256=hashlib.sha256(raw).hexdigest(), jiu_status="reference_only",
                      jiu_warning="数据日早于信号日" if latest < signal_day else "")
    except Exception as exc:
        result.update(jiu_status="unavailable", jiu_warning=f"9圈参考取数失败：{type(exc).__name__}: {exc}")
    result["jiu_retrieved_at"] = datetime.now(BEIJING).isoformat(timespec="seconds")
    return result


def validate_source_evidence(signal: dict[str, Any], *, validation_clock: datetime | None = None) -> None:
    """Independent consumer checks; missing page clocks are never fabricated."""
    if not flexible_source(signal):
        raise RuntimeError("恐贪取数修订身份无效")
    day = date.fromisoformat(str(signal.get("market_date"))[:10])
    stamps = {}
    for key in ("fear_fetch_started_at", "fear_retrieved_at", "fear_page_updated_at", "signal_generated_at"):
        value = signal.get(key)
        if value is None and key in {"fear_page_updated_at", "signal_generated_at"}:
            continue
        stamp = datetime.fromisoformat(str(value))
        if stamp.tzinfo is None:
            raise RuntimeError("恐贪证据时间必须含时区")
        stamps[key] = stamp.astimezone(BEIJING)
    retrieved = stamps["fear_retrieved_at"]
    checked = validation_clock or _validation_clock()
    if (retrieved.date() < SOURCE_POLICY_DATE or day > retrieved.date()
            or not stamps["fear_fetch_started_at"] <= retrieved <= checked.astimezone(BEIJING)
            or stamps.get("fear_page_updated_at", retrieved) > retrieved):
        raise RuntimeError("恐贪取数时间证据非法")
    generated = stamps.get("signal_generated_at")
    if generated is not None and not retrieved <= generated <= checked.astimezone(BEIJING):
        raise RuntimeError("恐贪生成时间不在采集与校验之间")
    if (retrieved.date() > day or (generated is not None and generated.date() > day)) and (generated is None or signal.get("signal_generation_timing") != "retrospective_replay"):
        raise RuntimeError("跨日补录必须标记真实生成时点与retrospective_replay")
    if signal.get("close_confirmed") is False and (retrieved.date() != day or retrieved.time() >= time(15)):
        raise RuntimeError("盘中采集窗口已结束")
    available = signal.get("fear_available_sources")
    if not isinstance(available, list) or len(set(available)) != len(available) or any(x not in {"csv", "page"} for x in available):
        raise RuntimeError("恐贪来源清单无效")
    selected = signal.get("fear_selected_source")
    quality = signal.get("fear_cross_validation")
    if len(available) == 2:
        csv_day = date.fromisoformat(str(signal.get("fear_csv_latest_date")))
        page_day = date.fromisoformat(str(signal.get("fear_page_data_date")))
        csv_value, page_value = float(signal.get("fear_csv_latest_value")), float(signal.get("fear_page_value"))
        if (not all(math.isfinite(v) and 0 <= v <= 100 for v in (csv_value, page_value))
                or max(csv_day, page_day) > retrieved.date()):
            raise RuntimeError("双侧快照日期或读数非法")
        agrees = csv_day == page_day and math.isclose(csv_value, page_value, abs_tol=.005, rel_tol=0)
        if (quality == "matched") != agrees:
            raise RuntimeError("双侧验证标识与实际快照不符")
    expected = "unavailable" if not available else "single_source" if len(available) == 1 else None
    if (expected is not None and quality != expected) or (expected is None and quality not in {"matched", "mismatch"}):
        raise RuntimeError("恐贪双侧验证状态与来源数量不符")
    for source in available:
        digest = signal.get(f"fear_{source}_sha256")
        if re.fullmatch(r"[0-9a-f]{64}", str(digest)) is None:
            raise RuntimeError("恐贪可用来源缺少哈希")
    if "page" in available and ("fear_page_updated_at" not in stamps or signal.get("fear_page_data_date") is None):
        raise RuntimeError("网页来源缺少真实日期与更新时间")
    if "page" not in available and (signal.get("fear_page_updated_at") is not None or signal.get("fear_page_data_date") is not None):
        raise RuntimeError("不可虚构网页发布时间")
    observations = signal.get("fear_csv_observations")
    if "csv" in available:
        if not isinstance(observations, dict):
            raise RuntimeError("CSV缺少信号日/上一交易日读数快照")
        csv_day = date.fromisoformat(str(signal.get("fear_csv_latest_date")))
        csv_value = signal.get("fear_csv_latest_value")
        if (isinstance(csv_value, bool) or not math.isfinite(float(csv_value))
                or not 0 <= float(csv_value) <= 100 or csv_day > retrieved.date()):
            raise RuntimeError("CSV最新快照非法")
        for observed_day, observed_value in observations.items():
            if (date.fromisoformat(observed_day) > csv_day or isinstance(observed_value, bool)
                    or not math.isfinite(float(observed_value)) or not 0 <= float(observed_value) <= 100):
                raise RuntimeError("CSV读数快照非法")
        if csv_day in {day, previous_exchange_session(day)} and csv_day.isoformat() not in observations:
            raise RuntimeError("CSV最新交易日不可从观察快照删除")
        if csv_day.isoformat() in observations and not math.isclose(float(observations[csv_day.isoformat()]), float(csv_value), abs_tol=.005, rel_tol=0):
            raise RuntimeError("CSV最新读数与快照冲突")
    if "page" in available:
        page_value = signal.get("fear_page_value")
        if isinstance(page_value, bool) or not math.isfinite(float(page_value)) or not 0 <= float(page_value) <= 100:
            raise RuntimeError("网页读数快照非法")
    value = signal.get("fear_greed_index")
    if value is None:
        if signal.get("fear_data_date") is not None or signal.get("fear_data_status") != "unavailable" or not signal.get("fear_unavailable_reason"):
            raise RuntimeError("恐贪缺值状态非法")
        return
    if (isinstance(value, bool) or not math.isfinite(float(value)) or not 0 <= float(value) <= 100
            or selected not in available or signal.get("fear_source_url") != {"csv": CSV_URL, "page": PAGE_URL}[selected]):
        raise RuntimeError("恐贪读数或选中来源非法")
    data_day = date.fromisoformat(str(signal.get("fear_data_date")))
    status = signal.get("fear_data_status")
    observed_value = (observations.get(data_day.isoformat()) if selected == "csv" else signal.get("fear_page_value"))
    if observed_value is None or not math.isclose(float(observed_value), float(value), abs_tol=.005, rel_tol=0):
        raise RuntimeError("选中恐贪读数与来源快照不符")
    if signal.get("fear_fallback_applied") is True:
        prior = previous_exchange_session(day)
        if (data_day != prior or signal.get("fear_previous_exchange_date") != prior.isoformat()
                or status != (CLOSE_FALLBACK_STATUS if signal.get("close_confirmed") else INTRADAY_FALLBACK_STATUS)
                or signal.get("fear_fallback_reason") not in {"same_day_not_published", "same_day_not_close_confirmed"}):
            raise RuntimeError("恐贪只允许严格上一实际交易日值")
        today_observed = ("csv" in available and (day.isoformat() in observations or csv_day == day)) or (
            "page" in available and signal.get("fear_page_data_date") == day.isoformat())
        if signal.get("fear_fallback_reason") == "same_day_not_published" and today_observed:
            raise RuntimeError("来源已有当日值，不得伪造当日未发布")
        if signal.get("fear_fallback_reason") == "same_day_not_close_confirmed" and (
                not signal.get("close_confirmed") or "page" not in available
                or signal.get("fear_page_data_date") != day.isoformat()
                or stamps["fear_page_updated_at"] > datetime.combine(day, time(15), tzinfo=BEIJING)):
            raise RuntimeError("未确认收盘沿用必须有当日盘中网页证据")
        expected_timing = ("retrospective_replay" if retrieved.date() > day else
                           "same_day_post_close" if signal.get("close_confirmed") else "intraday")
        if signal.get("fear_fallback_timing") != expected_timing:
            raise RuntimeError("沿用时点标签与真实采集不符")

    elif data_day != day or status not in {"same_day_post_close", "same_day_unverified_close", "intraday_provisional", "retrospective_replay"}:
        raise RuntimeError("恐贪数据日或状态非法")
    if signal.get("close_confirmed") and retrieved.date() == day and retrieved.time() <= time(15):
        raise RuntimeError("收盘模式必须在收盘后采集")
    latest = date.fromisoformat(str(signal.get("fear_latest_available_date")))
    if not data_day <= latest <= retrieved.date():
        raise RuntimeError("恐贪来源最新日期非法")
    if selected == "page" and signal.get("fear_page_data_date") != data_day.isoformat():
        raise RuntimeError("网页单点数据日与选中值不符")
    carried = status in {CLOSE_FALLBACK_STATUS, INTRADAY_FALLBACK_STATUS}
    if type(signal.get("fear_fallback_applied")) is not bool or carried != signal.get("fear_fallback_applied"):
        raise RuntimeError("恐贪沿用状态与标识矛盾")
    if not carried and any(signal.get(key) is not None for key in ("fear_fallback_reason", "fear_fallback_timing")):
        raise RuntimeError("非沿用状态不得附带沿用证据")
    close = signal.get("close_confirmed") is True
    if status == "same_day_post_close" and (not close or retrieved.date() != day
            or "page" not in available or signal.get("fear_page_data_date") != day.isoformat()
            or not math.isclose(float(signal.get("fear_page_value", float("nan"))), float(value), abs_tol=.005, rel_tol=0)
            or stamps.get("fear_page_updated_at", retrieved) <= datetime.combine(day, time(15), tzinfo=BEIJING)):
        raise RuntimeError("收盘后发布证明不足")
    if status == "same_day_unverified_close" and (not close or retrieved.date() != day or not signal.get("fear_source_warning")):
        raise RuntimeError("未验证收盘观察值必须披露")
    if status == "intraday_provisional" and close:
        raise RuntimeError("盘中观察不得伪装为收盘")
    if status == "retrospective_replay" and retrieved.date() <= day:
        raise RuntimeError("回放状态必须保留真实跨日时点")
    if quality != "matched" and not signal.get("fear_source_warning"):
        raise RuntimeError("非双侧一致必须披露提示")


def fetch_score(signal_day: date, mode: str) -> dict[str, Any]:
    started = datetime.now(BEIJING)
    if started.date() < SOURCE_POLICY_DATE:
        return _fetch_score_legacy(signal_day, mode, started)
    if mode not in {"intraday", "close"}:
        raise ValueError("unsupported Fear fetch mode")
    sources, errors = {}, {}
    for name, url, limit in (("csv", CSV_URL, 1024 * 1024), ("page", PAGE_URL, 512 * 1024)):
        try:
            raw = _bounded_get(url, limit)
            if name == "csv":
                values = _parse_csv(raw)
                updated = None
            else:
                page_day, page_value, updated = _parse_page(raw)
                if not math.isfinite(page_value) or not 0 <= page_value <= 100 or updated.date() < page_day:
                    raise ValueError("网页日期或读数非法")
                values = {page_day: page_value}
            now = datetime.now(BEIJING)
            if max(values) > now.date() or (updated is not None and updated > now):
                raise ValueError("来源日期/发布时间在未来")
            sources[name] = {"values": values, "latest": max(values), "updated": updated,
                             "sha256": hashlib.sha256(raw).hexdigest()}
        except Exception as exc:  # independent bounded requests; one failure cannot veto the other.
            errors[name] = f"{type(exc).__name__}: {exc}"
    fetched = datetime.now(BEIJING)
    page, csv_source = sources.get("page"), sources.get("csv")
    quality = "unavailable" if not sources else "single_source" if len(sources) == 1 else "matched"
    if len(sources) == 2 and (page["latest"] != csv_source["latest"] or not math.isclose(
            page["values"][page["latest"]], csv_source["values"][csv_source["latest"]], abs_tol=.005, rel_tol=0)):
        quality = "mismatch"
    warnings = []
    if quality == "single_source":
        warnings.append(f"仅取得{'CSV' if csv_source else '网页'}单侧数据，未完成双侧验证。")
    elif quality == "mismatch":
        warnings.append("CSV与网页不一致；优先可用的当日值，同日冲突采用CSV，未通过双侧验证。")
    elif quality == "unavailable":
        warnings.append("百分位CSV与网页均未取得合格数据；网格及合计目标未确认，日报继续。")
    # Prefer a source containing T. A latest webpage cannot prove a hole in CSV history.
    selected = next((name for name in ("csv", "page") if name in sources and signal_day in sources[name]["values"]), None)
    value = sources[selected]["values"][signal_day] if selected else None
    data_day = signal_day if selected else None
    fallback = {"fear_fallback_applied": False}
    status = "intraday_provisional" if mode == "intraday" else "same_day_post_close"
    if fetched.date() > signal_day:
        status = "retrospective_replay"
    close_proven = (page and page["latest"] == signal_day and page["updated"].date() == signal_day
                    and page["updated"] > datetime.combine(signal_day, time(15), tzinfo=BEIJING)
                    and value is not None and math.isclose(page["values"][signal_day], value, abs_tol=.005, rel_tol=0))
    close_unproven = (mode == "close" and value is not None and fetched.date() == signal_day and not close_proven)
    # Preserve the approved pre-close-page T-1 carry when the CSV contains it.
    preclose_page = (mode == "close" and page and page["latest"] == signal_day
                     and page["updated"] <= datetime.combine(signal_day, time(15), tzinfo=BEIJING))
    if value is None or preclose_page:
        prior = previous_exchange_session(signal_day)
        prior_source = next((name for name in ("csv", "page") if name in sources and prior in sources[name]["values"]), None)
        if prior_source:
            selected, data_day = prior_source, prior
            value = sources[selected]["values"][prior]
            status = CLOSE_FALLBACK_STATUS if mode == "close" else INTRADAY_FALLBACK_STATUS
            fallback.update(fear_fallback_applied=True, fear_previous_exchange_date=prior.isoformat(),
                            fear_fallback_reason="same_day_not_close_confirmed" if preclose_page else "same_day_not_published",
                            fear_fallback_timing="retrospective_replay" if fetched.date() > signal_day else "same_day_post_close" if mode == "close" else "intraday")
    if close_unproven and not fallback["fear_fallback_applied"]:
        status = "same_day_unverified_close"
        warnings.append("未取得该读数的收盘后发布证明；按实际采集的当日观察值用于模型，不声称官网已确认收盘值。")
    if mode == "intraday" and (fetched.date() != signal_day or fetched.time() >= time(15)):
        value, data_day = None, None
        fallback = {"fear_fallback_applied": False}
        warnings.append("盘中真实采集窗口已结束。")
    if value is not None and not page and not close_unproven:
        warnings.append("网页发布时间未取得，未核实收盘后发布证明。")
    if value is None:
        status = "unavailable"
        warnings.append("没有当日或严格上一交易日的合格百分位读数；网格及合计目标未确认。")
    latest = sources[selected]["latest"] if selected else max((s["latest"] for s in sources.values()), default=None)
    observed_days = {signal_day}
    if csv_source:
        observed_days.add(previous_exchange_session(signal_day))
    return {
        "fear_source_policy_revision": SOURCE_POLICY_REVISION,
        "fear_greed_index": value, "fear_data_date": data_day.isoformat() if data_day else None,
        "fear_data_status": status, "fear_retrieved_at": fetched.isoformat(timespec="seconds"),
        "fear_fetch_started_at": started.isoformat(timespec="seconds"),
        "fear_available_sources": list(sources), "fear_selected_source": selected,
        "fear_cross_validation": quality, "fear_source_warning": " ".join(warnings),
        "fear_source_errors": errors, "fear_source_url": {"csv": CSV_URL, "page": PAGE_URL}.get(selected),
        "fear_csv_sha256": csv_source["sha256"] if csv_source else None,
        "fear_page_sha256": page["sha256"] if page else None,
        "fear_page_updated_at": page["updated"].isoformat(timespec="minutes") if page else None,
        "fear_page_data_date": page["latest"].isoformat() if page else None,
        "fear_page_value": page["values"][page["latest"]] if page else None,
        "fear_csv_latest_date": csv_source["latest"].isoformat() if csv_source else None,
        "fear_csv_latest_value": csv_source["values"][csv_source["latest"]] if csv_source else None,
        "fear_csv_observations": {d.isoformat(): v for d, v in csv_source["values"].items()
                                  if d in observed_days} if csv_source else None,
        "fear_latest_available_date": latest.isoformat() if latest else None,
        "fear_unavailable_reason": " ".join(warnings) if value is None else None, **fallback,
    }
