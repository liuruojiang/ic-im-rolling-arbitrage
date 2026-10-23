"""New-write grid confirmation adversaries; disposable ledgers and no network."""
from copy import deepcopy
from datetime import date

import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_icim_batch3_old_call_20261021 as builder_seed
from test_icim_b3adv_state_20261021 import prepare
from test_icim_round2_audit_lifecycle_20261021 import snapshot


def unavailable_fear(signal):
    """A valid unavailable-source observation, not malformed or forged evidence."""
    day = signal["market_date"]
    stamp = str(day)[:10] + "T15:30:00+08:00"
    signal.update(
        fear_source_policy_revision=bot.fear_grid.SOURCE_POLICY_REVISION,
        fear_fetch_started_at=stamp, fear_retrieved_at=stamp,
        fear_csv_sha256=None, fear_greed_index=None, fear_data_date=None,
        fear_data_status="unavailable", fear_unavailable_reason="synthetic unavailable sources",
        fear_available_sources=[], fear_selected_source=None,
        fear_cross_validation="unavailable", fear_page_updated_at=None,
        fear_page_data_date=None, fear_source_url=None,
        grid_target=signal["grid_current"],
        grid_entry_source_target=signal["grid_entry_source_current"],
        grid_transition_reason="fear_unavailable", grid_action="HOLD",
    )
    return signal


GATES = [
    state.require_persistence_eligible,
    state.validate_fear_grid_signal,
    state.validate_v14_new_signal,
    state.validate_delivery_values,
]


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("gate", GATES, ids=lambda fn: fn.__name__)
def test_each_new_write_validator_recomputes_unconfirmed_grid(tmp_path, monkeypatch, product, gate):
    _store, _record, signals = prepare(tmp_path, monkeypatch)
    signal = unavailable_fear(signals[product])
    signal.update(signal_complete=True, persistence_eligible=True,
                  calendar_provisional=False, diagnostic_only=False)
    assert bot.fear_grid.publication_view(signal)["grid_target_confirmed"] is False
    with pytest.raises(RuntimeError, match="网格目标未确认"):
        gate(signal, product)


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("markers", [
    {},
    {"signal_complete": True, "persistence_eligible": True},
    {"grid_target_confirmed": True, "total_units_target_confirmed": True},
    {"signal_complete": True, "persistence_eligible": True,
     "grid_target_confirmed": True, "total_units_target_confirmed": True},
])
def test_producer_flags_cannot_override_unconfirmed_publication(tmp_path, monkeypatch, product, markers):
    _store, _record, signals = prepare(tmp_path, monkeypatch)
    signal = unavailable_fear(signals[product])
    signal.update(markers)
    with pytest.raises(RuntimeError, match="网格目标未确认"):
        state.require_persistence_eligible(signal, product)


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_direct_append_unconfirmed_leg_leaves_shared_ledger_unchanged(tmp_path, monkeypatch, product):
    store, current, signals = prepare(tmp_path, monkeypatch)
    # Keep the shared source version consistent so this isolates confirmation
    # from the independent mixed-source rejection.
    for signal in signals.values():
        unavailable_fear(signal).update(signal_complete=True, persistence_eligible=True)
    before = snapshot(store.root)
    with pytest.raises(RuntimeError, match="网格目标未确认"):
        store.append_confirmed_signals(current, signals)
    assert snapshot(store.root) == before
    assert store.load_latest()["digest"] == current["digest"]


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_frozen_historical_values_keep_original_validation_contract(tmp_path, monkeypatch, product):
    _store, _record, signals = prepare(tmp_path, monkeypatch)
    signal = unavailable_fear(signals[product])
    signal.update(signal_complete=False, persistence_eligible=False)
    # Old snapshots may preserve the model grid despite hiding the published
    # target. Reading them must not apply today's new-write completeness rule.
    state.validate_delivery_values(signal, product, historical_record=True)
    state.validate_fear_grid_signal(signal, product, historical_record=True)


def test_hash_verified_historical_record_read_does_not_apply_new_gate(tmp_path, monkeypatch):
    store, current, signals = prepare(tmp_path, monkeypatch)
    written = store.append_confirmed_signals(current, signals)
    historical = deepcopy(written)
    for signal in historical["signals"].values():
        unavailable_fear(signal).update(signal_complete=False, persistence_eligible=False)
    historical["digest"] = state._digest(historical)
    state._validate_record(historical)
    assert store.load_latest()["digest"] == written["digest"]


def test_confirmed_pair_still_commits_and_recovers_existing_complete_journal(tmp_path, monkeypatch):
    store, current, signals = prepare(tmp_path, monkeypatch)
    for product, signal in signals.items():
        signal.update(signal_complete=True, persistence_eligible=True)
        state.require_confirmed_grid_target(signal, product)
    written = store.append_confirmed_signals(current, signals)
    journal = store.journal_dir / f"{written['sequence']:06d}-{written['verified_day']}.json"
    frozen = journal.read_bytes()
    store._atomic_write(store.latest_path, current)
    recovered = store.append_confirmed_signals(current, signals)
    assert recovered["digest"] == written["digest"]
    assert journal.read_bytes() == frozen


def test_sparse_pre_policy_signal_keeps_legacy_eligibility():
    signal = {"market_date": date(2026, 9, 18), "close_confirmed": True}
    state.require_persistence_eligible(signal, "IC")


def test_public_builder_unavailable_fear_is_independently_rejected(monkeypatch):
    builder_seed.wire(monkeypatch, held=False)
    def no_network(*_args, **_kwargs):
        raise AssertionError("unexpected network")
    monkeypatch.setattr(bot.requests, "get", no_network)
    fear = unavailable_fear(dict(market_date=builder_seed.DAY,
                                 grid_current=0.0, grid_entry_source_current="none"))
    monkeypatch.setattr(bot.fear_grid, "fetch_score", lambda *_args: deepcopy(fear))
    signal = bot.build_live_trade_signal("IM", builder_seed.CLOCK, "close")
    assert bot.fear_grid.publication_view(signal)["grid_target_confirmed"] is False
    # Replacing producer flags must never be enough to bypass consumer checks.
    signal.update(signal_complete=True, persistence_eligible=True,
                  calendar_provisional=False, diagnostic_only=False,
                  grid_target_confirmed=True)
    with bot.runtime_clock(builder_seed.CLOCK):
        for gate in GATES:
            with pytest.raises(RuntimeError, match="网格目标未确认"):
                gate(signal, "IM")
