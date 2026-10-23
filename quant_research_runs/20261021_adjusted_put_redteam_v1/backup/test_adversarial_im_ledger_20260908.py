"""Adversarial boundary tests; archived evidence is read-only, ledgers are temporary.

The Sept 8 signal is an archived *intraday* artifact. Tests deliberately turn
its flags into close-confirmed inputs to exercise the writer boundary; these
are fault-injection fixtures, never represented as a real Sept 8 close.
When the local archive is absent (CI), an explicit synthetic checkpoint exercises
the identical production boundary rather than skipping the regression tests.
"""
from copy import deepcopy
from datetime import date, datetime
import json
import os
from pathlib import Path
import shutil
import tempfile

import pytest

_IMPORT_STATE = tempfile.TemporaryDirectory(prefix="icim_adversarial_import_")
_OLD_ENV = {key: os.environ.get(key) for key in ("ICIM_STATE_DIR", "ICIM_REQUIRE_MIGRATION")}
os.environ["ICIM_STATE_DIR"] = _IMPORT_STATE.name
os.environ["ICIM_REQUIRE_MIGRATION"] = "0"
try:
    import poe_ic_im_v1_3_state as state
    import run_ic_im_v1_3_github_digest as runner
finally:
    for _key, _value in _OLD_ENV.items():
        if _value is None:
            os.environ.pop(_key, None)
        else:
            os.environ[_key] = _value

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / "outputs/im_put102_release_20260908/remote"


def _synthetic_fixture(tmp_path):
    before = state.bootstrap_record()
    before["verified_day"] = "2026-09-07"
    for anchor in before["products"].values():
        anchor["last_verified_day"] = "2026-09-07"
    before["source"] = "synthetic_test_checkpoint_only"
    before["digest"] = state._digest(before)
    store = state.StateStore(tmp_path / "ledger")
    store._atomic_write(store.journal_dir / "000000-2026-09-07.json", before)
    store._atomic_write(store.latest_path, before)
    signals = {}
    for product in ("IC", "IM"):
        anchor = before["products"][product]
        held = anchor["post_core_contract"]
        plan = runner.strategy.quarter_roll.roll_state(
            product, held, [held, f"{product}2612"], date(2026, 9, 8), True,
            lambda c: runner.strategy._third_friday(*runner.strategy._contract_month(c)),
            runner.strategy._is_exchange_trading_day,
        )
        signal = dict(plan, product=product, market_date="2026-09-08",
                      state_anchor_day="2026-09-07", next_trade_date="2026-09-09",
                      close_confirmed=True, market_phase="收盘后", next_core=f"{product}2612",
                      momentum_current_weight=anchor["verified_next_momentum_weight"],
                      momentum_next_weight=0.0, grid_current=anchor["verified_next_grid_units"],
                      grid_target=0.0, call_target_qty_normalized=0.0,
                      call_target_contract=None, call_target_expiry=None,
                      call_target_strike=None, call_target_threat_roll_count=0)
        if product == "IC":
            signal.update(put_target_contract="510500P2612M07500", put_target_total_qty=14,
                          core_put_target_delta=0.25, momentum_put_target_delta=0.0,
                          total_put_target_delta=0.25, core_put_driver="synthetic_test",
                          momentum_put_driver="synthetic_test")
        else:
            signal.update(im_put_execution_revision=runner.strategy.IM_PUT_EXECUTION_REVISION,
                          im_execution_fix_revision=runner.strategy.IM_EXECUTION_FIX_REVISION,
                          im_put_policy_revision=runner.strategy.IM_PUT_POLICY_REVISION,
                          im_put_target_moneyness=1.02, momentum_120=-0.04,
                          put_reference_price=7600.0, put_reference_future=held,
                          option_monthly_reset_due=False, v13_parent_puts_per_full_core=3)
            for sleeve in ("core", "momentum"):
                signal[f"{sleeve}_put_current_qty_normalized"] = anchor[f"verified_{sleeve}_put_qty_normalized"]
                signal[f"{sleeve}_put_current_contract"] = anchor[f"post_{sleeve}_put_contract"]
            signal.update(total_put_current_qty_normalized=anchor["verified_total_put_qty_normalized"],
                          core_put_target_qty_normalized=1.5,
                          core_put_target_contract=anchor["post_core_put_contract"],
                          momentum_put_target_qty_normalized=0.0,
                          momentum_put_target_contract=None, total_put_target_qty_normalized=1.5)
        signals[product] = signal
    return store, before, signals


def _fixture(tmp_path):
    archived_ledger = EVIDENCE / "ic-im-v1-3-r7-put-monthly-fix1-ledger"
    archived_result = EVIDENCE / "ic-im-v1-3-r7-post-close-digest/strategy-artifacts/result.json"
    if not archived_ledger.exists() or not archived_result.exists():
        return _synthetic_fixture(tmp_path)
    target = tmp_path / "ledger"
    shutil.copytree(archived_ledger, target)
    store = state.StateStore(target)
    current = store.load_latest()
    assert current["verified_day"] == "2026-09-07"
    signals = json.loads(archived_result.read_text(encoding="utf-8"))["signals"]
    # The input shape is archived; this fault-injection fixture represents a
    # newly produced result and must declare the current execution guard version.
    signals["IM"]["im_execution_fix_revision"] = runner.strategy.IM_EXECUTION_FIX_REVISION
    for signal in signals.values():
        signal["close_confirmed"] = True
        signal["market_phase"] = "收盘后"
    return store, current, signals


def test_control_complete_policy_advances_and_builds_close_report(tmp_path):
    store, before, signals = _fixture(tmp_path)
    after = store.append_confirmed_signals(before, signals)
    assert after["sequence"] == before["sequence"] + 1
    runner.validate_close_artifact(completed_day=date(2026, 9, 8), latest=after, observed=after["signals"])


def test_ci_without_local_archive_exercises_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(__import__(__name__), "EVIDENCE", tmp_path / "missing_archive")
    test_control_complete_policy_advances_and_builds_close_report(tmp_path)


def test_new_date_missing_execution_revision_must_not_commit_old_policy(tmp_path):
    store, before, signals = _fixture(tmp_path)
    signals["IM"].pop("im_put_execution_revision")
    signals["IM"].pop("im_put_policy_revision")
    signals["IM"]["im_put_target_moneyness"] = 0.95
    with pytest.raises(RuntimeError):
        store.append_confirmed_signals(before, signals)
    assert store.load_latest()["digest"] == before["digest"]


def test_rejected_daily_report_must_not_already_have_poisoned_ledger(tmp_path, monkeypatch):
    store, before, signals = _fixture(tmp_path)
    signals["IM"].pop("im_put_execution_revision")
    signals["IM"].pop("im_put_policy_revision")
    signals["IM"]["im_put_target_moneyness"] = 0.95
    try:
        store.append_confirmed_signals(before, signals)
    except RuntimeError:
        assert store.load_latest()["digest"] == before["digest"]
        return
    monkeypatch.setenv("ICIM_REQUIRE_MIGRATION", "0")
    with pytest.raises(RuntimeError, match="已修正月度Put执行版本"):
        runner.build_artifacts(
            state_dir=store.root, out_dir=tmp_path / "report",
            clock=datetime(2026, 9, 8, 18, tzinfo=runner.strategy.BEIJING),
            max_sessions=20, mode="close",
        )
    after = store.load_latest()
    coordinator = runner.LedgerCoordinator(store)
    assert coordinator.catch_up_until_current(
        datetime(2026, 9, 8, 18, tzinfo=runner.strategy.BEIJING)
    ) == 0, "Writer already believes Sept 8 is complete, so retry cannot regenerate it"
    assert after["digest"] == before["digest"], (
        "BUG: obsolete 95% policy was committed before report rejection; "
        f"sequence {before['sequence']} -> {after['sequence']}, "
        "and normal catch-up now skips the poisoned day"
    )


def test_nonzero_call_missing_contract_must_not_enter_ledger(tmp_path):
    store, before, signals = _fixture(tmp_path)
    signals["IM"].update(call_target_qty_normalized=-1.0,
                         call_target_contract=None, call_target_expiry=None,
                         call_target_strike=float("nan"))
    with pytest.raises(RuntimeError):
        store.append_confirmed_signals(before, signals)


def test_realtime_nan_core_put_quantity_must_not_pass_delivery_gate(tmp_path):
    _, before, signals = _fixture(tmp_path)
    for signal in signals.values():
        signal.update(close_confirmed=False, market_phase="盘中")
    signals["IM"]["core_put_target_qty_normalized"] = float("nan")
    with pytest.raises(RuntimeError):
        runner.validate_realtime_artifact(
            clock=datetime(2026, 9, 8, 14, 30, tzinfo=runner.strategy.BEIJING),
            completed_day=date(2026, 9, 7), before=before, after=deepcopy(before),
            observed=signals,
        )


@pytest.mark.parametrize("field,value", [
    ("core_put_target_qty_normalized", float("inf")),
    ("momentum_put_current_qty_normalized", -0.5),
    ("total_put_target_qty_normalized", 0.0),
    ("core_put_target_qty_normalized", True),
    ("core_put_target_qty_normalized", "1.5"),
    ("v13_parent_puts_per_full_core", 4),
])
def test_bad_option_quantity_never_writes_or_passes_delivery(tmp_path, field, value):
    store, before, signals = _fixture(tmp_path)
    signals["IM"][field] = value
    with pytest.raises(RuntimeError):
        store.append_confirmed_signals(before, signals)
    assert store.load_latest()["digest"] == before["digest"]
    with pytest.raises(RuntimeError):
        state.validate_delivery_values(signals["IM"], "IM")


@pytest.mark.parametrize("field,value", [
    ("call_target_strike", float("nan")),
    ("call_target_strike", 8600.0),
    ("call_target_expiry", "2026-11-20"),
    ("call_target_contract", "MO2612-P-8500"),
    ("call_target_qty_normalized", 0.0),
])
def test_call_contract_identity_must_match_quantity_strike_and_expiry(tmp_path, field, value):
    store, before, signals = _fixture(tmp_path)
    signals["IM"].update(call_target_qty_normalized=-1.0,
                         call_target_contract="MO2612-C-8500",
                         call_target_expiry="2026-12-18", call_target_strike=8500.0)
    state.validate_delivery_values(signals["IM"], "IM")
    signals["IM"][field] = value
    with pytest.raises(RuntimeError):
        store.append_confirmed_signals(before, signals)
    assert store.load_latest()["digest"] == before["digest"]


@pytest.mark.parametrize("revision", [None, "im_put_execution_guards_old"])
def test_new_producer_requires_current_execution_fix_revision(tmp_path, revision):
    store, before, signals = _fixture(tmp_path)
    signals["IM"]["im_execution_fix_revision"] = revision
    with pytest.raises(RuntimeError, match="执行防线版本"):
        store.append_confirmed_signals(before, signals)
    with pytest.raises(RuntimeError, match="执行防线版本"):
        state.validate_delivery_values(signals["IM"], "IM")
    assert store.load_latest()["digest"] == before["digest"]


def test_historical_record_read_does_not_promote_old_producer(tmp_path):
    store, before, signals = _fixture(tmp_path)
    archived = store.append_confirmed_signals(before, signals)
    archived["signals"]["IM"].pop("im_execution_fix_revision")
    archived["digest"] = state._digest(archived)
    # Only a temporary historical fixture is changed; production history is
    # neither migrated nor rewritten by the implementation.
    store._atomic_write(store.journal_dir / f"{archived['sequence']:06d}-2026-09-08.json", archived)
    store._atomic_write(store.latest_path, archived)
    assert store.load_latest()["digest"] == archived["digest"]
    with pytest.raises(RuntimeError, match="执行防线版本"):
        runner.validate_close_artifact(completed_day=date(2026, 9, 8),
                                       latest=archived, observed=archived["signals"])


def test_orphan_from_old_producer_is_not_recovered_as_fixed(tmp_path, monkeypatch):
    store, before, signals = _fixture(tmp_path)
    original_write = store._atomic_write

    def fail_latest(path, record):
        if path == store.latest_path:
            raise OSError("simulated crash before latest replace")
        original_write(path, record)

    monkeypatch.setattr(store, "_atomic_write", fail_latest)
    with pytest.raises(OSError, match="simulated crash"):
        store.append_confirmed_signals(before, signals)
    monkeypatch.setattr(store, "_atomic_write", original_write)
    orphan_path = store.journal_dir / f"{before['sequence'] + 1:06d}-2026-09-08.json"
    orphan = json.loads(orphan_path.read_text(encoding="utf-8"))
    orphan["signals"]["IM"].pop("im_execution_fix_revision")
    orphan["digest"] = state._digest(orphan)
    original_write(orphan_path, orphan)
    with pytest.raises(RuntimeError, match="执行防线版本"):
        store.append_confirmed_signals(before, signals)
    assert store.load_latest()["digest"] == before["digest"]
    assert json.loads(orphan_path.read_text(encoding="utf-8"))["digest"] == orphan["digest"]
