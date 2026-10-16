from contextlib import contextmanager
from copy import deepcopy
from datetime import date
import json
from pathlib import Path

import pytest

import ic_im_option_maintenance_notice as notice
import poe_ic_im_mainline_v1_4_bot as strategy
import run_ic_im_v1_4_github_digest as digest


def signal(product, *, intraday=False):
    return {
        "market_date": "2026-10-16" if intraday else "2026-10-15",
        "next_trade_date": "2026-10-19" if intraday else "2026-10-16",
        "close_confirmed": not intraday,
        "v14_action": "HOLD", "put_action": "HOLD",
        "option_monthly_reset_execution_date": "2026-10-16",
        "put_monthly_reset_execution_date": "2026-10-16",
        "option_monthly_reset_preview": not intraday,
        "put_monthly_reset_preview": not intraday,
        # Reminder is independent of already-recorded/zero/partial targets.
        "option_monthly_reset_due": False,
        "put_monthly_reset_already_recorded": True,
        "grid_target_confirmed": False,
    }


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("intraday", [False, True])
def test_hold_reminds_before_close_and_today_intraday_without_mutating(product, intraday):
    source = signal(product, intraday=intraday)
    before = deepcopy(source)
    text = notice.reminder(product, source)
    assert ("今日（2026-10-16）" if intraday else "明日（2026-10-16）") in text
    for phrase in ("盘尾", "自行计算判断", "2026-10-16 当天收盘", "不顺延", "HOLD", "不提前锁定", "不代表已换仓"):
        assert phrase in text
    assert "2026-10-19" not in text
    assert source == before


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("change", [
    {"market_date": "2026-10-16"},  # today's close is too late for a prospective instruction
    {"close_confirmed": False},  # prior-day intraday is not the requested prior close
    {"next_trade_date": "2026-10-19"},
    {"market_date": "bad"},
    {"close_confirmed": "True"},
    {"option_monthly_reset_preview": False, "put_monthly_reset_preview": False},
    {"option_monthly_reset_execution_date": None, "put_monthly_reset_execution_date": None},
])
def test_no_stale_or_nonmaintenance_reminders(product, change):
    item = signal(product)
    item.update(change)
    assert not notice.reminder(product, item)


def test_calendar_next_session_not_calendar_tomorrow_and_date_objects():
    item = signal("IC")
    item.update(market_date=date(2026, 6, 18), next_trade_date=date(2026, 6, 22),
                option_monthly_reset_execution_date=date(2026, 6, 22))
    text = notice.reminder("IC", item, is_session=strategy._is_exchange_trading_day)
    assert "下一交易日（2026-06-22）" in text and "明日" not in text
    assert not notice.reminder("IC", item)  # Holiday shift requires an actual calendar.


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("intraday", [False, True])
def test_wrong_monthly_date_rejected_despite_valid_session_dates(product, intraday):
    item = signal(product, intraday=intraday)
    item.update(market_date="2026-10-19" if intraday else "2026-10-16",
                next_trade_date="2026-10-20" if intraday else "2026-10-19",
                option_monthly_reset_execution_date="2026-10-19",
                put_monthly_reset_execution_date="2026-10-19")
    assert not notice.reminder(product, item)
    assert not notice.reminder(product, item, is_session=strategy._is_exchange_trading_day)


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_official_calendar_rejects_unshifted_holiday_and_errors(product):
    item = signal(product, intraday=True)
    item.update(market_date="2026-06-19", option_monthly_reset_execution_date="2026-06-19",
                put_monthly_reset_execution_date="2026-06-19")
    assert not notice.reminder(product, item, is_session=strategy._is_exchange_trading_day)
    def unavailable(day):
        raise RuntimeError("calendar unavailable")
    assert not notice.reminder(product, signal(product), is_session=unavailable)


@pytest.mark.parametrize("intraday", [False, True])
def test_report_replay_context_forces_historical_legs_without_mutation(intraday):
    items = {p: signal(p, intraday=intraday) for p in ("IC", "IM")}
    before = deepcopy(items)
    rendered = notice.reminders(items, historical=True,
                                is_session=strategy._is_exchange_trading_day)
    assert len(rendered) == 2
    assert all("非原日已发布提醒" in text and "明日（" not in text and "今日（" not in text
               for text in rendered)
    assert items == before


def test_replay_is_explicitly_historical():
    item = signal("IM")
    item["signal_generation_timing"] = "retrospective_replay"
    text = notice.reminder("IM", item)
    assert "历史日历复核，非原日已发布提醒" in text
    assert "明日" not in text


def test_report_context_cannot_relabel_an_old_signal_as_today():
    assert not notice.reminder("IM", signal("IM"), signal_day="2026-10-16")


def test_stored_close_notice_precedes_hold_tables_without_changing_ledger():
    latest = {"verified_day": "2026-10-15", "signals": {p: signal(p) for p in ("IC", "IM")}}
    before = deepcopy(latest)
    report = digest.render_stored_close_report(latest)
    assert report.count("月度Put展期换仓提醒") == 2
    assert report.index("月度Put展期换仓提醒") < report.index("## IC\n")
    assert latest == before


@pytest.mark.parametrize("intraday", [False, True])
def test_poe_notice_precedes_current_and_next_targets(monkeypatch, intraday):
    fixture = Path(__file__).parent / "tests/fixtures/icim_fear_unpublished_20261014.json"
    signals = json.loads(fixture.read_text(encoding="utf-8"))["signals"]
    for product, item in signals.items():
        item.update(signal(product, intraday=intraday))
        item["market_date"] = date.fromisoformat(item["market_date"])
    chunks = []

    class Capture:
        @contextmanager
        def start_message(self):
            class Message:
                def write(self, value):
                    chunks.append(str(value))
            yield Message()

    monkeypatch.setattr(strategy, "poe", Capture())
    monkeypatch.setattr(strategy, "build_live_signal_with_transport_retry", lambda p, *args: deepcopy(signals[p]))
    monkeypatch.setattr(strategy, "_SIGNAL_OBSERVER", None)
    strategy.ICIMMainlinesBot()._handle_signal(("IC", "IM"), "intraday" if intraday else "close")
    text = "".join(chunks)
    assert text.count("月度Put展期换仓提醒") == 2
    assert text.index("月度Put展期换仓提醒") < text.index("当前总期货")
