"""Independent offline date and quantity checks for modern IC/IM delivery.

The calendar below is the published common CFFEX/SSE 2026 calendar already
used by the official strategy.  Its source is
liuruojiang/ic-im-rolling-arbitrage@739071a201506dd869b9a156e490c3bafd95a334,
poe_ic_im_mainline_v1_4_bot.py::_EXCHANGE_CLOSURES and
_OFFICIAL_EXCHANGE_CALENDAR_YEARS.  This is an explicit covered-year snapshot,
never a NAV-derived or weekday-only calendar.  New years fail closed until
their official schedule is added and checked against the strategy calendar.
"""
from datetime import date, datetime, time, timedelta
import math
import re
from typing import Any
from zoneinfo import ZoneInfo


CALENDAR_SOURCE_REVISION = "739071a201506dd869b9a156e490c3bafd95a334"
CALENDAR_YEARS = frozenset({2026})
EXCHANGE_CLOSURES = frozenset({
    date(2026, 1, 1), date(2026, 1, 2),
    *[date(2026, 2, day) for day in range(16, 24)],
    date(2026, 4, 6), date(2026, 5, 1), date(2026, 5, 4), date(2026, 5, 5),
    date(2026, 6, 19), date(2026, 9, 25),
    *[date(2026, 10, day) for day in range(1, 8)],
})


def is_session(day: date) -> bool:
    if day.year not in CALENDAR_YEARS:
        raise ValueError(f"independent exchange calendar does not cover {day.year}")
    return day.weekday() < 5 and day not in EXCHANGE_CLOSURES


def adjacent_session(day: date, direction: int = 1) -> date:
    if direction not in {-1, 1}:
        raise ValueError("calendar direction must be -1 or 1")
    candidate = day + timedelta(days=direction)
    while not is_session(candidate):
        candidate += timedelta(days=direction)
    return candidate


def validate_session_dates(days: dict[str, date], publication_mode: str) -> None:
    for field, day in days.items():
        if not is_session(day):
            raise ValueError(f"{field} is not a common exchange trading session")
    if days["next_trade_day"] != adjacent_session(days["market_date"]):
        raise ValueError("next_trade_day is not the next actual common exchange session")
    if publication_mode == "realtime" and days["completed_day"] != adjacent_session(days["market_date"], -1):
        raise ValueError("realtime completed day is not the previous actual common exchange session")


def _validation_clock() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def validate_fear_clock(signal: dict[str, Any], day: date, mode: str) -> None:
    if flexible_source(signal):
        validate_source_evidence(signal)
        return
    if day < date(2026, 10, 14):
        return
    carried = signal.get("fear_data_status") in {"close_previous_session", "intraday_previous_session"}
    applied = signal.get("fear_fallback_applied")
    if applied is not None and type(applied) is not bool:
        raise ValueError("Fear fallback flag must be boolean")
    if not carried and (applied is True or signal.get("fear_fallback_reason") is not None
                        or signal.get("fear_fallback_timing") is not None):
        raise ValueError("nonfallback Fear status has contradictory carry evidence")
    beijing = ZoneInfo("Asia/Shanghai")
    fields = ["fear_retrieved_at", "fear_page_updated_at"]
    if carried or signal.get("fear_fetch_started_at") is not None:
        fields.append("fear_fetch_started_at")
    if signal.get("signal_generated_at") is not None:
        fields.append("signal_generated_at")
    stamps = {}
    for field in fields:
        stamp = datetime.fromisoformat(str(signal.get(field)))
        if stamp.tzinfo is None:
            raise ValueError("Fear collection/generation clock lacks timezone")
        stamps[field] = stamp.astimezone(beijing)
    retrieved = stamps["fear_retrieved_at"]
    if stamps["fear_page_updated_at"] > retrieved:
        raise ValueError("Fear page update is later than completed retrieval")
    checked = _validation_clock()
    if checked.tzinfo is None or retrieved > checked.astimezone(beijing):
        raise ValueError("Fear retrieval is later than independent validation clock")
    if "fear_fetch_started_at" in stamps and stamps["fear_fetch_started_at"] > retrieved:
        raise ValueError("Fear collection starts after completed retrieval")
    if mode == "realtime" and (retrieved.date() != day or retrieved.time() >= time(15)):
        raise ValueError("Fear realtime retrieval must complete on T before 15:00")
    generated = stamps.get("signal_generated_at")
    if generated is not None and not retrieved <= generated <= checked.astimezone(beijing):
        raise ValueError("Fear generation is outside collection/independent validation interval")
    if (retrieved.date() > day or (generated is not None and generated.date() > day)) and (generated is None
            or signal.get("signal_generation_timing") != "retrospective_replay"):
        raise ValueError("cross-day Fear signal must retain real generation and retrospective identity")


def validate_report_clock(payload: dict[str, Any], day: date) -> None:
    if day < date(2026, 10, 14):
        return
    values = [s.get("signal_generated_at") for s in payload["signals"].values()
              if s.get("signal_generated_at") is not None]
    if not values and payload.get("report_generated_at") is None:
        return  # Read-only sparse archives predate the real completion field.
    completed = datetime.fromisoformat(str(payload.get("report_generated_at")))
    checked = _validation_clock()
    if completed.tzinfo is None or not completed <= checked:
        raise ValueError("report completion is later than independent validation clock or lacks timezone")
    for value in values:
        generated = datetime.fromisoformat(str(value))
        if generated.tzinfo is None or generated > completed:
            raise ValueError("signal generation is later than actual report completion")


def validate_fear_fallback(signal: dict[str, Any], day: date, mode: str) -> None:
    if flexible_source(signal):
        validate_source_evidence(signal)
        return
    """Independent source/date/time boundary for the explicitly approved carry."""
    close = mode == "close_confirmed"
    expected_status = "close_previous_session" if close else "intraday_previous_session"
    revision_field = "fear_close_policy_revision" if close else "fear_intraday_policy_revision"
    revision = ("ic_im_close_fear_previous_session_20261015_v1" if close else
                "ic_im_intraday_fear_previous_session_20261014_v1")
    if day < date(2026, 10, 14) or not is_session(day):
        raise ValueError("Fear fallback is outside its approved signal-day/calendar scope")
    validate_fear_clock(signal, day, mode)
    prior = adjacent_session(day, -1)
    reason = signal.get("fear_fallback_reason")
    source_day = day if close and reason == "same_day_not_close_confirmed" else prior
    if (signal.get("fear_data_status") != expected_status or signal.get(revision_field) != revision
            or signal.get("fear_fallback_applied") is not True
            or reason not in ({"same_day_not_published", "same_day_not_close_confirmed"} if close else {"same_day_not_published"})
            or signal.get("close_confirmed") is not close
            or any(signal.get(field) != prior.isoformat() for field in (
                "fear_data_date", "fear_previous_exchange_date"))
            or any(signal.get(field) != source_day.isoformat() for field in ("fear_latest_available_date", "fear_page_data_date"))):
        raise ValueError("Fear fallback revision/date/mode evidence invalid")
    value = signal.get("fear_greed_index")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("Fear fallback score invalid")
    if signal.get("fear_source_url") != "https://baifenwei.com/data/fear-greed/fear-greed-full.csv" or re.fullmatch(
        r"[0-9a-f]{64}", str(signal.get("fear_csv_sha256", ""))
    ) is None:
        raise ValueError("Fear fallback source/hash invalid")
    stamps = {}
    beijing = ZoneInfo("Asia/Shanghai")
    for field in ("fear_retrieved_at", "fear_page_updated_at"):
        stamp = datetime.fromisoformat(str(signal.get(field)))
        if stamp.tzinfo is None:
            raise ValueError("Fear fallback time lacks timezone")
        stamps[field] = stamp.astimezone(beijing)
    retrieved, updated = stamps["fear_retrieved_at"], stamps["fear_page_updated_at"]
    if updated > retrieved or updated <= datetime.combine(prior, time(15), tzinfo=beijing):
        raise ValueError("Fear fallback page lacks prior-session close evidence")
    if close:
        if reason == "same_day_not_close_confirmed" and (updated.date() != day or updated > datetime.combine(day, time(15), tzinfo=beijing)):
            raise ValueError("qualified T close evidence cannot be discarded for carried Fear")
        if retrieved.date() < day or (retrieved.date() == day and retrieved.time() <= time(15)):
            raise ValueError("Fear close fallback was not acquired after signal close")
        timing = "retrospective_replay" if retrieved.date() > day else "same_day_post_close"
        if signal.get("fear_fallback_timing") != timing:
            raise ValueError("Fear fallback replay timing invalid")
        if timing == "retrospective_replay":
            generated = datetime.fromisoformat(str(signal.get("signal_generated_at")))
            if (generated.tzinfo is None or generated.astimezone(beijing) < retrieved
                    or signal.get("signal_generation_timing") != "retrospective_replay"):
                raise ValueError("Fear fallback replay must retain real signal generation time")
    elif retrieved.date() != day or retrieved.time() >= time(15):
        raise ValueError("Fear intraday fallback was not acquired on T before 15:00")


def quantity(value: Any, label: str, *, signed: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} requires a finite numeric quantity")
    if not signed and value < 0:
        raise ValueError(f"{label} quantity must be nonnegative")
    return float(value)


def _same(left: float, right: float, label: str) -> None:
    if not math.isclose(left, right, abs_tol=1e-9, rel_tol=1e-9):
        raise ValueError(f"{label} quantity formula does not match")


def validate_quantities(product: str, signal: dict[str, Any]) -> None:
    """Check recorded quantities; never infer missing optional archive fields.

    Sparse older presentation payloads can omit a complete sleeve table.
    Every present quantity is checked, and every complete table is reconciled.
    Momentum weights have no invented upper cap: 0.5 * weight is the producer
    conversion, including legitimate weights above one.
    """
    signed_fields = {"total_units_change", "momentum_units_change"}
    fields = {
        "core_units_current", "core_units_target", "momentum_units_current",
        "momentum_units_target", "total_units_current", "total_units_target",
        "momentum_current_weight", "momentum_next_weight", "grid_current", "grid_target",
        "v14_short_put_qty_normalized", "v14_core_put_qty", *signed_fields,
        "v14_profit_old_qty", "v14_profit_reentry_qty",
    }
    if product == "IC":
        fields.update(f"put_{when}_{leg}_qty" for when in ("current", "target")
                      for leg in ("core", "momentum", "grid", "total"))
    else:
        fields.update(f"{leg}_put_{when}_qty_normalized" for when in ("current", "target")
                      for leg in ("core", "momentum", "total"))
    # Current fix11 producers always emit the complete table.  Deleting a leg
    # must not turn its sum check into an archive compatibility shortcut.
    if date.fromisoformat(signal["market_date"]) >= date(2026, 10, 8):
        missing = fields - signal.keys()
        if missing:
            raise ValueError(f"{product} current producer lacks required quantity fields: {sorted(missing)}")
    for field in fields:
        if field in signal:
            quantity(signal[field], f"{product} {field}", signed=field in signed_fields)
    for when, weight_field in (("current", "momentum_current_weight"), ("target", "momentum_next_weight")):
        momentum = f"momentum_units_{when}"
        if momentum in signal:
            _same(signal[momentum], .5 * signal[weight_field], f"{product} {momentum}")
        legs = (f"core_units_{when}", momentum, f"grid_{when}")
        if all(field in signal for field in legs):
            _same(signal[f"total_units_{when}"], sum(signal[field] for field in legs), f"{product} futures {when} total")
        if product == "IC":
            put_legs = [f"put_{when}_{leg}_qty" for leg in ("core", "momentum", "grid")]
            total = f"put_{when}_total_qty"
            grid = f"put_{when}_grid_qty"
            if grid in signal and signal[grid] != 0:
                raise ValueError("IC grid has no Put protection quantity")
        else:
            put_legs = [f"{leg}_put_{when}_qty_normalized" for leg in ("core", "momentum")]
            total = f"total_put_{when}_qty_normalized"
        if total in signal and all(field in signal for field in put_legs):
            _same(signal[total], sum(signal[field] for field in put_legs), f"{product} Put {when} total")
    for sleeve in ("total", "momentum"):
        field = f"{sleeve}_units_change"
        if field in signal and all(f"{sleeve}_units_{when}" in signal for when in ("current", "target")):
            _same(signal[field], signal[f"{sleeve}_units_target"] - signal[f"{sleeve}_units_current"], f"{product} {field}")


def _day(value: Any, label: str) -> date:
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError(f"{label} requires ISO date")
    try:
        day = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} requires ISO date") from exc
    if not is_session(day):
        raise ValueError(f"{label} is not an exchange session")
    return day


def validate_pending_plans(product: str, signal: dict[str, Any]) -> None:
    if date.fromisoformat(signal["market_date"]) >= date(2026, 10, 8):
        required = {
            "v14_route_state", "v14_ordinary_put_pending", "v14_profit_pending",
            "v14_roll_pending", "v14_settlement_pending", "v14_profit_trigger_day",
            "v14_profit_execution_day", "v14_profit_old_contract", "v14_profit_reentry_contract",
        }
        if required - signal.keys():
            raise ValueError(f"{product} current producer lacks required pending-plan state")
    plan = signal.get("v14_ordinary_put_pending")
    scheduled = signal.get("v14_ordinary_put_plan_status") == "scheduled_t_plus_1_open"
    if (plan is not None) != scheduled:
        raise ValueError(f"{product} ordinary Put pending/status mismatch")
    for flag in ("v14_profit_pending", "v14_roll_pending", "v14_settlement_pending"):
        if flag in signal and type(signal[flag]) is not bool:
            raise ValueError(f"{product} {flag} must be boolean")
    route = signal.get("v14_route_state", "future")
    if route not in {"future", "short_put", "assigned_etf", "cash_wait", "recovery_future"}:
        raise ValueError(f"{product} core route state is invalid")
    for kind in ("roll", "settlement"):
        if signal.get(f"v14_{kind}_pending"):
            if route != "short_put":
                raise ValueError(f"{product} {kind} pending requires the short-Put route")
            trigger = _day(signal.get(f"v14_{kind}_trigger_day"), f"{product} {kind} trigger")
            if trigger > _day(signal.get("market_date"), f"{product} signal day"):
                raise ValueError(f"{product} {kind} trigger cannot be later than its signal")
    if plan is not None:
        day = date.fromisoformat(signal["market_date"])
        replay = (signal.get("signal_generation_timing") == "retrospective_replay"
                  or signal.get("fear_data_status") == "retrospective_replay"
                  or signal.get("fear_fallback_timing") == "retrospective_replay"
                  or any(datetime.fromisoformat(str(signal[field])).astimezone(ZoneInfo("Asia/Shanghai")).date() > day
                         for field in ("fear_retrieved_at", "signal_generated_at")
                         if signal.get(field) is not None))
        if (day >= date(2026, 10, 14) and replay
                and plan.get("signal_day") == signal.get("market_date")):
            if signal.get("signal_generation_timing") != "retrospective_replay":
                raise ValueError("replayed ordinary Put plan cannot erase real replay identity")
            if plan.get("creation_timing") != "retrospective_replay" or plan.get("research_scope") != "retrospective_model_plan":
                raise ValueError("replayed ordinary Put plan lacks creation identity")
            stamps = [datetime.fromisoformat(str(value)) for value in (
                signal.get("fear_retrieved_at"), plan.get("created_at"), signal.get("signal_generated_at"))]
            if (any(stamp.tzinfo is None for stamp in stamps)
                    or not stamps[0] <= stamps[1] <= stamps[2] <= _validation_clock()):
                raise ValueError("replayed ordinary Put plan creation is outside actual collection interval")
        if signal.get("option_monthly_reset_due"):
            raise ValueError(f"{product} ordinary Put plan conflicts with monthly maintenance")
        if (route != "future" or signal.get("v14_profit_pending")
                or signal.get("v14_action") in {"CORE_PUT_PROFIT3X_REENTER", "EXECUTE_CORE_PUT_PROFIT3X_REENTER"}):
            legs = plan.get("legs") if isinstance(plan, dict) else None
            core = legs.get("core") if isinstance(legs, dict) else None
            if not isinstance(core, dict) or core.get("changed") is not False:
                raise ValueError(f"{product} ordinary Put plan cannot change a route/3x-owned core")
            # The digest's per-leg gate binds both unchanged core identities,
            # security IDs and quantities to the appropriate held stage.
    if signal.get("v14_profit_pending"):
        if route != "future":
            raise ValueError(f"{product} 3x plan requires the future core route")
        maintenance_premium = signal.get("v14_core_put_target_entry_premium")
        # The producer retains an auditable unresolved plan if maintenance
        # has no price.  A completed priced monthly reset supersedes it.
        if (signal.get("option_monthly_reset_due") and signal.get("close_confirmed") is True
                and isinstance(maintenance_premium, (int, float))
                and not isinstance(maintenance_premium, bool)
                and math.isfinite(maintenance_premium) and maintenance_premium > 0):
            raise ValueError(f"{product} priced monthly maintenance supersedes the 3x pending plan")
        trigger = _day(signal.get("v14_profit_trigger_day"), f"{product} 3x trigger")
        execution = _day(signal.get("v14_profit_execution_day"), f"{product} 3x execution")
        if trigger > _day(signal.get("market_date"), f"{product} signal day"):
            raise ValueError(f"{product} 3x trigger cannot be later than its signal")
        if execution != adjacent_session(trigger):
            raise ValueError(f"{product} 3x execution must be the next common session")
        if trigger >= date(2026, 9, 28):
            for side in ("old", "reentry"):
                field = f"v14_profit_{side}_qty"
                if quantity(signal.get(field), f"{product} {field}") <= 0:
                    raise ValueError(f"{product} 3x quantity must be positive")
                if not signal.get(f"v14_profit_{side}_contract"):
                    raise ValueError(f"{product} 3x plan lacks {side} contract")
                if product == "IC" and not signal.get(f"v14_profit_{side}_security_id"):
                    raise ValueError("IC 3x plan lacks security id")
    elif signal.get("v14_profit_execution_day") is not None:
        raise ValueError(f"{product} 3x execution day lacks pending trigger")



SOURCE_POLICY_REVISION = "baifenwei_either_source_20261016_v1"
SOURCE_POLICY_DATE = date(2026, 10, 16)
BEIJING = ZoneInfo("Asia/Shanghai")
CSV_URL = "https://baifenwei.com/data/fear-greed/fear-greed-full.csv"
PAGE_URL = "https://baifenwei.com/indicator/fear-greed/"
CLOSE_FALLBACK_STATUS = "close_previous_session"
INTRADAY_FALLBACK_STATUS = "intraday_previous_session"

def flexible_source(signal):
    return signal.get("fear_source_policy_revision") == SOURCE_POLICY_REVISION

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
        if csv_day in {day, adjacent_session(day, -1)} and csv_day.isoformat() not in observations:
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
        prior = adjacent_session(day, -1)
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


def validate_fear_publication(payload: dict[str, Any]) -> None:
    """A missing feed cannot be promoted to a complete publication or marker."""
    signals = payload.get("signals") or {}
    missing = []
    for product, signal in signals.items():
        if not isinstance(signal, dict) or not flexible_source(signal) or signal.get("fear_greed_index") is not None:
            continue
        missing.extend([f"{product}.grid", f"{product}.total_futures"])
        if (signal.get("grid_action") != "UNCONFIRMED"
                or signal.get("signal_completeness") != "partial"
                or set(signal.get("incomplete_components") or []) != {"grid", "total_futures"}
                or signal.get("grid_target_confirmed") is not False
                or signal.get("total_units_target_confirmed") is not False
                or any(signal.get(key) is not None for key in (
                    "grid_target", "grid_entry_source_target", "grid_transition_reason",
                    "total_units_target", "total_units_change"))):
            raise ValueError("missing Fear must hide grid/total targets and publish UNCONFIRMED")
    if missing and (payload.get("status") != "partial" or payload.get("signal_completeness") != "partial"
                    or set(payload.get("incomplete_components") or []) != set(missing)):
        raise ValueError("missing Fear requires an exact partial publication manifest")
