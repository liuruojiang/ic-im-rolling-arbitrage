from __future__ import annotations

import asyncio
import sys
import threading
import types
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
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
    monkeypatch.setattr(
        bot,
        "fetch_live_price_quote",
        lambda _product: {
            "price": float(history.iloc[-1]),
            "source": "测试源",
            "source_date": date(2026, 8, 21),
            "source_time": "15:00:00",
        },
    )
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
        self.attachments: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def write(self, value):
        self.text.append(str(value))

    def attach_file(self, **kwargs):
        self.attachments.append(kwargs)


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
        "future_quote_source": "新浪财经",
        "future_quote_date": date(2026, 8, 21),
        "scheduled_roll_completed": True,
        "put_market": "Put行情",
        "next_core": "",
        "roll_execution_date": None,
        "roll_date": date(2026, 9, 18),
        "data_notes": ["a", "b", "行情源回退/不可用汇总：中金所官方超时"],
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
        "构建 v1.2-20260824-r14",
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
        "该估值对应的Put Delta值为 **0%**",
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
        "新浪财经，as-of 2026-08-21",
        "行情源回退/不可用汇总：中金所官方超时",
    ):
        assert fragment in output
    assert "张数看起来多" not in output
    assert "一张IC期货名义较大" not in output


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


@pytest.mark.parametrize(
    "product, expected_cagr, expected_max_drawdown",
    [
        ("IC", 0.24875657377745042, -0.17460391624320182),
        ("IM", 0.2785129392952679, -0.34443426662200605),
    ],
)
def test_embedded_v12_fixed_curves_are_complete_and_match_full_metrics(
    product, expected_cagr, expected_max_drawdown
):
    frame = bot.performance_frame(
        product, date(2015, 4, 16), date(2026, 8, 23), refresh_latest=False
    )
    metrics = bot.performance_metrics(frame)
    assert len(frame) == 2756
    assert frame.index[0].date() == date(2015, 4, 16)
    assert frame.index[-1].date() == date(2026, 8, 14)
    assert not frame["is_live"].any()
    assert metrics["cagr"] == pytest.approx(expected_cagr, abs=1e-12)
    assert metrics["max_drawdown"] == pytest.approx(expected_max_drawdown, abs=1e-12)


def test_recent_year_performance_returns_separate_ic_im_charts(monkeypatch):
    capture = _CaptureMessage()
    monkeypatch.setattr(bot.poe, "start_message", lambda: capture)
    monkeypatch.setattr(bot, "render_nav_drawdown_chart", lambda *_args: b"png")
    continuation_calls: list[tuple[str, date]] = []

    def fake_continuation(product: str, end: date) -> pd.DataFrame:
        continuation_calls.append((product, end))
        return pd.DataFrame(
            {
                "ret": [0.001],
                "benchmark_price": [8000.0 if product == "IC" else 7800.0],
                "is_live": [True],
                "is_transition": [True],
            },
            index=pd.DatetimeIndex(["2026-08-17"], name="date"),
        )

    monkeypatch.setattr(bot, "latest_continuation_frame", fake_continuation)
    intent = bot.classify_query("最近一年表现", date(2026, 8, 23))
    assert intent.route == "performance"
    assert intent.products == ("IC", "IM")

    bot.ICIMMainlinesBot()._handle_performance(intent)
    output = "".join(capture.text)
    assert "1.2 历史表现" in output
    assert "历史表现尚未对外开放" not in output
    assert "真实期货、期权和指数日行情实时续接" in output
    assert "历史段不会被改写" in output
    assert "续接已包含 **2026-08-21** 月换日" in output
    assert "不是实盘授权" in output
    assert "10.32%年化基差" in output
    assert "含未来信息" in output
    assert continuation_calls == [("IC", date(2026, 8, 23)), ("IM", date(2026, 8, 23))]
    assert len(capture.attachments) == 2
    assert {item["name"].split("_")[0] for item in capture.attachments} == {"ic", "im"}
    assert all(item["content_type"] == "image/png" for item in capture.attachments)


def test_v12_im_live_continuation_scales_options_to_half_core_sleeve():
    anchor = bot.LIVE_CONTINUATION_ANCHOR["IM"]
    assert anchor["put_equivalent_units"] == pytest.approx(0.75)
    assert anchor["call_equivalent_units"] == pytest.approx(0.5)
    assert anchor["last_verified_day"] == date(2026, 8, 21)


@pytest.mark.parametrize(
    "query",
    ["IC 2026-02-31至2026-03-01表现", "IC 2026-13月表现"],
)
def test_invalid_date_never_silently_falls_back_to_recent_year(query):
    with pytest.raises(ValueError, match="日期区间无效|月份无效"):
        bot.classify_query(query, date(2026, 8, 23))


def test_nan_live_price_is_rejected(monkeypatch):
    class Response:
        content = b'v="x~x~x~bad"'

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(bot.requests, "get", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(
        bot, "_request_json", lambda *_args, **_kwargs: {"data": {"f43": float("nan")}}
    )
    with pytest.raises(RuntimeError, match="有效实时价格"):
        bot.fetch_live_price("IC")


@pytest.mark.parametrize("bad_kind", ["nan", "stale"])
def test_live_proxy_rejects_nan_or_stale_history(monkeypatch, bad_kind):
    index = pd.bdate_range(end="2026-08-21", periods=260)
    history = pd.Series(np.linspace(6000.0, 8000.0, len(index)), index=index)
    if bad_kind == "nan":
        history.iloc[-121] = np.nan
    else:
        history.index = pd.bdate_range(end="2026-08-10", periods=len(history))
    monkeypatch.setattr(
        bot,
        "fetch_live_price_quote",
        lambda _product: {
            "price": 8000.0,
            "source": "测试源",
            "source_date": date(2026, 8, 21),
            "source_time": "15:00:00",
        },
    )
    monkeypatch.setattr(bot, "fetch_price_history", lambda _product: history.copy())
    expected = "NaN|过期"
    with pytest.raises(RuntimeError, match=expected):
        bot.live_proxy("IC", datetime(2026, 8, 23, 12, tzinfo=bot.BEIJING))


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_verified_821_market_date_remains_available_but_later_state_fails_closed(product):
    weekend = datetime(2026, 8, 23, 12, tzinfo=bot.BEIJING)
    bot._validate_signal_market_date(product, date(2026, 8, 21), weekend)
    later = datetime(2026, 8, 24, 15, 30, tzinfo=bot.BEIJING)
    with pytest.raises(RuntimeError, match="审计账本仅逐腿核验至.*暂停新增信号"):
        bot._validate_signal_market_date(product, date(2026, 8, 24), later)


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_first_unverified_trading_day_intraday_bridge_is_narrow(product):
    intraday = datetime(2026, 8, 24, 9, 42, tzinfo=bot.BEIJING)
    assert bot._validate_signal_market_date(
        product, date(2026, 8, 24), intraday, mode="intraday"
    ) is True
    with pytest.raises(RuntimeError, match="审计账本仅逐腿核验至"):
        bot._validate_signal_market_date(
            product, date(2026, 8, 24), intraday, mode="close"
        )
    after_close = datetime(2026, 8, 24, 15, 1, tzinfo=bot.BEIJING)
    with pytest.raises(RuntimeError, match="审计账本仅逐腿核验至"):
        bot._validate_signal_market_date(
            product, date(2026, 8, 24), after_close, mode="intraday"
        )
    next_day = datetime(2026, 8, 25, 9, 42, tzinfo=bot.BEIJING)
    with pytest.raises(RuntimeError, match="审计账本仅逐腿核验至"):
        bot._validate_signal_market_date(
            product, date(2026, 8, 25), next_day, mode="intraday"
        )


def test_intraday_bridge_current_legs_are_taken_from_verified_anchor():
    live = {
        "momentum_current_weight": 0.0,
        "momentum_current_source_date": date(2026, 8, 24),
        "grid_current_units": 1.0,
        "v12_current_put_delta": 1.0,
        "v12_current_core_put_delta": 0.5,
        "v12_current_momentum_put_delta": 0.5,
        "current_core_put_driver": "错误重算",
        "current_momentum_put_driver": "错误重算",
    }
    anchored = bot._apply_first_unverified_intraday_anchor("IC", live)
    assert anchored["momentum_current_weight"] == 1.0
    assert anchored["grid_current_units"] == 0.0
    assert anchored["v12_current_put_delta"] == 0.25
    assert anchored["v12_current_core_put_delta"] == 0.25
    assert anchored["v12_current_momentum_put_delta"] == 0.0
    assert anchored["state_anchor_day"] == date(2026, 8, 21)


def test_intraday_live_price_requires_same_day_timestamp(monkeypatch):
    history = pd.Series(
        np.linspace(6000.0, 8000.0, 260),
        index=pd.bdate_range(end="2026-08-21", periods=260),
    )
    monkeypatch.setattr(bot, "fetch_price_history", lambda _product: history.copy())
    monkeypatch.setattr(
        bot,
        "fetch_live_price_quote",
        lambda _product: {
            "price": 8010.0,
            "source": "陈旧源",
            "source_date": date(2026, 8, 21),
            "source_time": "15:00:00",
        },
    )
    with pytest.raises(RuntimeError, match="缺少2026-08-24可验证时间戳"):
        bot.live_proxy(
            "IC", datetime(2026, 8, 24, 9, 42, tzinfo=bot.BEIJING)
        )


def test_intraday_bridge_rejects_stale_auxiliary_source_day():
    with pytest.raises(RuntimeError, match="必须等于当日 2026-08-24"):
        bot._require_intraday_bridge_source_day(
            "IC期货", date(2026, 8, 21), date(2026, 8, 24), True
        )


def test_invalid_price_row_cannot_hide_conflicting_source_date():
    frame = pd.DataFrame(
        [
            {
                "instrument": contract,
                "lastprice": np.nan if contract == "IC2609" else 7000.0,
                "volume": 1.0,
                "position": 1.0,
                "source_date": date(2099, 1, 1)
                if contract == "IC2609"
                else date(2026, 8, 20),
            }
            for contract in ("IC2608", "IC2609", "IC2612", "IC2703")
        ]
    )
    with pytest.raises(RuntimeError, match="行情日期不一致"):
        bot._validate_quote_frame(
            frame,
            "IC",
            datetime(2026, 8, 20, 14, 0, tzinfo=bot.BEIJING),
            "测试源",
        )


def test_roll_requires_exact_next_listed_future_quote():
    quotes = pd.DataFrame(
        {
            "instrument": ["IC2608", "IC2612", "IC2703"],
            "lastprice": [7000.0, 7020.0, 7040.0],
        }
    )
    quotes.attrs["listed_instruments"] = [
        "IC2608",
        "IC2609",
        "IC2612",
        "IC2703",
    ]
    next_contract = bot._next_listed_future_contract(
        "IC", quotes, date(2026, 8, 21)
    )
    assert next_contract == "IC2609"
    with pytest.raises(RuntimeError, match="IC2609.*禁止跳到更远月份"):
        bot._require_listed_future_quote("IC", quotes, next_contract)


def test_weekend_query_uses_source_day_before_exact_roll_gate():
    quotes = pd.DataFrame(
        {
            "instrument": ["IC2608", "IC2612", "IC2703"],
            "lastprice": [7000.0, 7020.0, 7040.0],
        }
    )
    quotes.attrs["listed_instruments"] = [
        "IC2608",
        "IC2609",
        "IC2612",
        "IC2703",
    ]
    market_day = date(2026, 8, 21)
    active = bot.select_active_future("IC", quotes, market_day)
    assert active["instrument"] == "IC2608"
    next_contract = bot._next_listed_future_contract(
        "IC", quotes, bot._third_friday(2026, 8)
    )
    with pytest.raises(RuntimeError, match="IC2609.*禁止跳到更远月份"):
        bot._require_listed_future_quote("IC", quotes, next_contract)


def _mo_valid_subset_with_raw_listing() -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {
                "instrument": "MO2610-P-7200",
                "lastprice": 100.0,
                "volume": 10.0,
                "position": 20.0,
                "bprice": 99.0,
                "sprice": 101.0,
            },
            {
                "instrument": "MO2612-C-8500",
                "lastprice": 30.0,
                "volume": 10.0,
                "position": 20.0,
                "bprice": 29.0,
                "sprice": 31.0,
            },
        ]
    )
    frame.attrs["listed_instruments"] = [
        "MO2608-P-7200",
        "MO2608-C-8500",
        "MO2609-P-7200",
        "MO2609-C-8500",
        "MO2610-P-7200",
        "MO2610-C-8500",
        "MO2612-P-7200",
        "MO2612-C-8500",
        "MO2703-P-7200",
        "MO2703-C-8500",
        "MO2706-P-7200",
        "MO2706-C-8500",
    ]
    return frame


def test_mo_put_exact_listed_target_cannot_jump_to_priced_month():
    quotes = _mo_valid_subset_with_raw_listing()
    with pytest.raises(RuntimeError, match="MO2612-P-7200.*禁止跳月或跳行权价"):
        bot.select_im_put_for_reset(quotes, date(2026, 8, 21), 7600.0)


def test_d10_and_rescue_do_not_skip_unpriced_nearest_listed_expiry():
    quotes = _mo_valid_subset_with_raw_listing()
    assert (
        bot.select_im_call_d10(
            quotes, date(2026, 8, 21), 7600.0, date(2026, 9, 18)
        )
        is None
    )
    assert (
        bot.select_im_call_rescue(
            quotes,
            date(2026, 8, 21),
            7600.0,
            date(2026, 8, 21),
            8000.0,
        )
        is None
    )


def test_rescue_does_not_skip_unpriced_lowest_qualifying_strike():
    quotes = pd.DataFrame(
        [
            {
                "instrument": "MO2609-C-8500",
                "lastprice": 30.0,
                "volume": 10.0,
                "position": 20.0,
                "bprice": 29.0,
                "sprice": 31.0,
            }
        ]
    )
    quotes.attrs["listed_instruments"] = [
        "MO2609-C-8400",
        "MO2609-C-8500",
    ]
    assert (
        bot.select_im_call_rescue(
            quotes,
            date(2026, 8, 21),
            7600.0,
            date(2026, 8, 21),
            8000.0,
        )
        is None
    )


def test_cross_source_audit_rejects_different_asof_days():
    left = pd.DataFrame(
        {"instrument": ["IC2608", "IC2609"], "lastprice": [7000.0, 7010.0]}
    )
    right = left.copy()
    left.attrs.update(source="源A", source_date=date(2026, 8, 20))
    right.attrs.update(source="源B", source_date=date(2026, 8, 21))
    with pytest.raises(RuntimeError, match="as-of不一致"):
        bot._audit_quote_sources(left, right, "IC")


def test_stale_market_date_fails_closed_before_position_logic():
    later = datetime(2026, 9, 17, 15, 30, tzinfo=bot.BEIJING)
    with pytest.raises(RuntimeError, match="行情已过期.*暂停新增信号"):
        bot._validate_signal_market_date("IM", date(2026, 8, 21), later)


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_september_preview_cannot_emit_false_hold(monkeypatch, product):
    monkeypatch.setattr(
        bot, "live_proxy", lambda *_args, **_kwargs: {"history_date": date(2026, 9, 17)}
    )
    monkeypatch.setattr(
        bot,
        "fetch_cffex_quotes",
        lambda *_args, **_kwargs: pytest.fail("position logic must not run past audit horizon"),
    )
    with pytest.raises(RuntimeError, match="审计账本仅逐腿核验至.*暂停新增信号"):
        bot.build_live_trade_signal(
            product,
            datetime(2026, 9, 17, 15, 30, tzinfo=bot.BEIJING),
            mode="close",
        )


def test_signal_failure_pauses_new_targets_but_shows_last_verified_snapshot(monkeypatch):
    capture = _CaptureMessage()
    monkeypatch.setattr(bot.poe, "start_message", lambda: capture)
    monkeypatch.setattr(
        bot,
        "build_live_trade_signal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    bot.ICIMMainlinesBot()._handle_signal(("IC", "IM"), mode="intraday")
    output = "".join(capture.text)
    assert output.count("已暂停该品种的新增/调整信号") == 2
    assert output.count("最后逐腿核验快照（2026-08-21") == 2
    assert "14张 `510500P2612M07500`" in output
    assert "1.5张 `MO2612-P-7200`" in output
    assert "不生成下一交易日目标" in output


def test_cffex_archive_uses_https_and_enforces_download_limit(monkeypatch):
    calls: list[str] = []

    class Response:
        headers = {"Content-Length": "8"}

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def iter_content(chunk_size):
            assert chunk_size > 0
            yield b"PKsafe!!"

    def fake_get(url, **_kwargs):
        calls.append(url)
        return Response()

    bot._CFFEX_MONTH_CACHE.clear()
    monkeypatch.setattr(bot.requests, "get", fake_get)
    assert bot._cffex_month_archive(pd.Timestamp("2026-08-01")) == b"PKsafe!!"
    assert calls == ["https://www.cffex.com.cn/sj/historysj/202608/zip/202608.zip"]

    class OversizedResponse(Response):
        headers = {"Content-Length": str(bot.MAX_CFFEX_DOWNLOAD_BYTES + 1)}

    bot._CFFEX_MONTH_CACHE.clear()
    monkeypatch.setattr(bot.requests, "get", lambda *_args, **_kwargs: OversizedResponse())
    with pytest.raises(RuntimeError, match="超过下载上限"):
        bot._cffex_month_archive(pd.Timestamp("2026-08-01"))


def test_cffex_zip_member_limit_is_checked_before_csv_read(monkeypatch):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("20260821_1.csv", b"x" * 32)
    monkeypatch.setattr(bot, "_cffex_month_archive", lambda _month: buffer.getvalue())
    monkeypatch.setattr(bot, "MAX_CFFEX_ZIP_MEMBER_BYTES", 16)
    with pytest.raises(RuntimeError, match="单个成员超过安全上限"):
        bot.fetch_cffex_daily_marks(["IC2608"], date(2026, 8, 21), date(2026, 8, 21))


def test_chart_nav_and_benchmark_both_start_at_one():
    frame = bot.performance_frame(
        "IC", date(2025, 8, 14), date(2026, 8, 14), refresh_latest=False
    )
    assert frame["chart_nav"].iloc[0] == pytest.approx(1.0, abs=1e-15)
    assert frame["benchmark_nav"].iloc[0] == pytest.approx(1.0, abs=1e-15)


def test_performance_network_failure_degrades_to_frozen_history(monkeypatch):
    capture = _CaptureMessage()
    monkeypatch.setattr(bot.poe, "start_message", lambda: capture)
    monkeypatch.setattr(
        bot,
        "latest_continuation_frame",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    monkeypatch.setattr(bot, "render_nav_drawdown_chart", lambda *_args: b"png")
    intent = bot.classify_query("最近一年表现 IC", date(2026, 8, 23))
    bot.ICIMMainlinesBot()._handle_performance(intent)
    output = "".join(capture.text)
    assert "联网续接失败，已明确降级" in output
    assert "冻结历史终点 **2026-08-14**" in output
    assert "未外推、未伪造后续收益" in output
    assert len(capture.attachments) == 1


def test_network_budget_caps_individual_timeout():
    with bot._network_budget(1.0):
        timeout = bot._bounded_timeout(30.0)
    assert 0.25 <= timeout <= 1.0


def test_nested_network_budget_cannot_extend_parent_deadline():
    with bot._network_budget(0.8):
        with bot._network_budget(30.0):
            timeout = bot._bounded_timeout(30.0)
    assert 0.25 <= timeout <= 0.8


def test_signal_budget_gives_each_product_full_independent_allowance():
    assert bot.SIGNAL_NETWORK_BUDGET_SECONDS == 90.0
    assert bot._signal_product_network_budget(1) == 45.0
    assert bot._signal_product_network_budget(2) == 45.0
    with pytest.raises(ValueError, match="品种数必须为正"):
        bot._signal_product_network_budget(0)


def test_network_budget_is_request_local_across_concurrent_threads():
    barrier = threading.Barrier(2)

    def worker(seconds):
        with bot._network_budget(seconds):
            barrier.wait(timeout=2.0)
            return bot._bounded_timeout(30.0)

    with ThreadPoolExecutor(max_workers=2) as pool:
        short_future = pool.submit(worker, 0.8)
        long_future = pool.submit(worker, 5.0)
        short_timeout = short_future.result(timeout=3.0)
        long_timeout = long_future.result(timeout=3.0)

    assert 0.25 <= short_timeout <= 0.8
    assert 2.0 < long_timeout <= 5.0


def test_network_budget_is_request_local_across_async_contexts():
    async def scenario():
        short_ready = asyncio.Event()
        long_ready = asyncio.Event()

        async def worker(seconds, own_ready, other_ready):
            with bot._network_budget(seconds):
                own_ready.set()
                await asyncio.wait_for(other_ready.wait(), timeout=2.0)
                return bot._bounded_timeout(30.0)

        return await asyncio.gather(
            worker(0.8, short_ready, long_ready),
            worker(5.0, long_ready, short_ready),
        )

    short_timeout, long_timeout = asyncio.run(scenario())
    assert 0.25 <= short_timeout <= 0.8
    assert 2.0 < long_timeout <= 5.0


def test_2027_signal_fails_closed_before_any_network_fetch(monkeypatch):
    monkeypatch.setattr(
        bot,
        "live_proxy",
        lambda *_args, **_kwargs: pytest.fail("unsupported calendar must fail first"),
    )
    with pytest.raises(RuntimeError, match=r"2027年官方交易日历.*暂停"):
        bot.build_live_trade_signal(
            "IC",
            now=datetime(2027, 1, 4, 16, tzinfo=bot.BEIJING),
            mode="close",
        )


def test_2027_freshness_check_fails_without_calendar_rollback_loop():
    with pytest.raises(RuntimeError, match=r"2027年官方交易日历.*暂停"):
        bot._latest_completed_exchange_day(
            datetime(2027, 1, 4, 16, tzinfo=bot.BEIJING)
        )


def _standard_future_frame(
    source="测试源", prices=(7834.2, 7762.0, 7563.0, 7380.2)
):
    instruments = ["IC2608", "IC2609", "IC2612", "IC2703"]
    raw = pd.DataFrame(
        {
            "instrument": instruments,
            "lastprice": list(prices),
            "bprice": [price - 0.2 for price in prices],
            "sprice": [price + 0.2 for price in prices],
            "volume": [100, 80, 60, 40],
            "position": [200, 160, 120, 80],
            "source_date": [date(2026, 8, 21)] * 4,
            "source_time": ["15:00:00"] * 4,
        }
    )
    return bot._validate_quote_frame(
        raw,
        "IC",
        datetime(2026, 8, 23, 12, tzinfo=bot.BEIJING),
        source,
    )


def test_low_remaining_budget_skips_official_and_reaches_sina_fallback(monkeypatch):
    clock = datetime(2026, 8, 23, 12, tzinfo=bot.BEIJING)
    calls = []

    monkeypatch.setattr(bot, "_remaining_network_budget", lambda: 9.0)
    monkeypatch.setattr(
        bot,
        "_fetch_cffex_official_quotes",
        lambda *_args: pytest.fail("low budget must skip CFFEX official"),
    )

    def sina(_product, _clock):
        calls.append("新浪财经")
        return _standard_future_frame("新浪财经")

    monkeypatch.setattr(bot, "_fetch_sina_future_quotes", sina)
    monkeypatch.setattr(
        bot,
        "_fetch_eastmoney_future_quotes",
        lambda *_args: pytest.fail("successful Sina recovery should preserve budget"),
    )
    frame = bot.fetch_cffex_quotes("IC", clock)
    assert calls == ["新浪财经"]
    assert frame.attrs["source"] == "新浪财经"
    assert "跳过官方源" in "；".join(frame.attrs["source_failures"])


def test_official_source_cap_does_not_consume_sina_fallback_budget(monkeypatch):
    clock = datetime(2026, 8, 23, 12, tzinfo=bot.BEIJING)
    observed = {}

    def official(_product, _clock):
        observed["official_timeout"] = bot._bounded_timeout(30.0)
        raise TimeoutError("official stalled")

    def sina(_product, _clock):
        observed["sina_timeout"] = bot._bounded_timeout(30.0)
        return _standard_future_frame("新浪财经")

    monkeypatch.setattr(bot, "_fetch_cffex_official_quotes", official)
    monkeypatch.setattr(bot, "_fetch_sina_future_quotes", sina)
    monkeypatch.setattr(
        bot,
        "_fetch_eastmoney_future_quotes",
        lambda *_args: pytest.fail("successful Sina recovery should stop here"),
    )
    with bot._network_budget(45.0):
        frame = bot.fetch_cffex_quotes("IC", clock)
    assert observed["official_timeout"] <= 4.0
    assert 7.0 < observed["sina_timeout"] <= 8.0
    assert frame.attrs["source"] == "新浪财经"


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (lambda frame: frame.drop(columns="position"), "缺少标准列"),
        (
            lambda frame: frame.assign(instrument=["IC2609", "IC2609.BAD"]),
            "非法合约代码",
        ),
        (
            lambda frame: pd.concat(
                [frame.iloc[[0]], frame.iloc[[0]].assign(lastprice=np.nan)],
                ignore_index=True,
            ),
            "重复合约",
        ),
    ],
)
def test_quote_adapter_rejects_missing_illegal_or_duplicate_contracts(mutator, expected):
    base = pd.DataFrame(
        {
            "instrument": ["IC2609", "IC2612"],
            "lastprice": [7762.0, 7563.0],
            "volume": [100, 80],
            "position": [200, 160],
            "source_date": [date(2026, 8, 21)] * 2,
        }
    )
    with pytest.raises(RuntimeError, match=expected):
        bot._validate_quote_frame(
            mutator(base),
            "IC",
            datetime(2026, 8, 23, 12, tzinfo=bot.BEIJING),
            "恶意源",
        )


def test_incomplete_mo_fallback_chain_cannot_select_contract():
    rows = []
    for option_type in ("C", "P"):
        for strike in (7000, 7200, 7400):
            rows.append(
                {
                    "instrument": f"MO2612-{option_type}-{strike}",
                    "lastprice": 10.0,
                    "volume": 1,
                    "position": 1,
                    "source_date": date(2026, 8, 21),
                }
            )
    with pytest.raises(RuntimeError, match="备用链不完整.*应挂牌"):
        bot._validate_quote_frame(
            pd.DataFrame(rows),
            "MO",
            datetime(2026, 8, 23, 12, tzinfo=bot.BEIJING),
            "残缺备用源",
        )


def test_two_month_mo_chain_is_rejected_even_with_balanced_calls_and_puts():
    rows = []
    for month in ("2608", "2609"):
        for option_type in ("C", "P"):
            for strike in (7000, 7200, 7400):
                rows.append(
                    {
                        "instrument": f"MO{month}-{option_type}-{strike}",
                        "lastprice": 10.0,
                        "volume": 1,
                        "position": 1,
                        "source_date": date(2026, 8, 21),
                    }
                )
    with pytest.raises(RuntimeError, match=r"应挂牌.*MO2610.*MO2706"):
        bot._validate_quote_frame(
            pd.DataFrame(rows),
            "MO",
            datetime(2026, 8, 23, 12, tzinfo=bot.BEIJING),
            "两月残链",
        )


def test_future_source_with_four_far_months_cannot_replace_near_contracts():
    frame = pd.DataFrame(
        {
            "instrument": ["IC2706", "IC2709", "IC2712", "IC2803"],
            "lastprice": [7000.0, 6900.0, 6800.0, 6700.0],
            "volume": [1, 1, 1, 1],
            "position": [1, 1, 1, 1],
            "source_date": [date(2026, 8, 21)] * 4,
        }
    )
    with pytest.raises(RuntimeError, match=r"挂牌结构不完整.*IC2608"):
        bot._validate_quote_frame(
            frame,
            "IC",
            datetime(2026, 8, 23, 12, tzinfo=bot.BEIJING),
            "远月伪源",
        )


def test_expected_listed_month_sets_use_source_date_not_weekend_clock():
    as_of = date(2026, 8, 21)
    assert bot._expected_future_contracts("IC", as_of) == {
        "IC2608",
        "IC2609",
        "IC2612",
        "IC2703",
    }
    assert bot._expected_mo_months(as_of) == {
        "MO2608",
        "MO2609",
        "MO2610",
        "MO2612",
        "MO2703",
        "MO2706",
    }


def test_cross_source_future_contract_sets_must_match_exactly():
    preferred = _standard_future_frame("中金所官方")
    secondary = _standard_future_frame("新浪财经")
    secondary.loc[secondary["instrument"].eq("IC2703"), "instrument"] = "IC2706"
    with pytest.raises(RuntimeError, match="挂牌集合冲突"):
        bot._audit_quote_sources(preferred, secondary, "IC")


def _complete_mo_frame(source):
    rows = []
    for month in ("2608", "2609", "2610", "2612", "2703", "2706"):
        for option_type in ("C", "P"):
            for strike in (7000, 7200, 7400):
                rows.append(
                    {
                        "instrument": f"MO{month}-{option_type}-{strike}",
                        "lastprice": 10.0 + strike / 10_000,
                        "volume": 1,
                        "position": 1,
                        "source_date": date(2026, 8, 21),
                    }
                )
    return bot._validate_quote_frame(
        pd.DataFrame(rows),
        "MO",
        datetime(2026, 8, 23, 12, tzinfo=bot.BEIJING),
        source,
    )


def test_cross_source_mo_expiry_month_sets_must_match_exactly():
    preferred = _complete_mo_frame("中金所官方")
    secondary = _complete_mo_frame("东方财富")
    secondary["instrument"] = secondary["instrument"].str.replace(
        "MO2706", "MO2709", regex=False
    )
    with pytest.raises(RuntimeError, match="到期月集合冲突"):
        bot._audit_quote_sources(preferred, secondary, "MO")


def test_missing_official_last_modified_falls_back_and_keeps_explicit_clock(monkeypatch):
    clock = datetime(2026, 8, 23, 12, tzinfo=bot.BEIJING)
    seen = []

    def official(_product, received_clock):
        assert received_clock is clock
        raise RuntimeError("缺少可验证的Last-Modified行情时间")

    def sina(_product, received_clock):
        assert received_clock is clock
        seen.append("新浪")
        return _standard_future_frame("新浪财经")

    monkeypatch.setattr(bot, "_fetch_cffex_official_quotes", official)
    monkeypatch.setattr(bot, "_fetch_sina_future_quotes", sina)
    monkeypatch.setattr(
        bot,
        "_fetch_eastmoney_future_quotes",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("东财离线")),
    )
    frame = bot.fetch_cffex_quotes("IC", clock)
    assert seen == ["新浪"]
    assert frame.attrs["source"] == "新浪财经"
    assert frame.attrs["source_date"] == date(2026, 8, 21)
    assert "Last-Modified" in "；".join(frame.attrs["source_failures"])


def test_all_quote_source_failures_are_aggregated(monkeypatch):
    for name in (
        "_fetch_cffex_official_quotes",
        "_fetch_sina_future_quotes",
        "_fetch_eastmoney_future_quotes",
    ):
        monkeypatch.setattr(
            bot,
            name,
            lambda *_args, _name=name: (_ for _ in ()).throw(RuntimeError(_name)),
        )
    with pytest.raises(RuntimeError, match="所有行情源失败") as caught:
        bot.fetch_cffex_quotes(
            "IC", datetime(2026, 8, 23, 12, tzinfo=bot.BEIJING)
        )
    message = str(caught.value)
    assert "中金所官方" in message
    assert "新浪财经" in message
    assert "东方财富" in message


def test_severe_cross_source_future_price_conflict_fails_closed(monkeypatch):
    monkeypatch.setattr(
        bot,
        "_fetch_cffex_official_quotes",
        lambda *_args: _standard_future_frame(
            "中金所官方", (7834.2, 7762.0, 7563.0, 7380.2)
        ),
    )
    monkeypatch.setattr(
        bot,
        "_fetch_sina_future_quotes",
        lambda *_args: _standard_future_frame(
            "新浪财经", (9200.0, 9000.0, 8800.0, 8600.0)
        ),
    )
    monkeypatch.setattr(
        bot,
        "_fetch_eastmoney_future_quotes",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    with pytest.raises(RuntimeError, match="行情源严重冲突"):
        bot.fetch_cffex_quotes(
            "IC", datetime(2026, 8, 23, 12, tzinfo=bot.BEIJING)
        )


@pytest.mark.parametrize(
    "action", ["ROLL", "RESIZE_OR_ROLL", "CLOSE_CALL", "RESCUE_NEXT_LISTED"]
)
def test_missing_old_leg_quote_blocks_state_changing_action(action):
    with pytest.raises(RuntimeError, match=f"禁止继续{action}"):
        bot._require_existing_leg_quote("测试腿", "MO2610-P-6600", None, action)
    bot._require_existing_leg_quote("测试腿", "MO2610-P-6600", None, "HOLD")


def test_ic_reset_chain_stamp_must_match_etf_day():
    with pytest.raises(RuntimeError, match="新月份期权链与参考行情日期不一致"):
        bot._validate_chain_stamp_matches(
            "上交所510500新月份期权链",
            {"date": "2026-08-21", "time": "150000"},
            date(2026, 8, 20),
            datetime(2026, 8, 23, 12, tzinfo=bot.BEIJING),
        )


def test_sina_selected_mo_quote_conflict_fails_closed(monkeypatch):
    class Response:
        headers = {}
        encoding = "gbk"
        content = (
            'var hq_str_P_OP_mo2612P7200="1,350,900,901,2,1640,-1,7200,'
            + ",".join(["0"] * 24)
            + ',2026-08-21 15:00:00,0,,cn,sh000852,name,0,920,340,323";'
        ).encode("gbk")

        def raise_for_status(self):
            return None

    monkeypatch.setattr(bot.requests, "get", lambda *_args, **_kwargs: Response())
    with pytest.raises(RuntimeError, match="MO行情源严重冲突"):
        bot.verify_sina_option_quote(
            "MO2612-P-7200",
            date(2026, 8, 21),
            365.6,
            datetime(2026, 8, 23, 12, tzinfo=bot.BEIJING),
        )


@pytest.mark.parametrize("contract", ["XIC2609", "IC2609JUNK", "IC2609-P-7000"])
def test_contract_month_rejects_partial_or_noncanonical_matches(contract):
    with pytest.raises(ValueError, match="无法识别合约月份"):
        bot._contract_month(contract)
    assert bot._contract_month("IC2609") == (2026, 9)
    assert bot._contract_month("MO2612-P-7200") == (2026, 12)


def test_sina_selected_mo_quote_uses_injected_clock_for_future_date(monkeypatch):
    class Response:
        headers = {}
        encoding = "gbk"
        content = (
            'var hq_str_P_OP_mo2612P7200="1,350,365.6,366,2,1640,-1,7200,'
            + ",".join(["0"] * 24)
            + ',2026-08-22 15:00:00,0,,cn,sh000852,name,0,420,350,323";'
        ).encode("gbk")

        def raise_for_status(self):
            return None

    monkeypatch.setattr(bot.requests, "get", lambda *_args, **_kwargs: Response())
    with pytest.raises(RuntimeError, match="日期 2026-08-22 晚于北京时间"):
        bot.verify_sina_option_quote(
            "MO2612-P-7200",
            date(2026, 8, 21),
            365.6,
            datetime(2026, 8, 21, 16, tzinfo=bot.BEIJING),
        )
