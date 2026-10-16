"""Read-only monthly Put calendar reminders; never selects contracts or records fills."""
from datetime import date, datetime, timedelta
from collections.abc import Callable
from typing import Any

REVISION = "monthly_put_notice_20261016_v2"


def _day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _valid_maintenance_day(execution: date, is_session: Callable[[date], bool] | None) -> bool:
    """Independently verify the monthly node using an explicit session calendar.

    Without a calendar we accept only the unadjusted third Friday. A supplied
    official calendar, rather than a weekday guess, must justify any holiday
    postponement. Calendar failures never produce a maintenance instruction.
    """
    first = execution.replace(day=1)
    candidate = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
    if is_session is None:
        return execution == candidate
    try:
        for _ in range(32):
            if is_session(candidate) is True:
                return execution == candidate
            candidate += timedelta(days=1)
    except Exception:  # A display reminder must fail closed on calendar errors.
        return False
    return False


def reminder(product: str, signal: dict[str, Any], *, signal_day: Any = None,
             is_session: Callable[[date], bool] | None = None,
             historical: bool = False) -> str:
    """Use the producer's actual maintenance/next-session dates, not weekday guesses.

    A calendar reminder survives HOLD/zero targets or partial grid publication.
    It applies to monthly bought-Put maintenance, not short-Put decay/settlement.
    """
    if product not in {"IC", "IM"}:
        return ""
    day = _day(signal.get("market_date", signal_day))
    if signal_day is not None and day != _day(signal_day):
        return ""
    execution = _day(signal.get("put_monthly_reset_execution_date") if product == "IM"
                     else signal.get("option_monthly_reset_execution_date"))
    if day is None or execution is None or not _valid_maintenance_day(execution, is_session):
        return ""
    preview = signal.get("put_monthly_reset_preview") if product == "IM" else signal.get("option_monthly_reset_preview")
    historical = historical is True or signal.get("signal_generation_timing") == "retrospective_replay"
    if (signal.get("close_confirmed") is True and preview is True
            and execution == _day(signal.get("next_trade_date"))) and execution > day:
        relative = "明日" if execution == day + timedelta(days=1) else "下一交易日"
        when = f"信号日下一交易日（{execution}）" if historical else f"{relative}（{execution}）"
    elif execution == day and signal.get("close_confirmed") is False:
        when = f"信号日当日（{execution}）" if historical else f"今日（{execution}）"
    else:
        return ""
    prefix = "历史日历复核，非原日已发布提醒：" if historical else ""
    return (
        f"**⚠️ {prefix}{product} 月度Put展期换仓提醒：{when}为维护日，"
        f"需在 {execution} 当天收盘完成换仓，不顺延至下一交易日开盘。**\n\n"
        "具体换仓合约及数量需由用户在维护日盘尾按当时行情和策略配置自行计算判断；"
        "本提醒不提前锁定新合约，盘中候选仍须盘尾复核。"
        "对应Put配置为零时核对无需换仓，不因日历提醒新增仓位。"
        "即使常规仓位显示HOLD，也须单独核对这项月度维护。"
        "这是研究规则的日历提醒，不代表已换仓或账户成交。"
    )


def reminders(signals: dict[str, Any], *, signal_day: Any = None,
              is_session: Callable[[date], bool] | None = None,
              historical: bool = False) -> list[str]:
    return [notice for product in ("IC", "IM")
            if (notice := reminder(product, signals.get(product, {}), signal_day=signal_day,
                                   is_session=is_session, historical=historical))]
