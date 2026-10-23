"""Explicit replacements for superseded crash/partial-write expectations.

Original test files and receipts remain immutable. Reuse the same input fixtures
while asserting the new observable outcome and unchanged persistent payloads.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta

import pandas as pd
import pytest
import requests

import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_ic_im_external_review_20261021 as external
import test_icim_redteam_execution_20261021 as execution_seed
import test_ic_im_ordinary_put_route_fix_20261013 as ledger
import test_icim_round2_audit_lifecycle_20261021 as lifecycle
import test_ic_im_external_review_round2_20261021 as identity
import test_icim_b3adv_market_20261021 as market_seed


def assert_diagnostic(out):
    assert out["diagnostic_only"] is True and out["signal_complete"] is False
    assert out["persistence_eligible"] is False and out["v14_action"] == "HOLD_FAIL_CLOSED"
    assert not any(key in out for key in ("core_target", "put_target_contract", "v14_ordinary_put_pending",
                                        "v14_short_put_open_pending", "v14_profit_pending"))
    with pytest.raises(RuntimeError):
        state.require_persistence_eligible(out, out["product"])


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_actual_missing_opening_becomes_diagnostic_instead_of_crash(monkeypatch, product):
    anchor = external.ordinary_anchor(product)
    original = deepcopy(anchor)
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, product, anchor)
    monkeypatch.setattr(bot, "live_proxy", lambda *a: {"history_date": external.DAY})
    monkeypatch.setattr(bot, "_validate_signal_market_date", lambda *a: True)
    def unavailable(*a, **k):
        raise requests.Timeout("same legacy missing-opening input")
    monkeypatch.setattr(bot, "fetch_option_dated_open", unavailable)
    monkeypatch.setattr(bot, "fetch_cffex_quotes", unavailable)
    out = bot.build_live_trade_signal(product, external.CLOCK, "close")
    assert_diagnostic(out)
    assert out["diagnostic_status"] == "awaiting_opening_evidence"
    assert bot.LIVE_CONTINUATION_ANCHOR[product] == original


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("failed", ["ordinary", "short_put"])
def test_coexisting_failed_openings_restore_anchor_and_publish_no_new_plan(monkeypatch, product, failed):
    anchor = execution_seed.with_independent_momentum(external.short_anchor(product), product)
    bot.v14_policy.validate_extension(product, anchor)
    original = deepcopy(anchor)
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, product, anchor)
    monkeypatch.setattr(bot, "live_proxy", lambda *a: {"history_date": execution_seed.DAY})
    monkeypatch.setattr(bot, "_validate_signal_market_date", lambda *a: True)
    plan = anchor["v14_ordinary_put_pending"]
    opening = dict(status=bot.put_execution.RETRY_OPEN_STATUS if failed == "ordinary" else "confirmed_open_research_price",
                   plan=plan, prices=execution_seed.ordinary_prices(plan, execution_seed.DAY))
    monkeypatch.setattr(bot, "_v14_ordinary_open_evidence", lambda *a: deepcopy(opening))
    calls = []
    def failed_short(*a):
        calls.append(a)
        raise requests.Timeout("same legacy coexisting opening input")
    monkeypatch.setattr(bot, "_v14_committed_open_prices", failed_short)
    out = bot.build_live_trade_signal(product, execution_seed.CLOCK, "close")
    assert_diagnostic(out)
    assert len(calls) == (0 if failed == "ordinary" else 1)
    assert bot.LIVE_CONTINUATION_ANCHOR[product] is anchor
    assert bot.LIVE_CONTINUATION_ANCHOR[product] == original
    assert len(out["retry_original_plans"]) == 2
    # This legacy fixture intentionally contains only the option-anchor fields.
    assert out["diagnostic_future"]["last_verified_contract"] == original.get("post_core_contract")


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("missing", ["old_core", "new_core"])
def test_new_write_of_legacy_failclosed_replay_is_rejected_atomically(tmp_path, product, missing):
    store, record = ledger._persist(tmp_path, ledger._profit_record(product))
    anchor = state.anchors_from_record(record)[product]
    initial = ledger._profit_trigger(product, anchor)
    record = ledger._profit_append(store, record, product, initial)
    anchor = state.anchors_from_record(record)[product]
    day = bot._roll_forward_exchange_day(ledger.DAY + timedelta(days=1))
    signal = ledger._profit_session(product, anchor, day, missing=missing)
    assert signal["v14_action"] == "HOLD_FAIL_CLOSED"
    before = lifecycle.snapshot(store.root)
    with pytest.raises(RuntimeError, match="HOLD_FAIL_CLOSED"):
        ledger._profit_append(store, record, product, signal)
    assert lifecycle.snapshot(store.root) == before
    # Reading the already-confirmed historical record remains supported.
    assert state.StateStore(store.root).load_latest()["digest"] == record["digest"]


def test_same_december_public_builder_case_is_diagnostic_without_ledger_write(tmp_path, monkeypatch):
    day = date(2026, 12, 23)
    clock = datetime(2026, 12, 23, 15, 40, tzinfo=bot.BEIJING)
    for key, value in dict(DAY=day, PREVIOUS=date(2026, 12, 22), CLOCK=clock).items():
        monkeypatch.setattr(lifecycle, key, value)
    store, record = lifecycle.recent_store(tmp_path / "ledger")
    anchors = state.anchors_from_record(record)
    anchor = anchors["IC"]
    ledger._empty_core(anchor, "IC")
    anchor.update(post_core_contract="IC2703", v14_route_state="short_put", v14_cycle_id="same-legacy-december",
                  v14_short_put_contract=identity.M, v14_short_put_security_id="10011470",
                  v14_short_put_qty_normalized=5., v14_short_put_entry_premium=.4, v14_short_put_expiry=day,
                  v14_short_put_cost_basis="confirmed_dated_open", v14_short_put_entry_price_day=date(2026, 12, 22),
                  verified_momentum_put_delta=0., verified_momentum_put_qty=0)
    body = identity.payload()
    body["date"] = 20261223
    chain, stamp = identity.wire(monkeypatch, body)
    monkeypatch.setattr(bot, "LIVE_CONTINUATION_ANCHOR", anchors)
    lifecycle.builder_inputs(monkeypatch, anchor, chain, stamp)
    monkeypatch.setattr(bot, "fetch_sse_510500_quote", lambda: dict(date="20261223", time="150000", last=7.8))
    monkeypatch.setattr(bot, "fetch_option_closes", lambda security: pd.Series([.01], index=pd.to_datetime([day])))
    before, disk = deepcopy(anchors), lifecycle.snapshot(store.root)
    out = bot.build_live_trade_signal("IC", clock, "close")
    assert_diagnostic(out)
    assert out["calendar_provisional"] is True
    assert bot.LIVE_CONTINUATION_ANCHOR == before and lifecycle.snapshot(store.root) == disk


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_same_mixed_source_input_retains_waiting_and_malformed_evidence(monkeypatch, product):
    market_seed.source_parsers(monkeypatch, product, bad_primary=True)
    with pytest.raises(RuntimeError) as caught:
        bot.fetch_ohlcv_history(product)
    assert not isinstance(caught.value, bot.DailyOHLCVNotReady)
    assert caught.value.status == "daily_ohlcv_sources_unavailable"
    assert caught.value.waiting_sources
    assert any(row["type"] == "KeyError" for row in caught.value.source_failures)
