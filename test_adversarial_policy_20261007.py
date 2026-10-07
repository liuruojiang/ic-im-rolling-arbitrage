"""Synthetic implementation boundaries; these are not market performance tests."""

from datetime import date, datetime

import pandas as pd
import pytest

import ic_im_v1_4_policy as policy
import poe_ic_im_mainline_v1_4_bot as bot


def _signal(product="IM"):
    return {
        "product": product, "market_date": date(2026, 10, 8),
        "next_trade_date": date(2026, 10, 9), "close_confirmed": True,
        "valuation_tier": 1, "valuation_puts_per_full_core": 1,
        "momentum_120": .02, "momentum_next_weight": 1.,
        "option_monthly_reset_due": False, "core_units_current": .5,
        "core_units_target": .5, "total_units_current": 1.,
        "total_units_target": 1., "momentum_put_target_delta": .25,
        "put_target_momentum_qty": 0, "momentum_put_target_qty_normalized": 0,
    }


def test_ic_reset_uses_existing_chain_failover(monkeypatch):
    """A valid backup exists, so an SSE outage must not abort monthly selection."""
    chain = pd.DataFrame([{"contract": "510500P2701M07500", "last": .20, "strike": 7.5,
                           "source_stamp": datetime(2026, 10, 8, 15, tzinfo=bot.BEIJING)}])
    stamp = {"date": "20261008", "time": "150000", "source": "Sina"}
    monkeypatch.setattr(bot, "fetch_sse_510500_expiries", lambda: ["202701"])
    monkeypatch.setattr(bot, "fetch_sse_510500_chain",
                        lambda month: (_ for _ in ()).throw(TimeoutError("SSE outage")))
    monkeypatch.setattr(bot, "fetch_sina_510500_chain", lambda month: (chain, stamp))
    monkeypatch.setattr(bot, "fetch_sina_510500_security_id", lambda contract: "10012357")
    monkeypatch.setattr(bot, "_gov10y_for_day", lambda *args: .02)
    with bot.runtime_clock(datetime(2026, 10, 8, 16, tzinfo=bot.BEIJING)):
        selected = bot.select_ic_put_for_reset(date(2026, 10, 8), 7.83, 7641., .25)
    assert selected["contract"] == "510500P2701M07500"
    assert selected["stamp"]["source"] == "Sina"


def test_ic_reset_rejects_stale_selected_row_in_current_chain(monkeypatch):
    chain = pd.DataFrame([
        {"contract": "510500P2701M07500", "last": .20, "strike": 7.5,
         "source_stamp": datetime(2026, 10, 7, 15, tzinfo=bot.BEIJING)},
        {"contract": "510500P2701M07250", "last": .10, "strike": 7.25,
         "source_stamp": datetime(2026, 10, 8, 15, tzinfo=bot.BEIJING)},
    ])
    stamp = {"date": "20261008", "time": "150000", "source": "Sina"}
    monkeypatch.setattr(bot, "fetch_sse_510500_expiries", lambda: ["202701"])
    monkeypatch.setattr(bot, "fetch_sse_510500_chain", lambda month: (chain, stamp))
    monkeypatch.setattr(bot, "fetch_sina_510500_security_id", lambda contract: "10012357")
    monkeypatch.setattr(bot, "_gov10y_for_day", lambda *args: .02)
    with bot.runtime_clock(datetime(2026, 10, 8, 16, tzinfo=bot.BEIJING)):
        with pytest.raises(RuntimeError, match="报价日期|当日|收盘"):
            bot.select_ic_put_for_reset(date(2026, 10, 8), 7.83, 7641., .25)


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
@pytest.mark.parametrize("field", ["v14_profit_old_qty", "v14_profit_reentry_qty"])
def test_profit_plan_quantities_must_be_finite(value, field):
    anchor = policy.default_extension("IM")
    anchor.update(v14_profit_pending=True, v14_profit_trigger_day=date(2026, 10, 8),
                  v14_profit_execution_day=date(2026, 10, 9),
                  v14_profit_old_contract="MO2612-P-7500", v14_profit_old_qty=1.5,
                  v14_profit_reentry_contract="MO2612-P-7400", v14_profit_reentry_qty=1.5)
    anchor[field] = value
    with pytest.raises(RuntimeError, match="quantity|quantities|数量"):
        policy.validate_extension("IM", anchor)


def test_candidate_failure_does_not_hide_completed_recovery_transition(monkeypatch):
    anchor = policy.default_extension("IM")
    anchor.update(v14_route_state="recovery_future", v14_cycle_id="IM-test-cycle",
                  v14_recovery_net_pnl=-10., v14_last_event_id="previous-event")
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, "IM", anchor)
    monkeypatch.setattr(bot, "_v14_im_short_put_candidate",
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("chain unavailable")))
    signal = _signal()
    signal["v14_recovery_net_pnl_mark"] = 0.
    output = bot._apply_v14_live_policy("IM", signal, mo_quotes=pd.DataFrame())
    assert output["v14_route_state"] == "future"
    assert output["v14_action"] == "EXIT_RECOVERY_AT_BREAKEVEN"
    assert "EXIT_RECOVERY_AT_BREAKEVEN" in output["v14_last_event_id"]
