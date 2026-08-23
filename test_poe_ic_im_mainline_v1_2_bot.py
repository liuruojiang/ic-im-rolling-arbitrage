from __future__ import annotations

import sys
import types
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import im_mainline_v1_2 as im_local
import poe_ic_im_mainline_v1_2_bot as bot


def test_poe_source_compiles_and_executes_without_file(monkeypatch):
    source = Path(bot.__file__).read_text(encoding="utf-8")
    compile("# Poe bootstrap\n" + source, "poe_v12.py", "exec")
    module = types.ModuleType("poe_v12_without_file")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile(source, "<poepython>", "exec"), module.__dict__)
    assert not hasattr(module, "__file__")
    assert hasattr(module, "ICIMMainlinesBot")


def test_v12_im_parent_targets_keep_three_put_momentum_floor_and_new_grid():
    low = bot.im_targets(1.70, -0.01)
    tier4 = bot.im_targets(2.40, 0.01)
    assert low["puts_per_core"] == 3
    assert low["valuation_puts_per_full_core"] == 0
    assert low["put_driver"] == "MOM120负动量下限"
    assert tier4["puts_per_core"] == 4
    assert tier4["put_driver"] == "估值档"
    assert bot.im_targets(1.59, 0.01)["grid_next"] is True
    assert bot.im_targets(2.00, 0.01)["grid_exit"] is True


def test_ic_put_driver_separates_valuation_from_mom120_floor():
    floor_driven = bot.ic_targets(1.85, -0.01)
    valuation_driven = bot.ic_targets(2.05, 0.01)
    assert floor_driven["valuation_tier_label"].startswith("基础观察档")
    assert floor_driven["valuation_put_delta"] == 0.0
    assert floor_driven["put_delta"] == 0.5
    assert floor_driven["put_driver"] == "MOM120负动量下限"
    assert valuation_driven["valuation_tier"] == 4
    assert valuation_driven["put_delta"] == 1.0
    assert valuation_driven["put_driver"] == "估值档"


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_v12_momentum_score_matches_a_share_v13_local_formula(product):
    index = pd.bdate_range("2025-01-02", periods=220)
    close = pd.Series(
        5000.0 * np.exp(np.linspace(0.0, 0.18, len(index)))
        * (1.0 + 0.01 * np.sin(np.arange(len(index)) / 7.0)),
        index=index,
    )
    rule = bot.MOMENTUM_RULES[product]
    expected = im_local.calc_bias_momentum(
        close,
        bias_ma=int(rule["ma"]),
        momentum_days=int(rule["days"]),
        linear_weight_end=float(rule["weight_end"]),
    )
    actual = bot.calc_v12_momentum_score(product, close)
    np.testing.assert_allclose(actual, expected, rtol=0.0, atol=1e-12, equal_nan=True)


def test_live_proxy_reconciles_current_and_next_momentum_and_put(monkeypatch):
    index = pd.bdate_range(end="2026-08-21", periods=260)
    history = pd.Series(np.linspace(6000.0, 8000.0, len(index)), index=index)
    monkeypatch.setattr(bot, "fetch_live_price", lambda _product: float(history.iloc[-1]))
    monkeypatch.setattr(bot, "fetch_price_history", lambda _product: history.copy())
    live = bot.live_proxy("IC", datetime(2026, 8, 23, 12, tzinfo=bot.BEIJING))
    assert live["momentum_current_weight"] in {0.0, 0.5, 1.0}
    assert live["momentum_next_weight"] in {0.0, 0.5, 1.0}
    assert live["v12_put_target_delta"] == pytest.approx(
        live["v12_core_put_delta"] + live["v12_momentum_put_delta"]
    )
    assert live["momentum_current_source_date"] == date(2026, 8, 20)
    assert live["momentum_signal_date"] == date(2026, 8, 21)


def test_ic_put_integer_breakdown_reconciles_exactly_to_total():
    split = bot._ic_put_quantity_breakdown(
        full_equivalent=20,
        absolute_delta=0.36,
        core_delta=0.2376,
        momentum_delta=0.1188,
        total_qty=20,
    )
    assert split["core"] == 13
    assert split["momentum"] == 7
    assert split["grid"] == 0
    assert split["core"] + split["momentum"] + split["grid"] == split["total"]


def test_ic_put_integer_breakdown_keeps_zero_sleeve_at_zero():
    split = bot._ic_put_quantity_breakdown(20, 0.36, 0.25, 0.0, total_qty=14)
    assert split["core"] == 14
    assert split["momentum"] == 0
    assert split["total"] == 14


class _CaptureMessage:
    def __init__(self):
        self.text: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def write(self, value):
        self.text.append(str(value))


def _fake_signal(product: str) -> dict:
    common = {
        "close_confirmed": True,
        "market_date": date(2026, 8, 21),
        "next_trade_date": date(2026, 8, 24),
        "total_units_current": 1.0,
        "total_units_target": 0.75,
        "total_units_change": -0.25,
        "core_current": f"{product}2609",
        "core_target": f"{product}2609",
        "core_action": "HOLD",
        "momentum_units_current": 0.5,
        "momentum_units_target": 0.25,
        "momentum_current_weight": 1.0,
        "momentum_next_weight": 0.5,
        "momentum_units_change": -0.25,
        "grid_current": 0,
        "grid_target": 0,
        "put_current": "当前Put",
        "put_target": "目标Put",
        "put_action": "HOLD",
        "call_current": "当前Call",
        "call_target": "目标Call",
        "call_action": "HOLD",
        "momentum_score": 1.2,
        "momentum_abs20": -0.01,
        "momentum_current_source_date": date(2026, 8, 20),
        "momentum_signal_date": date(2026, 8, 21),
        "index_price": 7000.0,
        "score": 1.8,
        "momentum_120": -0.05,
        "market_phase": "收盘后",
        "fetch_time": "2026-08-21 15:30:00",
        "future_last": 6990.0,
        "future_bid": 6989.0,
        "future_ask": 6991.0,
        "scheduled_roll_completed": True,
        "put_market": "Put行情",
        "next_core": "",
        "roll_execution_date": None,
        "roll_date": date(2026, 9, 18),
        "data_notes": ["a", "b"],
    }
    if product == "IC":
        common.update(
            {
                "core_put_target_delta": 0.25,
                "momentum_put_target_delta": 0.0,
                "total_put_target_delta": 0.25,
                "core_put_current_delta": 0.25,
                "momentum_put_current_delta": 0.0,
                "total_put_current_delta": 0.25,
                "valuation_tier_label": "基础观察档（低于1.900）",
                "valuation_put_delta": 0.0,
                "mom120_floor_delta": 0.5,
                "core_put_driver": "MOM120负动量下限",
                "momentum_put_driver": "估值处于基础观察档",
                "current_core_put_driver": "MOM120负动量下限",
                "current_momentum_put_driver": "估值处于基础观察档",
                "put_sizing_future_price": 7000.0,
                "put_sizing_future_multiplier": 200,
                "put_sizing_future_notional": 1_400_000.0,
                "put_sizing_etf_price": 7.0,
                "put_sizing_option_multiplier": 10_000,
                "put_sizing_etf_option_notional": 70_000.0,
                "put_sizing_full_equivalent_contracts": 20,
                "put_sizing_target_delta": 0.25,
                "put_sizing_option_abs_delta": 0.36,
                "put_sizing_raw_qty": 13.8889,
                "put_sizing_rounded_qty": 14,
                "put_current_total_qty": 14,
                "put_current_core_qty": 14,
                "put_current_momentum_qty": 0,
                "put_current_grid_qty": 0,
                "put_target_total_qty": 14,
                "put_target_core_qty": 14,
                "put_target_momentum_qty": 0,
                "put_target_grid_qty": 0,
                "put_sizing_signal_date": date(2026, 8, 21),
                "put_sizing_target_expiry_date": date(2026, 11, 21),
                "put_sizing_expiry": date(2026, 12, 23),
                "put_sizing_strike": 6.75,
            }
        )
    else:
        common.update(
            {
                "core_put_target_qty_normalized": 1.5,
                "core_put_current_qty_normalized": 1.5,
                "momentum_put_current_qty_normalized": 0.0,
                "absolute_valuation_tier_label": "基础观察档（低于2.450）",
                "relative_valuation_tier_label": "第1保护档",
                "valuation_puts_per_full_core": 1,
                "mom120_floor_puts_per_full_core": 3,
                "core_put_driver": "MOM120负动量下限",
                "put_sizing_signal_date": date(2026, 8, 21),
                "put_sizing_target_expiry_date": date(2026, 11, 21),
                "put_sizing_expiry": date(2026, 12, 18),
                "put_sizing_strike": 6600.0,
                "put_sizing_target_strike": 6650.0,
                "call_market": "Call行情",
                "call_otm": 0.10,
            }
        )
    return common


def test_signal_output_lists_each_leg_current_next_change_and_total(monkeypatch):
    capture = _CaptureMessage()
    monkeypatch.setattr(bot.poe, "start_message", lambda: capture)
    monkeypatch.setattr(bot, "build_live_trade_signal", lambda product, mode: _fake_signal(product))
    bot.ICIMMainlinesBot()._handle_signal(("IC", "IM"), mode="close")
    output = "".join(capture.text)
    for fragment in (
        "裸滚核心袖",
        "动量指引袖",
        "独立估值网格",
        "期货合计",
        "当前总期货：1倍",
        "下一交易日确认目标：0.75倍",
        "净变化：-0.25倍",
        "下一交易日：**2026-08-24**",
        "估值档位与Put决策链",
        "当前估值分 **1.800**",
        "核心袖",
        "动量袖",
        "MOM120负动量下限",
        "估值等级以上述档位文字为准",
        "Put按仓位来源拆分",
        "裸滚IC核心袖",
        "IC动量指引袖",
        "下一交易日共 **14张**",
        "裸滚核心袖 **14张**",
        "动量指引袖 **0张**",
        "网格 **0张**",
        "裸滚IM核心袖",
        "IM动量指引袖",
        "规范化小数张数不是可直接成交的半张合约",
    ):
        assert fragment in output


@pytest.mark.parametrize(
    "query, expected_mode", [("实时信号 IM", "intraday"), ("信号 IC", "close")]
)
def test_signal_routes_keep_explicit_live_and_close_modes(monkeypatch, query, expected_mode):
    calls: list[tuple[tuple[str, ...], str]] = []
    monkeypatch.setattr(bot.poe, "query", types.SimpleNamespace(text=query, attachments=[]))
    monkeypatch.setattr(
        bot.ICIMMainlinesBot,
        "_handle_signal",
        lambda _self, products, mode: calls.append((products, mode)),
    )
    bot.ICIMMainlinesBot()._run_impl()
    expected_product = ("IM",) if "IM" in query else ("IC",)
    assert calls == [(expected_product, expected_mode)]
