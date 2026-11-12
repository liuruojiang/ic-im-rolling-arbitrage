"""Correct the audit's explicit-performance expectation; preserve original run."""
from types import SimpleNamespace

import pytest

from test_surface_v5_r1 import bot, DAY, offline


def test_explicit_performance_has_priority_over_signal_at_actual_entry(monkeypatch):
    text = "IC 2026-01-05表现 信号"
    assert bot.classify_query(text, DAY).route == "performance"
    called = []
    monkeypatch.setattr(bot.poe, "query", SimpleNamespace(text=text))
    monkeypatch.setattr(bot.ICIMMainlinesBot, "_handle_performance", lambda self, intent: called.append(intent))
    monkeypatch.setattr(bot.ICIMMainlinesBot, "_handle_signal", lambda *a: pytest.fail("priority changed"))
    bot.ICIMMainlinesBot()._run_impl()
    assert len(called) == 1 and called[0].start == called[0].end == DAY.replace(month=1, day=5)


@pytest.mark.parametrize("reference", ["12月份合约", "12月的合约", "12月份的合约"])
def test_explicit_performance_keeps_strict_unconsumed_month_rejection(monkeypatch, reference):
    monkeypatch.setattr(bot.poe, "query", SimpleNamespace(text=f"IC {reference} 2026-01-05表现 信号"))
    monkeypatch.setattr(bot.ICIMMainlinesBot, "_handle_snapshot", lambda *a: pytest.fail("strict performance swallowed"))
    with pytest.raises(ValueError, match="未解析的日期"):
        bot.ICIMMainlinesBot()._run_impl()


@pytest.mark.parametrize("text", ["查询IC 12.5月合约", "查询IC 1. 2月合约", "查询IC 12026年12月合约"])
def test_old_decimal_and_broken_year_controls_stay_strict(monkeypatch, text):
    monkeypatch.setattr(bot.poe, "query", SimpleNamespace(text=text))
    monkeypatch.setattr(bot.ICIMMainlinesBot, "_handle_snapshot", lambda *a: pytest.fail("old malformed date bypass"))
    with pytest.raises(ValueError):
        bot.ICIMMainlinesBot()._run_impl()


@pytest.mark.parametrize("text,products", [("查询IC 2026年12月合约", ("IC",)),
    ("查询中证1000 3月合约", ("IM",))])
def test_prior_real_snapshot_regressions_are_closed(monkeypatch, text, products):
    calls = []
    monkeypatch.setattr(bot.poe, "query", SimpleNamespace(text=text))
    monkeypatch.setattr(bot.ICIMMainlinesBot, "_handle_snapshot", lambda self, selected: calls.append(selected))
    bot.ICIMMainlinesBot()._run_impl()
    assert calls == [products]
