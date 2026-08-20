from __future__ import annotations

import base64
import hashlib
import io
import math
import sys
import types
import zipfile
import zlib
from datetime import date
from pathlib import Path

import numpy as np
import pytest
import requests
from hypothesis import example, given, settings
from hypothesis import strategies as st

import poe_ic_im_mainlines_v2_bot as bot


def test_poe_script_compiles_when_runtime_code_is_injected_before_it():
    source = Path(bot.__file__).read_text(encoding="utf-8")
    compile(
        "# simulated Poe bootstrap\nPOE_INJECTED = True\n" + source,
        "poe_injected.py",
        "exec",
    )


def test_poe_script_executes_when_runtime_does_not_define_file(monkeypatch):
    source = Path(bot.__file__).read_text(encoding="utf-8")
    runtime_module = types.ModuleType("poepython_without_file")
    monkeypatch.setitem(sys.modules, runtime_module.__name__, runtime_module)

    exec(compile(source, "<poepython>", "exec"), runtime_module.__dict__)

    assert not hasattr(runtime_module, "__file__")
    assert hasattr(runtime_module, "ICIMMainlinesBot")


@pytest.mark.parametrize(
    "query, route, products, start, end",
    [
        (
            "查询最近一年表现",
            "performance",
            ("IC", "IM"),
            date(2025, 8, 20),
            date(2026, 8, 20),
        ),
        ("最近一年", "performance", ("IC", "IM"), date(2025, 8, 20), date(2026, 8, 20)),
        (
            "查询最近3年",
            "performance",
            ("IC", "IM"),
            date(2023, 8, 20),
            date(2026, 8, 20),
        ),
        ("查看近五年 IM", "performance", ("IM",), date(2021, 8, 20), date(2026, 8, 20)),
        (
            "查询从2023年到2025年表现",
            "performance",
            ("IC", "IM"),
            date(2023, 1, 1),
            date(2025, 12, 31),
        ),
        (
            "SC 2022年至2024年",
            "performance",
            ("IC",),
            date(2022, 1, 1),
            date(2024, 12, 31),
        ),
        ("查询 SC 参数", "params", ("IC",), None, None),
        ("实时信号 IM", "intraday_signal", ("IM",), None, None),
        ("信号 IC", "close_signal", ("IC",), None, None),
        ("查询", "snapshot", ("IC", "IM"), None, None),
        (
            "净值曲线 IC 2025-01-01 到 2025-12-31",
            "performance",
            ("IC",),
            date(2025, 1, 1),
            date(2025, 12, 31),
        ),
    ],
)
def test_requested_natural_language_routes(query, route, products, start, end):
    intent = bot.classify_query(query, now=date(2026, 8, 20))
    assert intent.route == route
    assert intent.products == products
    assert intent.start == start
    assert intent.end == end


def test_performance_precedes_generic_signal_words():
    intent = bot.classify_query("实时信号 查询最近一年表现", now=date(2026, 8, 20))
    assert intent.route == "performance"


def test_explicit_parameter_wording_intentionally_precedes_signal_wording():
    intent = bot.classify_query("实时信号和参数", now=date(2026, 8, 20))
    assert intent.route == "params"


def test_reversed_year_range_is_normalized():
    intent = bot.classify_query("从2025年到2023年", now=date(2026, 8, 20))
    assert (intent.start, intent.end) == (date(2023, 1, 1), date(2025, 12, 31))


@pytest.mark.parametrize(
    "query, expected_start",
    [
        ("查询最近一百年表现", date(1926, 8, 20)),
        ("查询最近一百二十个月表现", date(2016, 8, 20)),
        ("查询最近两百零三个月表现", date(2009, 9, 20)),
        ("查询最近三年半表现", date(2023, 2, 20)),
        ("查询最近半年表现", date(2026, 2, 20)),
    ],
)
def test_chinese_period_numbers_are_parsed_consistently(query, expected_start):
    intent = bot.classify_query(query, now=date(2026, 8, 20))
    assert intent.route == "performance"
    assert intent.start == expected_start
    assert intent.end == date(2026, 8, 20)


def test_extreme_period_is_clamped_and_future_to_now_is_ordered():
    huge = bot.classify_query("最近99999个月", now=date(2026, 8, 20))
    future = bot.classify_query("2026年12月至今", now=date(2026, 8, 20))

    assert (huge.start, huge.end) == (date.min, date(2026, 8, 20))
    assert (future.start, future.end) == (date(2026, 8, 20), date(2026, 12, 1))


def test_sc_is_exact_ic_alias():
    assert bot.classify_query("查询SC参数") == bot.classify_query("查询IC参数")


@given(st.text(max_size=200))
@example("查询最近九百九十九年表现")
@example("最近99999个月")
@example("2026年12月至今")
@settings(max_examples=250, deadline=None)
def test_query_parser_never_crashes(text):
    intent = bot.classify_query(text, now=date(2026, 8, 20))
    assert intent.route in {
        "snapshot",
        "intraday_signal",
        "close_signal",
        "params",
        "performance",
    }
    if intent.start is not None:
        assert intent.start <= intent.end


@given(st.text(max_size=120))
@settings(max_examples=150, deadline=None)
def test_normalization_is_idempotent(text):
    once = bot.normalize_query(text)
    assert bot.normalize_query(once) == once


@given(st.sampled_from(["信号", "实时信号 IM", "查询 SC 参数", "查询最近一年表现"]))
@settings(max_examples=4, deadline=None)
def test_outer_whitespace_does_not_change_intent(query):
    now = date(2026, 8, 20)
    assert bot.classify_query(query, now) == bot.classify_query(
        f"  \n {query} \t ", now
    )


@pytest.mark.parametrize(
    "product, expected_rows, expected_cagr, expected_dd, expected_index_first, expected_index_last",
    [
        ("IC", 946, 0.3397479107427816, -0.1579485748983755, 5913.15, 7990.33),
        ("IM", 986, 0.3178445915615868, -0.1498462179185934, 7034.60, 7769.82),
    ],
)
def test_embedded_formal_returns_match_frozen_metrics(
    product,
    expected_rows,
    expected_cagr,
    expected_dd,
    expected_index_first,
    expected_index_last,
):
    series = bot.decode_returns(product)
    assert len(series) == expected_rows
    frame = bot.performance_frame(product, series.index.min().date(), bot.DATA_CUTOFF)
    metrics = bot.performance_metrics(frame)
    assert metrics["cagr"] == pytest.approx(expected_cagr, abs=1e-10)
    assert metrics["max_drawdown"] == pytest.approx(expected_dd, abs=1e-10)
    assert frame["benchmark_price"].iloc[0] == pytest.approx(expected_index_first)
    assert frame["benchmark_price"].iloc[-1] == pytest.approx(expected_index_last)
    assert frame["benchmark_nav"].iloc[0] == pytest.approx(1.0)


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_chart_is_valid_nontrivial_png(product):
    frame = bot.performance_frame(product, date(2025, 8, 20), bot.DATA_CUTOFF)
    image = bot.render_nav_drawdown_chart(product, frame, date(2025, 8, 20))
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(image) > 25_000


def test_ic_latest_continuation_uses_actual_future_and_etf_option_marks(monkeypatch):
    day = bot.pd.Timestamp("2026-08-17")
    marks = bot.pd.DataFrame(
        {"close": [8161.0], "settle": [8145.0], "pre_settle": [7949.8]},
        index=bot.pd.MultiIndex.from_tuples(
            [(day, "IC2608")], names=["date", "contract"]
        ),
    )
    requested_contracts = []

    def fake_marks(contracts, *_args):
        requested_contracts.extend(contracts)
        return marks

    monkeypatch.setattr(bot, "fetch_cffex_daily_marks", fake_marks)
    monkeypatch.setattr(
        bot,
        "fetch_price_history",
        lambda _product: bot.pd.Series([8184.64], index=[day]),
    )
    monkeypatch.setattr(
        bot,
        "fetch_sina_option_closes",
        lambda _security_id: bot.pd.Series(
            [0.0633, 0.0357],
            index=[bot.pd.Timestamp(bot.DATA_CUTOFF), day],
        ),
    )
    monkeypatch.setattr(
        bot,
        "fetch_sina_option_closes",
        lambda _security_id: bot.pd.Series(
            [0.0633, 0.0357],
            index=[bot.pd.Timestamp(bot.DATA_CUTOFF), day],
        ),
    )

    frame = bot.latest_continuation_frame("IC", date(2026, 8, 17))

    assert frame.index[-1].date() == date(2026, 8, 17)
    assert frame.loc[day, "ret"] == pytest.approx(0.020122199948429873)
    assert frame.loc[day, "benchmark_price"] == pytest.approx(8184.64)
    assert bool(frame.loc[day, "is_live"])
    assert requested_contracts == ["IC2608"]


def test_continuation_rejects_broken_formal_anchor(monkeypatch):
    day = bot.pd.Timestamp("2026-08-17")
    marks = bot.pd.DataFrame(
        {"close": [8161.0], "settle": [8145.0], "pre_settle": [7900.0]},
        index=bot.pd.MultiIndex.from_tuples(
            [(day, "IC2608")], names=["date", "contract"]
        ),
    )
    monkeypatch.setattr(bot, "fetch_cffex_daily_marks", lambda *_args: marks)
    monkeypatch.setattr(
        bot,
        "fetch_price_history",
        lambda _product: bot.pd.Series([8184.64], index=[day]),
    )

    with pytest.raises(RuntimeError, match="续接锚点不连续"):
        bot.latest_continuation_frame("IC", date(2026, 8, 17))


@pytest.mark.parametrize(
    "option_series, message",
    [
        (
            lambda day: bot.pd.Series([0.0357], index=[day]),
            "缺少正式段末日",
        ),
        (
            lambda day: bot.pd.Series(
                [0.0634, 0.0357],
                index=[bot.pd.Timestamp(bot.DATA_CUTOFF), day],
            ),
            "IC Put续接锚点不连续",
        ),
    ],
)
def test_ic_put_continuation_anchor_is_required(monkeypatch, option_series, message):
    day = bot.pd.Timestamp("2026-08-17")
    marks = bot.pd.DataFrame(
        {"close": [8161.0], "settle": [8145.0], "pre_settle": [7949.8]},
        index=bot.pd.MultiIndex.from_tuples(
            [(day, "IC2608")], names=["date", "contract"]
        ),
    )
    monkeypatch.setattr(bot, "fetch_cffex_daily_marks", lambda *_args: marks)
    monkeypatch.setattr(
        bot,
        "fetch_price_history",
        lambda _product: bot.pd.Series([8184.64], index=[day]),
    )
    monkeypatch.setattr(
        bot, "fetch_sina_option_closes", lambda _security_id: option_series(day)
    )

    with pytest.raises(RuntimeError, match=message):
        bot.latest_continuation_frame("IC", date(2026, 8, 17))


def test_missing_contract_marks_have_contextual_error():
    marks = bot.pd.DataFrame(
        {"settle": [1.0]},
        index=bot.pd.MultiIndex.from_tuples(
            [(bot.pd.Timestamp("2026-08-17"), "OTHER")],
            names=["date", "contract"],
        ),
    )
    with pytest.raises(RuntimeError, match="IM Put日行情缺少合约 MO2610-P-6600"):
        bot._contract_daily_marks(marks, "MO2610-P-6600", "IM", "Put")


def test_performance_rejects_overlapping_continuation_days(monkeypatch):
    duplicated_day = bot.pd.Timestamp(bot.DATA_CUTOFF)
    monkeypatch.setattr(
        bot,
        "latest_continuation_frame",
        lambda *_args: bot.pd.DataFrame(
            {
                "ret": [0.01],
                "benchmark_price": [8000.0],
                "is_live": [True],
            },
            index=[duplicated_day],
        ),
    )

    with pytest.raises(RuntimeError, match="重复交易日"):
        bot.performance_frame(
            "IC", date(2026, 8, 1), date(2026, 8, 20), refresh_latest=True
        )


def test_im_latest_continuation_uses_actual_option_settlements(monkeypatch):
    day = bot.pd.Timestamp("2026-08-17")
    marks = bot.pd.DataFrame(
        {
            "close": [7955.6, 38.0, 1.0],
            "settle": [7935.8, 38.0, 1.0],
            "pre_settle": [7740.6, 53.6, 0.8],
        },
        index=bot.pd.MultiIndex.from_tuples(
            [
                (day, "IM2608"),
                (day, "MO2610-P-6600"),
                (day, "MO2608-C-8800"),
            ],
            names=["date", "contract"],
        ),
    )
    monkeypatch.setattr(bot, "fetch_cffex_daily_marks", lambda *_args: marks)
    monkeypatch.setattr(
        bot,
        "fetch_price_history",
        lambda _product: bot.pd.Series([7968.38], index=[day]),
    )

    frame = bot.latest_continuation_frame("IM", date(2026, 8, 17))

    assert frame.loc[day, "ret"] == pytest.approx(0.021233879745098598)
    assert frame.loc[day, "benchmark_price"] == pytest.approx(7968.38)
    assert bool(frame.loc[day, "is_live"])


def test_target_boundary_rules():
    assert bot.ic_targets(1.90, 0.1)["put_delta"] == 0.25
    assert bot.ic_targets(1.95, 0.1)["put_delta"] == 0.50
    assert bot.ic_targets(2.00, 0.1)["put_delta"] == 0.75
    assert bot.ic_targets(2.05, 0.1)["put_delta"] == 1.00
    assert bot.ic_targets(1.80, -0.01)["put_delta"] == 0.50
    assert bot.ic_targets(1.80, 0.00)["put_delta"] == 0.00
    assert bot.im_targets(2.00, 0.1)["puts_per_core"] == 1
    assert bot.im_targets(1.00, -0.01)["puts_per_core"] == 4
    assert bot.im_targets(1.00, 0.00)["puts_per_core"] == 0
    assert bot.im_targets(0.85, 0.1)["grid_next"] is True


def test_zero_ic_delta_selects_no_put_without_fetching_a_chain(monkeypatch):
    monkeypatch.setattr(
        bot,
        "fetch_sse_510500_expiries",
        lambda: pytest.fail("zero target must not fetch an option chain"),
    )

    selected = bot.select_ic_put_for_reset(date(2026, 8, 21), 7.86, 7760.0, 0.0)

    assert selected["qty"] == 0
    assert selected["contract"] is None
    assert selected["quote"] is None


@pytest.mark.parametrize("target", [-0.01, 1.01])
def test_ic_delta_target_outside_unit_interval_is_rejected(target):
    with pytest.raises(ValueError, match="超出"):
        bot.select_ic_put_for_reset(date(2026, 8, 21), 7.86, 7760.0, target)


@given(
    score=st.floats(min_value=-10, max_value=10, allow_nan=False, allow_infinity=False),
    momentum=st.floats(min_value=-2, max_value=2, allow_nan=False, allow_infinity=False),
)
@example(score=1.90, momentum=0.0)
@example(score=2.05, momentum=-0.0)
@settings(max_examples=250, deadline=None)
def test_target_functions_preserve_allowed_four_tier_ranges(score, momentum):
    ic = bot.ic_targets(score, momentum)["put_delta"]
    im = bot.im_targets(score, momentum)["puts_per_core"]
    assert ic in {0.0, 0.25, 0.50, 0.75, 1.00}
    assert im in {0, 1, 2, 3, 4}
    if momentum < 0:
        assert ic >= 0.50
        assert im == 4


@given(
    low=st.floats(min_value=-10, max_value=9.9, allow_nan=False, allow_infinity=False),
    step=st.floats(min_value=0, max_value=0.1, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=150, deadline=None)
def test_positive_momentum_targets_are_monotone_in_score(low, step):
    high = low + step
    assert bot.ic_targets(low, 0.1)["put_delta"] <= bot.ic_targets(high, 0.1)["put_delta"]
    assert bot.im_targets(low, 0.1)["puts_per_core"] <= bot.im_targets(high, 0.1)["puts_per_core"]


def test_live_proxy_uses_market_price_but_keeps_research_boundaries(monkeypatch):
    history = np.linspace(6500.0, 7900.0, 180)
    monkeypatch.setattr(bot, "fetch_live_price", lambda _product: 8000.0)
    monkeypatch.setattr(
        bot,
        "fetch_price_history",
        lambda _product: bot.pd.Series(
            history, index=bot.pd.date_range("2025-12-01", periods=180, freq="B")
        ),
    )
    result = bot.live_proxy("IM")
    assert result["price"] == 8000.0
    assert np.isfinite(result["score"])
    assert result["puts_per_core"] in {0, 1, 2, 3, 4}
    assert result["history_date"] == date(2026, 8, 7)


def _future_quotes(product):
    return bot.pd.DataFrame(
        [
            {
                "instrument": f"{product}2608",
                "lastprice": 7800.0,
                "bprice": 7799.0,
                "sprice": 7801.0,
            },
            {
                "instrument": f"{product}2609",
                "lastprice": 7750.0,
                "bprice": 7749.0,
                "sprice": 7751.0,
            },
        ]
    )


def test_active_future_rejects_only_expired_quotes():
    quotes = bot.pd.DataFrame(
        [{"instrument": "IC2607", "lastprice": 7700.0}]
    )
    with pytest.raises(RuntimeError, match="均已到期"):
        bot.select_active_future("IC", quotes, date(2026, 8, 20))


def test_2026_exchange_holiday_roll_and_market_phases():
    assert bot._third_friday(2026, 6) == date(2026, 6, 22)
    assert bot._roll_forward_exchange_day(date(2026, 10, 1)) == date(2026, 10, 8)
    assert bot._is_pre_expiry_close(
        date(2026, 6, 18), date(2026, 6, 22), "收盘后"
    )
    assert not bot._is_pre_expiry_close(
        date(2026, 6, 17), date(2026, 6, 22), "收盘后"
    )
    assert (
        bot._market_phase(bot.datetime(2026, 6, 19, 10, 0, tzinfo=bot.BEIJING))
        == "非交易日"
    )
    assert (
        bot._market_phase(bot.datetime(2026, 8, 20, 9, 27, tzinfo=bot.BEIJING))
        == "集合竞价"
    )
    assert (
        bot._market_phase(bot.datetime(2026, 8, 20, 9, 30, tzinfo=bot.BEIJING))
        == "盘中"
    )


def test_uncovered_listed_calendar_year_is_explicitly_reported():
    note = bot._calendar_coverage_note(
        [date(2026, 12, 18), date(2027, 3, 19)]
    )
    assert note is not None
    assert "2027年" in note
    assert "尚未载入该年度官方休市表" in note


def test_fully_covered_calendar_year_has_no_note():
    assert bot._calendar_coverage_note([date(2026, 6, 22), date(2026, 12, 18)]) is None


def test_missing_futures_bid_ask_are_rendered_as_missing():
    assert bot._format_quote_number(np.nan) == "缺失"
    assert bot._format_quote_number(float("inf")) == "缺失"
    assert bot._format_quote_number(-1.0) == "缺失"
    assert bot._format_quote_number(7799.0) == "7799.00"


def test_cffex_daily_marks_use_named_headers(monkeypatch):
    csv_text = (
        "合约代码,今开盘,最高价,最低价,成交量,成交金额,持仓量,持仓变化,今收盘,今结算,前结算\n"
        "IC2608,8000,8100,7950,100,10,200,5,8050,8040,7990\n"
    )
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("20260817_1.csv", csv_text.encode("gbk"))
    monkeypatch.setattr(bot, "_cffex_month_archive", lambda _month: payload.getvalue())

    frame = bot.fetch_cffex_daily_marks(
        ["IC2608"], date(2026, 8, 17), date(2026, 8, 17)
    )

    row = frame.loc[(bot.pd.Timestamp("2026-08-17"), "IC2608")]
    assert row["close"] == 8050.0
    assert row["settle"] == 8040.0
    assert row["pre_settle"] == 7990.0


def test_cffex_daily_marks_reject_duplicate_contract_rows(monkeypatch):
    csv_text = (
        "合约代码,今收盘,今结算,前结算\n"
        "IC2608,8050,8040,7990\n"
        "IC2608,8050,8040,7990\n"
    )
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("20260817_1.csv", csv_text.encode("gbk"))
    monkeypatch.setattr(bot, "_cffex_month_archive", lambda _month: payload.getvalue())

    with pytest.raises(RuntimeError, match="重复合约记录"):
        bot.fetch_cffex_daily_marks(
            ["IC2608"], date(2026, 8, 17), date(2026, 8, 17)
        )


def test_real_cffex_archive_named_fields_match_legacy_positions():
    archive_path = (
        Path(__file__).resolve().parent
        / "data"
        / "ic_monthly_discount_roll_v1"
        / "cffex_raw"
        / "202608.zip"
    )
    assert archive_path.is_file()
    assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == (
        "4b75710904a158d3c016f2ceb54681370c69d407a570139977ff7f1f8669f9ef"
    )
    with zipfile.ZipFile(archive_path) as archive:
        raw = bot.pd.read_csv(
            io.BytesIO(archive.read("20260814_1.csv")),
            encoding="gbk",
            low_memory=False,
        )
    row = raw.loc[
        raw["合约代码"].astype(str).str.strip().eq("IC2608")
    ].iloc[0]

    assert list(raw.columns[8:11]) == ["今收盘", "今结算", "前结算"]
    assert float(row["今收盘"]) == float(row.iloc[8]) == pytest.approx(7939.8)
    assert float(row["今结算"]) == float(row.iloc[9]) == pytest.approx(7949.8)
    assert float(row["前结算"]) == float(row.iloc[10]) == pytest.approx(7984.0)


def test_cffex_quotes_find_header_after_metadata_line(monkeypatch):
    response = requests.Response()
    response.status_code = 200
    response._content = (
        "2026-08-20 14:30:00\n"
        "instrument,openprice,lastprice,bprice,sprice\n"
        "IC2609,7789.6,7761.0,7760.8,7761.6\n"
    ).encode()
    monkeypatch.setattr(bot.requests, "get", lambda *_args, **_kwargs: response)

    frame = bot.fetch_cffex_quotes("IC")

    assert frame.iloc[0]["instrument"] == "IC2609"
    assert frame.iloc[0]["lastprice"] == pytest.approx(7761.0)


def test_sse_expiry_months_filter_malformed_values(monkeypatch):
    monkeypatch.setattr(
        bot,
        "_sse_quote_json",
        lambda *_args, **_kwargs: {
            "list": [
                ["510500", "202609"],
                ["510500", "202613"],
                ["510500", "2609"],
                ["510300", "202610"],
            ]
        },
    )
    assert bot.fetch_sse_510500_expiries() == ["202609"]


def test_request_json_does_not_retry_permanent_4xx(monkeypatch):
    calls = 0

    def fake_get(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        response = requests.Response()
        response.status_code = 404
        response.url = "https://example.test/missing"
        response._content = b"missing"
        return response

    monkeypatch.setattr(bot.requests, "get", fake_get)

    with pytest.raises(RuntimeError, match="HTTP 404"):
        bot._request_json("https://example.test", {})
    assert calls == 1


def test_eastmoney_index_history_requests_unadjusted_prices(monkeypatch):
    captured = []

    def fake_request_json(url, params):
        captured.append((url, params))
        if "gtimg" in url:
            return {}
        dates = bot.pd.bdate_range("2026-01-01", periods=121)
        return {
            "data": {
                "klines": [
                    f"{day:%Y-%m-%d},100,{100 + index},100,100,1"
                    for index, day in enumerate(dates)
                ]
            }
        }

    monkeypatch.setattr(bot, "_request_json", fake_request_json)

    series = bot.fetch_price_history("IC")

    eastmoney_params = next(params for url, params in captured if "eastmoney" in url)
    assert eastmoney_params["fqt"] == "0"
    assert len(series) == 121


def test_embedded_data_tampering_and_benchmark_length_are_rejected(monkeypatch):
    raw_returns = bytearray(
        zlib.decompress(base64.b64decode(bot._RETURN_BLOBS["IC"]))
    )
    raw_returns[-1] ^= 1
    monkeypatch.setitem(
        bot._RETURN_BLOBS,
        "IC",
        base64.b64encode(zlib.compress(bytes(raw_returns))).decode(),
    )
    with pytest.raises(RuntimeError, match="哈希校验失败"):
        bot.decode_returns("IC")

    formal_index = bot.decode_returns("IM").index
    raw_benchmark = zlib.decompress(base64.b64decode(bot._BENCHMARK_PRICE_BLOBS["IM"]))
    shortened = raw_benchmark[:-4]
    monkeypatch.setitem(
        bot._BENCHMARK_PRICE_BLOBS,
        "IM",
        base64.b64encode(zlib.compress(shortened)).decode(),
    )
    monkeypatch.setitem(
        bot._BENCHMARK_PRICE_SHA256, "IM", hashlib.sha256(shortened).hexdigest()
    )
    with pytest.raises(RuntimeError, match="行数"):
        bot.decode_benchmark_prices("IM", formal_index)


def test_valuation_score_and_sina_jsonp_parser(monkeypatch):
    assert bot.valuation_score(2.0, 0.03, 0.02) == pytest.approx(1.0)

    response = requests.Response()
    response.status_code = 200
    response._content = (
        'callback([{"d":"2026-08-14","c":"0.0633"},'
        '{"d":"2026-08-17","c":"0.0357"}]);'
    ).encode()
    monkeypatch.setattr(bot.requests, "get", lambda *_args, **_kwargs: response)

    closes = bot.fetch_sina_option_closes("10012080")

    assert list(closes.index) == [
        bot.pd.Timestamp("2026-08-14"),
        bot.pd.Timestamp("2026-08-17"),
    ]
    assert closes.iloc[-1] == pytest.approx(0.0357)


def _mo_quotes():
    return bot.pd.DataFrame(
        [
            {
                "instrument": "MO2610-P-6600",
                "position": 500,
                "volume": 100,
                "lastprice": 72.6,
                "bprice": 72.2,
                "sprice": 76.4,
            },
            {
                "instrument": "MO2608-C-8800",
                "position": 1500,
                "volume": 100,
                "lastprice": 0.2,
                "bprice": -1.0,
                "sprice": 0.2,
            },
        ]
    )


def _priced_mo_call(
    contract: str,
    today: date,
    spot: float,
    sigma: float,
    position: float = 500.0,
) -> dict[str, float | str]:
    match = bot.re.fullmatch(r"MO(\d{2})(\d{2})-C-(\d+)", contract)
    assert match is not None
    expiry = bot._third_friday(2000 + int(match.group(1)), int(match.group(2)))
    strike = float(match.group(3))
    years = max((expiry - today).days, 1) / 365.0
    price = bot._bs_price_delta(
        "C",
        spot,
        strike,
        float(bot.FROZEN["IM"]["gov10y"]),
        float(bot.FROZEN["IM"]["dividend"]),
        sigma,
        years,
    )[0]
    return {
        "instrument": contract,
        "position": position,
        "volume": 100.0,
        "lastprice": price,
        "bprice": max(price - 0.1, 0.0),
        "sprice": price + 0.1,
    }


def test_im_call_d10_uses_nearest_expiry_and_closest_delta():
    today = date(2026, 8, 20)
    spot = 8000.0
    quotes = bot.pd.DataFrame(
        [
            _priced_mo_call("MO2610-C-8400", today, spot, 0.25),
            _priced_mo_call("MO2610-C-8800", today, spot, 0.25),
            _priced_mo_call("MO2612-C-9000", today, spot, 0.25),
        ]
    )

    selected = bot.select_im_call_d10(
        quotes, today, spot, anchor_expiry=date(2026, 9, 18)
    )

    assert selected is not None
    assert selected["expiry"] == date(2026, 10, 16)
    assert selected["row"]["instrument"] == "MO2610-C-8800"
    assert selected["iv"] == pytest.approx(0.25, abs=1e-10)


def test_im_call_rescue_uses_next_listed_and_minimum_five_percent_higher_strike():
    today = date(2026, 8, 20)
    spot = 8500.0
    quotes = bot.pd.DataFrame(
        [
            _priced_mo_call("MO2609-C-9200", today, spot, 0.24),
            _priced_mo_call("MO2609-C-9300", today, spot, 0.24),
            _priced_mo_call("MO2609-C-9400", today, spot, 0.24),
            _priced_mo_call("MO2610-C-9300", today, spot, 0.24),
        ]
    )

    selected = bot.select_im_call_rescue(
        quotes,
        today,
        spot,
        old_expiry=date(2026, 8, 21),
        old_strike=8800.0,
    )

    assert selected is not None
    assert selected["expiry"] == date(2026, 9, 18)
    assert selected["row"]["instrument"] == "MO2609-C-9300"


@given(
    spot=st.floats(min_value=100.0, max_value=10_000.0, allow_nan=False),
    moneyness=st.floats(min_value=0.7, max_value=1.3, allow_nan=False),
    rate=st.floats(min_value=0.0, max_value=0.10, allow_nan=False),
    dividend=st.floats(min_value=0.0, max_value=0.10, allow_nan=False),
    sigma=st.floats(min_value=0.05, max_value=1.0, allow_nan=False),
    years=st.floats(min_value=0.05, max_value=3.0, allow_nan=False),
)
@settings(max_examples=100, deadline=None)
def test_black_scholes_put_call_parity(
    spot, moneyness, rate, dividend, sigma, years
):
    strike = spot * moneyness
    call = bot._bs_price_delta("C", spot, strike, rate, dividend, sigma, years)[0]
    put = bot._bs_price_delta("P", spot, strike, rate, dividend, sigma, years)[0]
    expected = spot * math.exp(-dividend * years) - strike * math.exp(-rate * years)
    assert call - put == pytest.approx(expected, rel=1e-10, abs=1e-8)


@given(
    option_type=st.sampled_from(["C", "P"]),
    spot=st.floats(min_value=100.0, max_value=10_000.0, allow_nan=False),
    moneyness=st.floats(min_value=0.90, max_value=1.10, allow_nan=False),
    rate=st.floats(min_value=0.0, max_value=0.08, allow_nan=False),
    dividend=st.floats(min_value=0.0, max_value=0.08, allow_nan=False),
    sigma=st.floats(min_value=0.10, max_value=0.9, allow_nan=False),
    years=st.floats(min_value=0.25, max_value=2.0, allow_nan=False),
)
@settings(max_examples=100, deadline=None)
def test_implied_volatility_round_trip(
    option_type, spot, moneyness, rate, dividend, sigma, years
):
    strike = spot * moneyness
    price = bot._bs_price_delta(
        option_type, spot, strike, rate, dividend, sigma, years
    )[0]
    recovered = bot._implied_volatility(
        option_type, price, spot, strike, rate, dividend, years
    )
    # Low-vega options are numerically ill-conditioned even when price round-trips.
    assert recovered == pytest.approx(sigma, rel=1e-7, abs=1e-7)


def test_implied_volatility_rejects_invalid_or_out_of_range_prices():
    assert bot._implied_volatility("C", 0.0, 100.0, 100.0, 0.02, 0.01, 1.0) is None
    assert (
        bot._implied_volatility("P", 1_000_000.0, 100.0, 100.0, 0.02, 0.01, 1.0)
        is None
    )


def test_complete_live_ic_signal_has_positions_targets_and_actions(monkeypatch):
    monkeypatch.setattr(
        bot,
        "live_proxy",
        lambda _product: {
            "price": 7850.4,
            "score": 1.857,
            "momentum_120": -0.0826,
            "put_delta": 0.50,
            "grid_next": False,
            "grid_exit": True,
        },
    )
    monkeypatch.setattr(
        bot, "fetch_cffex_quotes", lambda _product: _future_quotes("IC")
    )
    monkeypatch.setattr(
        bot,
        "fetch_sse_510500_chain",
        lambda _month: (
            bot.pd.DataFrame(
                [{"contract": "510500P2609M07250", "last": 0.0955, "strike": 7.25}]
            ),
            {"date": "20260820", "time": "162900"},
        ),
    )
    monkeypatch.setattr(
        bot,
        "fetch_sse_510500_quote",
        lambda: {
            "date": "20260820",
            "time": "162900",
            "last": 7.863,
            "prev_close": 7.804,
        },
    )

    signal = bot.build_live_trade_signal(
        "IC", bot.datetime(2026, 8, 19, 16, 30, tzinfo=bot.BEIJING)
    )

    assert signal["core_action"] == "HOLD"
    assert signal["grid_action"] == "HOLD"
    assert signal["put_action"] == "HOLD"
    assert signal["call_action"] == "HOLD"
    assert signal["put_current"] == "多 26张 510500P2609M07250"
    assert signal["next_core"] == "IC2609"


def test_close_signal_is_not_confirmed_before_same_day_close(monkeypatch):
    monkeypatch.setattr(
        bot,
        "live_proxy",
        lambda _product: {
            "price": 7589.78,
            "score": 2.033,
            "momentum_120": -0.1061,
            "history_date": date(2026, 8, 20),
            "puts_per_core": 4,
            "grid_next": False,
            "grid_exit": True,
        },
    )
    monkeypatch.setattr(
        bot,
        "fetch_cffex_quotes",
        lambda product: _future_quotes("IM") if product == "IM" else _mo_quotes(),
    )

    signal = bot.build_live_trade_signal(
        "IM",
        bot.datetime(2026, 8, 20, 10, 30, tzinfo=bot.BEIJING),
        mode="close",
    )

    assert signal["close_confirmed"] is False
    assert "尚未收盘" in signal["stage"]


def test_complete_live_im_signal_checks_call_threat(monkeypatch):
    monkeypatch.setattr(
        bot,
        "live_proxy",
        lambda _product: {
            "price": 7589.78,
            "score": 2.033,
            "momentum_120": -0.1061,
            "puts_per_core": 4,
            "grid_next": False,
            "grid_exit": True,
        },
    )
    monkeypatch.setattr(
        bot,
        "fetch_cffex_quotes",
        lambda product: _future_quotes("IM") if product == "IM" else _mo_quotes(),
    )

    signal = bot.build_live_trade_signal(
        "IM", bot.datetime(2026, 8, 19, 16, 30, tzinfo=bot.BEIJING)
    )

    assert signal["core_action"] == "HOLD"
    assert signal["put_action"] == "HOLD"
    assert signal["call_action"] == "HOLD"
    assert signal["call_otm"] == pytest.approx(8800.0 / 7589.78 - 1.0)
    assert signal["call_otm"] > 0.05


def test_uncovered_calendar_note_is_wired_into_signal_data_notes(monkeypatch):
    futures = bot.pd.concat(
        [
            _future_quotes("IM"),
            bot.pd.DataFrame(
                [
                    {
                        "instrument": "IM2703",
                        "lastprice": 7700.0,
                        "bprice": 7699.0,
                        "sprice": 7701.0,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    monkeypatch.setattr(
        bot,
        "live_proxy",
        lambda _product: {
            "price": 7589.78,
            "score": 2.033,
            "momentum_120": -0.1061,
            "puts_per_core": 4,
            "grid_next": False,
            "grid_exit": True,
            "history_date": date(2026, 8, 19),
        },
    )
    monkeypatch.setattr(
        bot,
        "fetch_cffex_quotes",
        lambda product: futures if product == "IM" else _mo_quotes(),
    )

    signal = bot.build_live_trade_signal(
        "IM", bot.datetime(2026, 8, 19, 16, 30, tzinfo=bot.BEIJING)
    )

    assert any("2027年" in note for note in signal["data_notes"])


@pytest.mark.parametrize(
    "selected_iv, expected_action",
    [(None, "WAIT_IV_OR_CHAIN"), (0.20, "WAIT_IV"), (0.30, "OPEN_CALL")],
)
def test_im_expired_call_open_wait_and_empty_chain_paths(
    monkeypatch, selected_iv, expected_action
):
    selected_row = bot.pd.Series(
        {
            "instrument": "MO2610-C-9000",
            "lastprice": 85.0,
            "bprice": 84.0,
            "sprice": 86.0,
        }
    )
    selected = (
        None
        if selected_iv is None
        else {"row": selected_row, "iv": selected_iv, "delta": 0.10}
    )
    monkeypatch.setattr(
        bot,
        "live_proxy",
        lambda _product: {
            "price": 8000.0,
            "score": 2.1,
            "momentum_120": 0.01,
            "puts_per_core": 4,
            "grid_next": False,
            "grid_exit": True,
            "history_date": date(2026, 8, 21),
        },
    )
    monkeypatch.setattr(
        bot,
        "fetch_cffex_quotes",
        lambda product: _future_quotes("IM") if product == "IM" else _mo_quotes(),
    )
    monkeypatch.setattr(
        bot, "select_im_put_for_reset", lambda *_args: _mo_quotes().iloc[0]
    )
    monkeypatch.setattr(bot, "select_im_call_d10", lambda *_args: selected)

    signal = bot.build_live_trade_signal(
        "IM", bot.datetime(2026, 8, 21, 16, 30, tzinfo=bot.BEIJING)
    )

    assert signal["call_action"] == expected_action
    if selected is not None:
        assert "MO2610-C-9000" in signal["call_target"]
        assert "MO2610-C-9000" in signal["call_market"]


def test_im_call_rescue_is_rendered_with_selected_market_row(monkeypatch):
    rescue_row = bot.pd.Series(
        {
            "instrument": "MO2609-C-9300",
            "lastprice": 36.0,
            "bprice": 35.0,
            "sprice": 37.0,
        }
    )
    monkeypatch.setattr(
        bot,
        "live_proxy",
        lambda _product: {
            "price": 8500.0,
            "score": 2.7,
            "momentum_120": -0.01,
            "puts_per_core": 4,
            "grid_next": False,
            "grid_exit": True,
            "history_date": date(2026, 8, 20),
        },
    )
    monkeypatch.setattr(
        bot,
        "fetch_cffex_quotes",
        lambda product: _future_quotes("IM") if product == "IM" else _mo_quotes(),
    )
    monkeypatch.setattr(
        bot,
        "select_im_call_rescue",
        lambda *_args: {
            "row": rescue_row,
            "iv": 0.22,
            "delta": 0.10,
            "expiry": date(2026, 9, 18),
        },
    )

    signal = bot.build_live_trade_signal(
        "IM", bot.datetime(2026, 8, 20, 14, 0, tzinfo=bot.BEIJING)
    )

    assert signal["call_action"] == "RESCUE_NEXT_LISTED"
    assert "MO2609-C-9300" in signal["call_target"]
    assert "MO2609-C-9300" in signal["call_market"]


def test_im_zero_put_target_skips_reselection_on_core_roll(monkeypatch):
    monkeypatch.setattr(
        bot,
        "live_proxy",
        lambda _product: {
            "price": 7589.78,
            "score": 1.0,
            "momentum_120": 0.01,
            "puts_per_core": 0,
            "grid_next": False,
            "grid_exit": True,
            "history_date": date(2026, 8, 20),
        },
    )
    monkeypatch.setattr(
        bot,
        "fetch_cffex_quotes",
        lambda product: _future_quotes("IM") if product == "IM" else _mo_quotes(),
    )
    monkeypatch.setattr(
        bot,
        "select_im_put_for_reset",
        lambda *_args: pytest.fail("zero IM Put target must not select a new contract"),
    )

    signal = bot.build_live_trade_signal(
        "IM", bot.datetime(2026, 8, 20, 16, 30, tzinfo=bot.BEIJING)
    )

    assert signal["core_action"] == "ROLL"
    assert signal["put_target"] == "无需Put（目标0张）"
    assert signal["put_action"] == "RESIZE_OR_ROLL"


def test_im_sixth_threat_closes_call_without_selecting_rescue(monkeypatch):
    monkeypatch.setitem(bot.FROZEN["IM"], "threat_roll_count", 5)
    monkeypatch.setattr(
        bot,
        "live_proxy",
        lambda _product: {
            "price": 8500.0,
            "score": 2.7,
            "momentum_120": -0.01,
            "puts_per_core": 4,
            "grid_next": False,
            "grid_exit": True,
            "history_date": date(2026, 8, 20),
        },
    )
    monkeypatch.setattr(
        bot,
        "fetch_cffex_quotes",
        lambda product: _future_quotes("IM") if product == "IM" else _mo_quotes(),
    )
    monkeypatch.setattr(
        bot,
        "select_im_call_rescue",
        lambda *_args: pytest.fail("the sixth threat must not select another rescue"),
    )

    signal = bot.build_live_trade_signal(
        "IM", bot.datetime(2026, 8, 20, 14, 0, tzinfo=bot.BEIJING)
    )

    assert signal["call_action"] == "CLOSE_CALL"
    assert "连续5次救援上限" in signal["call_target"]
    assert signal["threat_roll_count"] == 5


@pytest.mark.parametrize("signal_day", [20, 21])
def test_pre_expiry_or_expiry_signal_rolls_core_and_resets_put(monkeypatch, signal_day):
    monkeypatch.setattr(
        bot,
        "live_proxy",
        lambda _product: {
            "price": 7850.4,
            "score": 1.857,
            "momentum_120": -0.0826,
            "put_delta": 0.50,
            "grid_next": False,
            "grid_exit": True,
        },
    )
    monkeypatch.setattr(
        bot, "fetch_cffex_quotes", lambda _product: _future_quotes("IC")
    )
    monkeypatch.setattr(
        bot,
        "fetch_sse_510500_chain",
        lambda _month: (
            bot.pd.DataFrame(
                [{"contract": "510500P2609M07250", "last": 0.0955, "strike": 7.25}]
            ),
            {"date": "20260821", "time": "162900"},
        ),
    )
    monkeypatch.setattr(
        bot,
        "fetch_sse_510500_quote",
        lambda: {
            "date": "20260821",
            "time": "162900",
            "last": 7.863,
            "prev_close": 7.804,
        },
    )
    monkeypatch.setattr(
        bot,
        "select_ic_put_for_reset",
        lambda *_args: {
            "contract": "510500P2612M07500",
            "quote": bot.pd.Series(
                {"contract": "510500P2612M07500", "last": 0.12, "strike": 7.5}
            ),
            "qty": 15,
        },
    )

    signal = bot.build_live_trade_signal(
        "IC", bot.datetime(2026, 8, signal_day, 16, 30, tzinfo=bot.BEIJING)
    )

    assert signal["core_target"] == "IC2609"
    assert signal["core_action"] == "ROLL"
    assert signal["roll_execution_date"] == date(2026, 8, 21)
    assert signal["put_action"] == "RESIZE_OR_ROLL"
    assert "510500P2612M07500" in signal["put_target"]


def test_ic_reset_without_valid_iv_renders_pending_quantity(monkeypatch):
    monkeypatch.setattr(
        bot,
        "live_proxy",
        lambda _product: {
            "price": 7850.4,
            "score": 1.857,
            "momentum_120": -0.0826,
            "put_delta": 0.50,
            "grid_next": False,
            "grid_exit": True,
            "history_date": date(2026, 8, 20),
        },
    )
    monkeypatch.setattr(bot, "fetch_cffex_quotes", lambda _product: _future_quotes("IC"))
    monkeypatch.setattr(
        bot,
        "fetch_sse_510500_chain",
        lambda _month: (
            bot.pd.DataFrame(
                [{"contract": "510500P2609M07250", "last": 0.0955, "strike": 7.25}]
            ),
            {"date": "20260820", "time": "162900"},
        ),
    )
    monkeypatch.setattr(
        bot,
        "fetch_sse_510500_quote",
        lambda: {
            "date": "20260820",
            "time": "162900",
            "last": 7.863,
            "prev_close": 7.804,
        },
    )
    reset_quote = bot.pd.Series(
        {"contract": "510500P2612M07500", "last": 0.12, "strike": 7.5}
    )
    monkeypatch.setattr(
        bot,
        "select_ic_put_for_reset",
        lambda *_args: {
            "contract": "510500P2612M07500",
            "quote": reset_quote,
            "qty": None,
        },
    )

    signal = bot.build_live_trade_signal(
        "IC", bot.datetime(2026, 8, 20, 16, 30, tzinfo=bot.BEIJING)
    )

    assert signal["put_action"] == "RESIZE_OR_ROLL"
    assert "数量待有效IV" in signal["put_target"]
    assert "510500P2612M07500" in signal["put_target"]


def test_action_labels_cover_every_signal_action_literal():
    possible_actions = {
        "HOLD",
        "ROLL",
        "ADD_GRID",
        "EXIT_GRID",
        "RESIZE_OR_ROLL",
        "RESCUE_NEXT_LISTED",
        "OPEN_CALL",
        "WAIT_IV",
        "WAIT_IV_OR_CHAIN",
        "CLOSE_CALL",
    }
    assert set(bot.ACTION_CN) == possible_actions


class _CaptureMessage:
    def __init__(self):
        self.text: list[str] = []
        self.files: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def write(self, value):
        self.text.append(str(value))

    def attach_file(self, **kwargs):
        self.files.append(kwargs)


@pytest.mark.parametrize(
    "handler, products, expected",
    [
        ("_handle_snapshot", ("IC", "IM"), ["冻结研究快照", "IC / 中证500", "IM / 中证1000"]),
        ("_handle_params", ("IC", "IM"), ["冻结主线参数", "绝对 Delta", "rescue_next_listed"]),
    ],
)
def test_snapshot_and_params_handlers_smoke(monkeypatch, handler, products, expected):
    capture = _CaptureMessage()
    monkeypatch.setattr(bot.poe, "start_message", lambda: capture)

    getattr(bot.ICIMMainlinesBot(), handler)(products)

    output = "".join(capture.text)
    assert all(fragment in output for fragment in expected)
    assert capture.files == []


def test_performance_handler_isolates_bad_zip_per_product(monkeypatch):
    capture = _CaptureMessage()
    monkeypatch.setattr(bot.poe, "start_message", lambda: capture)
    monkeypatch.setattr(
        bot,
        "performance_frame",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(zipfile.BadZipFile("bad")),
    )
    intent = bot.QueryIntent(
        "performance", ("IC", "IM"), date(2026, 1, 1), date(2026, 8, 20)
    )

    bot.ICIMMainlinesBot()._handle_performance(intent)

    output = "".join(capture.text)
    assert "### IC" in output
    assert "### IM" in output
    assert output.count("bad") == 2


def test_requested_recent_year_command_outputs_two_separate_nav_drawdown_images(
    monkeypatch,
):
    capture = _CaptureMessage()
    live_index = bot.pd.to_datetime(
        ["2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20"]
    )
    monkeypatch.setattr(
        bot,
        "latest_continuation_frame",
        lambda product, _end: bot.pd.DataFrame(
            {
                "ret": [0.01, -0.005, 0.002, 0.003],
                "benchmark_price": (
                    [8184.64, 8177.18, 7783.46, 7850.40]
                    if product == "IC"
                    else [7968.38, 7945.96, 7517.77, 7589.78]
                ),
                "is_live": [True] * 4,
            },
            index=live_index,
        ),
    )
    monkeypatch.setattr(
        bot.poe, "query", types.SimpleNamespace(text="查询最近一年表现", attachments=[])
    )
    monkeypatch.setattr(bot.poe, "start_message", lambda: capture)
    monkeypatch.setattr(bot, "beijing_today", lambda: date(2026, 8, 20))

    bot.ICIMMainlinesBot().run()

    assert len(capture.files) == 2
    assert {item["name"].split("_")[0] for item in capture.files} == {"ic", "im"}
    assert all(
        item["content_type"] == "image/png" and item["is_inline"]
        for item in capture.files
    )
    assert all(item["contents"].startswith(b"\x89PNG") for item in capture.files)
    output = "".join(capture.text)
    assert "数据已更新至 **2026-08-20**" in output
    assert "正式数据只到 2026-08-14" not in output
    assert "中证500价格指数" in output
    assert "中证1000价格指数" in output
    assert "仅供研究审计" not in output
    assert "不构成自动或人工下单建议" not in output


@pytest.mark.parametrize(
    "query, expected_mode",
    [("实时信号 IM", "intraday"), ("信号 IM", "close")],
)
def test_signal_commands_dispatch_explicit_mode(monkeypatch, query, expected_mode):
    called: list[tuple[tuple[str, ...], str]] = []
    monkeypatch.setattr(
        bot.poe, "query", types.SimpleNamespace(text=query, attachments=[])
    )
    monkeypatch.setattr(
        bot.ICIMMainlinesBot,
        "_handle_signal",
        lambda _self, products, mode: called.append((products, mode)),
    )

    bot.ICIMMainlinesBot()._run_impl()

    assert called == [(("IM",), expected_mode)]
