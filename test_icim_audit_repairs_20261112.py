"""Regression contracts for the four independently reproduced audit findings."""
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

import ic_im_state_council_calendar_v1 as cal
import poe_ic_im_mainline_v1_4_bot as bot


TODAY = date(2026, 11, 12)


@pytest.mark.parametrize("query", [
    "2026-03-01到2026-08-311", "2026-03-01到2026-08-310",
    "2026年3月1日至2026年8月311日", "12026-03-01到2026-08-31",
    "12026-03", "2026-123", "12026年3月", "20260年",
    "2026-13", "2026-02-30", "2026-03-01到12026-08-31",
    "12026-2027年", "20261-2027年", "12026-2027", "20261-2027",
    "12026到2027年", "20261到2027年", "12026-2027年至今",
    "2026到12027年", "2026-20271", "2026至12027",
    "12026至2027年", "20261至2027",
    "202-2027年", "202到2027年", "202至2027年", "202~2027年",
    "202—2027年", "202–2027年", "2026到202年", "2026至202",
    "2026~202年", "2026—202", "2026至202年至今",
    "19026-03-01到2026-08-31", "19026/03/01到2026/08/31",
    "19026-03-2026-08",
    "19026.03到2026.08", "2026.03到19026.08", "2/03到2026/08",
    "3月到8月", "26/03至27/08", "26.03月到27.08月",
    "2/03-2026/08", "26/03-2026/08", "2026-03-2/08", "2026.03到2/08",
    "2026.03到8", "2026.03到8月", "2026年3月至最近1年", "最近1年至2026年8月",
    "2026年3月到今年", "今年到2026年8月",
    "2026-03-02到今天", "2026年3月到现在", "2026年3月到",
    "2026/03/01/02", "2026.03.01.02",
    "2026-03-01到2026-08-31/2", "2026-03-01到2026-08-31.2", "2026/03/02/8",
])
def test_invalid_numeric_dates_cannot_shorten_the_request(query):
    with pytest.raises(ValueError):
        bot.parse_date_range(query, TODAY)


@pytest.mark.parametrize("query", ["IC 12026-2027表现", "IM 12026到2027年净值", "IC 20261-2027年至今收益"])
def test_invalid_year_ranges_cannot_become_default_performance_windows(query):
    with pytest.raises(ValueError):
        bot.classify_query(query, TODAY)


def test_invalid_year_mutations_cannot_escape_through_either_endpoint():
    # Short years, unsupported centuries and overlong years are whole tokens.
    # A numeric YYYY-M form remains a month, so use explicit 年 for that pair.
    for year in ["2", "20", "26", "202", "1900", "1902", "2100", "19026", "12026", "20261", "202600"]:
        queries = [
            f"{year}-03-01到2026-08-31", f"2026-03-01到{year}-08-31",
            f"{year}/03/01至2026/08/31", f"2026/03/01至{year}/08/31",
            f"{year}年3月1日至2026年8月31日", f"2026年3月1日至{year}年8月31日",
            f"{year}-03到2026-08", f"2026-03到{year}-08",
            f"{year}/03到2026/08", f"2026/03到{year}/08",
            f"{year}.03到2026.08", f"2026.03到{year}.08",
        ]
        for connector in ["到", "至", "~", "—", "–", "-"]:
            queries += [f"{year}年{connector}2026年", f"2026年{connector}{year}年"]
        for query in queries:
            with pytest.raises(ValueError, match="年份|日期"):
                bot.parse_date_range(query, TODAY)
            with pytest.raises(ValueError, match="年份|日期"):
                bot.classify_query("IC " + query + "表现", TODAY)
        for left_style in ["-", "/", ".", "chinese"]:
            for right_style in ["-", "/", ".", "chinese"]:
                for connector in ["到", "至", "~", "—", "–", "-"]:
                    for first, last in [(year, "2026"), ("2026", year)]:
                        query = _month_token(first, 3, left_style) + connector + _month_token(last, 8, right_style)
                        for text in [query, "IC " + query + "表现"]:
                            with pytest.raises(ValueError):
                                bot.parse_date_range(text, TODAY)
                        with pytest.raises(ValueError):
                            bot.classify_query("IC " + query + "表现", TODAY)


def _month_token(year, month, style):
    return f"{year}年{month}月" if style == "chinese" else f"{year}{style}{month:02}"


def test_complete_dates_are_not_misread_as_mixed_month_ranges():
    def full_date(month, day, style):
        return f"2026年{month}月{day}日" if style == "chinese" else f"2026{style}{month:02}{style}{day:02}"
    for left_style in ["-", "/", ".", "chinese"]:
        for right_style in ["-", "/", ".", "chinese"]:
            for connector in ["到", "至", "~", "—", "–", "-"]:
                month_query = _month_token("2026", 3, left_style) + connector + _month_token("2026", 8, right_style)
                full_query = full_date(3, 1, left_style) + connector + full_date(8, 31, right_style)
                for query in [month_query, full_query]:
                    assert bot.parse_date_range(query, TODAY) == (date(2026, 3, 1), date(2026, 8, 31))
                    intent = bot.classify_query("IC " + query + "表现", TODAY)
                    assert (intent.start, intent.end) == (date(2026, 3, 1), date(2026, 8, 31))


def test_version_numbers_and_unrelated_fractions_are_not_dates():
    assert bot.parse_date_range("IC v1.4 2026年3月表现", TODAY) == (date(2026, 3, 1), date(2026, 3, 31))
    assert bot.parse_date_range("IC v1.4—2026年3月表现", TODAY) == (date(2026, 3, 1), date(2026, 3, 31))
    assert bot.parse_date_range("IC 保证金 1/2", TODAY) == (None, None)
    assert bot.parse_date_range("IC v1.4年化收益", TODAY) == (None, None)
    assert bot.parse_date_range("IC 1.2到2.5", TODAY) == (None, None)
    assert bot.parse_date_range("IC 2026年3月 / 年化表现", TODAY) == (date(2026, 3, 1), date(2026, 3, 31))


@pytest.mark.parametrize("query,expected", [
    ("2026-03-02至今收益", (date(2026, 3, 2), TODAY)),
    ("2026年3月2日至今表现", (date(2026, 3, 2), TODAY)),
    ("2026年3月至今", (date(2026, 3, 1), TODAY)),
    ("2026年至今", (date(2026, 1, 1), TODAY)),
    ("2026-03-2026-08", (date(2026, 3, 1), date(2026, 8, 31))),
    ("2026-03-01-2026-08-31", (date(2026, 3, 1), date(2026, 8, 31))),
    ("2026/03/01～2026/08/31", (date(2026, 3, 1), date(2026, 8, 31))),
    ("2026.03.01到2026.08.31", (date(2026, 3, 1), date(2026, 8, 31))),
    ("2025-2026", (date(2025, 1, 1), date(2026, 12, 31))),
    ("2026年到2027年", (date(2026, 1, 1), date(2027, 12, 31))),
    ("2026-03", (date(2026, 3, 1), date(2026, 3, 31))),
    ("2025–2026", (date(2025, 1, 1), date(2026, 12, 31))),
    ("2026-03-01–2026-08-31", (date(2026, 3, 1), date(2026, 8, 31))),
    ("2026年–2026年8月", (date(2026, 1, 1), date(2026, 8, 31))),
    ("2026.03到2026.08", (date(2026, 3, 1), date(2026, 8, 31))),
    ("2026.08到2026.03", (date(2026, 3, 1), date(2026, 8, 31))),
    ("2026.03月到2026.08月", (date(2026, 3, 1), date(2026, 8, 31))),
    ("2026/03.01到2026.08-31", (date(2026, 3, 1), date(2026, 8, 31))),
    ("2026.03-01到2026/08.31", (date(2026, 3, 1), date(2026, 8, 31))),
    ("IC v1.4-r1 2026年3月至今表现", (date(2026, 3, 1), TODAY)),
    ("IC v1.4-r1 近3个月表现", (date(2026, 8, 12), TODAY)),
    ("最近1年至今", (date(2025, 11, 12), TODAY)),
    ("今年以来", (date(2026, 1, 1), TODAY)),
    ("去年全年", (date(2025, 1, 1), date(2025, 12, 31))),
    ("前年全年", (date(2024, 1, 1), date(2024, 12, 31))),
    ("近100年表现", (date(1926, 11, 12), TODAY)),
])
def test_valid_date_windows_keep_their_full_meaning(query, expected):
    assert bot.parse_date_range(query, TODAY) == expected


@pytest.mark.parametrize("entry", ["numeric", "validate", "mo_basis"])
@pytest.mark.parametrize("column", ["lastprice", "volume", "position", "bprice", "sprice"])
@pytest.mark.parametrize("boolean", [True, np.bool_(True)], ids=["bool", "numpy_bool"])
def test_quote_numbers_reject_booleans_before_conversion(entry, column, boolean):
    frame = pd.DataFrame([dict(instrument="MO2612-P-7500", lastprice=100., volume=0,
                               position=50, bprice=99., sprice=101., source_date=TODAY)])
    frame[column] = frame[column].astype(object)
    frame.at[0, column] = boolean
    with pytest.raises(RuntimeError, match="不允许布尔值"):
        if entry == "numeric":
            bot._numeric_frame(frame)
        elif entry == "validate":
            bot._validate_quote_frame(frame, "MO", datetime(2026, 11, 12, 16, tzinfo=bot.BEIJING), "新浪财经")
        else:
            bot._live_mo_put_quote_basis(frame)


def test_boolean_audit_metadata_is_not_mistaken_for_a_quote_number():
    frame = pd.DataFrame([dict(instrument="MO2612-P-7500", lastprice=100., volume=0,
                               position=50, bprice=99., sprice=101., is_live=True)])
    normalized = bot._numeric_frame(frame)
    assert bool(normalized.at[0, "is_live"])
    assert normalized.at[0, "lastprice"] == 100.


@pytest.mark.parametrize("signal_day", [date(2026, 11, 11), date(2026, 11, 12)])
def test_calendar_producer_consumer_use_one_signal_day_and_restore_context(signal_day):
    signal = dict(market_date=signal_day, next_trade_date=date(2026, 11, 13),
                  core_current="IM2612", core_target="IM2612",
                  core_put_target_contract="MO2703-P-8000", core_put_target_qty_normalized=1.5)
    outer_day = date(2028, 1, 1)
    token = bot._CALENDAR_POLICY_DAY.set(outer_day)
    try:
        with bot.runtime_clock(datetime(2026, 11, 12, 8, tzinfo=bot.BEIJING)):
            if signal_day < cal.EFFECTIVE_SIGNAL_DATE:
                with pytest.raises(bot.CalendarCoverageError):
                    bot.require_signal_calendar("IM", signal)
                assert bot._CALENDAR_POLICY_DAY.get() == outer_day
                with pytest.raises(bot.CalendarCoverageError):
                    bot.validate_calendar_authority("IM", signal)
            else:
                rows = bot.require_signal_calendar("IM", signal)
                signal["calendar_source_evidence"] = dict(policy_revision=cal.REVISION,
                    exchange_notice_verified=False, sources=rows)
                bot.validate_calendar_authority("IM", signal)
            assert bot._CALENDAR_POLICY_DAY.get() == outer_day
    finally:
        bot._CALENDAR_POLICY_DAY.reset(token)
