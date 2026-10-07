"""Independent storage-boundary attacks; writes only to pytest temporary roots.

These cases test real ledger code and copied existing fixture shapes, not market
performance. Production state, frozen specs, and published outputs are untouched.
"""
from datetime import date
from pathlib import Path
import shutil

import pytest

import ic_im_v1_4_policy as policy
import poe_ic_im_v1_4_state as state
from test_poe_ic_im_v1_3_state import _signals


def _v14_signals(day):
    signals = _signals(day)
    for product, signal in signals.items():
        signal.update(policy.default_extension(product))
        build, revision = policy.identity_for_signal_day(day)
        signal.update(strategy_version="1.4", strategy_revision="r1",
                      v14_build_id=build, v14_rule_revision=revision)
    # Historical IC fixture has one 14-contract sleeve, so there is no split
    # ambiguity and no synthetic inferred Delta sizing.
    signals["IC"].update(put_target_core_qty=14, put_target_momentum_qty=0)
    return signals


def test_truly_empty_volume_creates_one_valid_genesis(tmp_path):
    store = state.StateStore(tmp_path)
    assert not store.latest_path.exists()
    assert not store.journal_dir.exists()
    created = store.initialize()
    assert created["sequence"] == 0
    assert created["previous_digest"] is None
    assert store.load_latest() == created
    assert len(list(store.journal_dir.glob("*.json"))) == 1


def test_normal_v14_append_survives_restart(tmp_path):
    store = state.StateStore(tmp_path)
    first = store.initialize()
    second = store.append_confirmed_signals(first, _v14_signals(date(2026, 8, 25)))
    reopened = state.StateStore(tmp_path).initialize()
    assert reopened == second
    assert reopened["sequence"] == 1
    assert reopened["previous_digest"] == first["digest"]


@pytest.mark.parametrize("with_confirmed_close", [False, True])
def test_missing_latest_never_rewrites_existing_journal(tmp_path, with_confirmed_close):
    store = state.StateStore(tmp_path)
    initial = store.initialize()
    if with_confirmed_close:
        latest = store.append_confirmed_signals(initial, _v14_signals(date(2026, 8, 25)))
    else:
        latest = initial
    before = {path.name: path.read_bytes() for path in store.journal_dir.glob("*.json")}
    # Simulate a lost pointer in an isolated restored deployment volume.
    store.latest_path.unlink()
    try:
        reopened = state.StateStore(tmp_path).initialize()
    except RuntimeError:
        # Failing closed is valid; silently creating another genesis is not.
        reopened = None
    after = {path.name: path.read_bytes() for path in store.journal_dir.glob("*.json")}
    assert after == before, "initialize overwrote append-only journal after latest.json was lost"
    if reopened is not None:
        assert reopened == latest, "initialize rolled a restored ledger back to a new genesis"


def test_corrupt_latest_fails_closed_and_keeps_journal(tmp_path):
    store = state.StateStore(tmp_path)
    store.initialize()
    before = {path.name: path.read_bytes() for path in store.journal_dir.glob("*.json")}
    store.latest_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(RuntimeError, match="无法读取"):
        state.StateStore(tmp_path).initialize()
    assert {path.name: path.read_bytes() for path in store.journal_dir.glob("*.json")} == before


def test_copy_of_real_local_v14_ledger_never_loses_history_when_pointer_missing(tmp_path):
    source = Path(__file__).resolve().parent / "runtime" / "ic_im_v1_4_r1"
    if not (source / "latest.json").is_file():
        pytest.skip("the optional local persisted research ledger is not present")
    # Verify the real source first; copying is read-only at the source.
    source_record = state.StateStore(source).load_latest()
    shutil.copytree(source / "journal", tmp_path / "journal")
    shutil.copy2(source / "latest.json", tmp_path / "latest.json")
    store = state.StateStore(tmp_path)
    assert store.load_latest() == source_record
    before = {path.name: path.read_bytes() for path in store.journal_dir.glob("*.json")}
    store.latest_path.unlink()
    try:
        recovered = store.initialize()
    except RuntimeError:
        recovered = None
    assert {path.name: path.read_bytes() for path in store.journal_dir.glob("*.json")} == before
    if recovered is not None:
        assert recovered == source_record


@pytest.mark.parametrize("field,value", [("market_date", date(2026, 8, 26)),
                                         ("close_confirmed", "false"),
                                         ("v14_build_id", "obsolete")])
def test_invalid_or_partial_close_keeps_entire_v14_chain_unchanged(tmp_path, field, value):
    store = state.StateStore(tmp_path)
    current = store.initialize()
    signals = _v14_signals(date(2026, 8, 25))
    signals["IM"][field] = value
    before = {path.name: path.read_bytes() for path in store.journal_dir.glob("*.json")}
    with pytest.raises(RuntimeError):
        store.append_confirmed_signals(current, signals)
    assert store.load_latest() == current
    assert {path.name: path.read_bytes() for path in store.journal_dir.glob("*.json")} == before


def test_stale_writer_cannot_advance_newer_v14_chain(tmp_path):
    store = state.StateStore(tmp_path)
    stale = store.initialize()
    committed = store.append_confirmed_signals(stale, _v14_signals(date(2026, 8, 25)))
    with pytest.raises(RuntimeError, match="另一请求"):
        store.append_confirmed_signals(stale, _v14_signals(date(2026, 8, 25)))
    assert store.load_latest() == committed
