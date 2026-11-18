"""Attack the repaired research guards with variants of frozen real inputs."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import replay_guards as guards


SCRIPT = Path(__file__).resolve()
FROZEN = guards.FROZEN
FRESH = FROZEN / "fresh_sources"
DAILY = FROZEN / "full_account_daily.csv.gz"
BASE = FROZEN / "complete_signal_close_account_daily.csv.gz"
PARENT = FROZEN / "full_account_verification.json"
PREVIOUS = "2026-11-17"


def _rejected(call, expected: str) -> str:
    try:
        call()
    except RuntimeError as exc:
        if expected not in str(exc):
            raise AssertionError(f"expected failure containing {expected!r}, got {exc!s}") from exc
        return str(exc)
    raise AssertionError(f"guard accepted a negative control: {expected}")


def _load_original(scratch):
    sys.path.insert(0, str(FROZEN))
    spec = importlib.util.spec_from_file_location("original_frozen_replay_guard", FROZEN / "replay_latest.py")
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    os.environ["ICIM_STATE_DIR"] = str(scratch / "isolated_state")
    sys.path.insert(0, str(guards.ROOT))
    return original


def _market_cases(original, scratch):
    raw = FRESH / "extended_inputs"
    manifest = FRESH / "refresh_manifest.json"
    signal, mark, _ = guards.verify_frozen_market_snapshot(raw, manifest)
    terminal = guards.verify_terminal_official_prices(raw)
    assert terminal == {"IC": 4, "IM": 4, "MO_put": 159}
    altered = scratch / "market" / "extended_inputs"
    shutil.copytree(raw, altered)
    altered_manifest = altered.parent / "refresh_manifest.json"
    shutil.copy2(manifest, altered_manifest)
    changed_file = altered / "cffex_ic_contracts.csv"
    frame = pd.read_csv(changed_file, float_precision="round_trip")
    row = frame.index[frame.date.eq(mark.isoformat()) & frame.contract.eq("IC2612")].item()
    actual_close = float(frame.at[row, "close"])
    frame.at[row, "close"] = actual_close + 10
    frame.to_csv(changed_file, index=False)
    file_only = _rejected(lambda: guards.verify_frozen_market_snapshot(altered, altered_manifest), "input hashes differ")
    changed_manifest = guards.read_receipt(altered_manifest)
    changed_manifest["extended_input_hashes"][changed_file.name] = guards.sha256(changed_file)
    altered_manifest.write_text(json.dumps(changed_manifest, ensure_ascii=False), encoding="utf-8")
    assert original.read_snapshot(altered)[:2] == (signal, mark)
    coordinated = _rejected(lambda: guards.verify_frozen_market_snapshot(altered, altered_manifest), "manifest does not match")
    independent = _rejected(lambda: guards.verify_terminal_official_prices(altered), "differ from independent XML: IC2612")
    shutil.copy2(manifest, altered_manifest)
    shutil.copy2(raw / changed_file.name, changed_file)
    archived = altered / "cffex_raw/202207.zip"
    archived.write_bytes(archived.read_bytes() + b"tail mutation")
    assert original.read_snapshot(altered)[:2] == (signal, mark)
    archive = _rejected(lambda: guards.verify_frozen_market_snapshot(altered, altered_manifest), "independently pinned contents")
    return {
        "correct_market_cutoffs": [signal.isoformat(), mark.isoformat()],
        "official_terminal_rows": terminal,
        "original_price": actual_close,
        "altered_price": actual_close + 10.0,
        "original_guard_accepted_coordinated_price_and_manifest_mutation": True,
        "original_guard_accepted_nested_zip_mutation": True,
        "repaired_quote_only": file_only,
        "repaired_coordinated_manifest_and_quote": coordinated,
        "independent_terminal_xml": independent,
        "repaired_nested_zip": archive,
    }


def _resume_cases(parent, scratch):
    receipt = FROZEN / "second_attempt/verified_input_stages.json"
    inputs = FROZEN / "inputs"
    accepted = guards.verify_resume_receipt(
        receipt, inputs, manifest_sha256=guards.MARKET_MANIFEST_SHA256,
        formal_source_sha256=parent["formal_source_sha256"],
    )
    assert len(accepted["input_stage_hashes"]) == 8
    fixture = scratch / "resume"
    fixture.mkdir()
    for name in guards.REQUIRED_STAGE_FILES:
        shutil.copy2(inputs / name, fixture)
    candidate = "native_fix4_seller_candidates_v1.csv.gz"
    changed = fixture / "receipt.json"

    def verify(expected_pin=None):
        return guards.verify_resume_receipt(
            changed, fixture, manifest_sha256=guards.MARKET_MANIFEST_SHA256,
            formal_source_sha256=parent["formal_source_sha256"],
            expected_receipt_sha256=expected_pin,
        )

    changed.write_text(json.dumps({**accepted, "status": "FAIL"}), encoding="utf-8")
    pin = _rejected(verify, "receipt does not match")
    fail = _rejected(lambda: verify(guards.sha256(changed)), "receipt did not pass")
    hashes = dict(accepted["input_stage_hashes"])
    del hashes[candidate]
    changed.write_text(json.dumps({**accepted, "input_stage_hashes": hashes}), encoding="utf-8")
    missing = _rejected(lambda: verify(guards.sha256(changed)), "exactly the eight required")
    changed.write_text(receipt.read_text(encoding="utf-8").replace(
        '"input_stage_hashes": {',
        '"input_stage_hashes": {"' + candidate + '":"' + "c" * 64 + '",',
    ), encoding="utf-8")
    duplicate = _rejected(lambda: verify(guards.sha256(changed)), "duplicate JSON key")
    frame = pd.read_csv(fixture / candidate, float_precision="round_trip")
    row = frame.index[frame["product"].eq("IC") & frame.signal_date.eq("2026-11-05")].item()
    frame.at[row, "seller_permission"] = not bool(frame.at[row, "seller_permission"])
    frame.to_csv(fixture / candidate, index=False, compression="gzip")
    shutil.copy2(receipt, changed)
    mutant = _rejected(verify, "saved research stage changed")
    return {
        "actual_eight_member_receipt_passed": True,
        "independent_receipt_pin_rejects_rewrite": pin,
        "failure_status_rejected_even_after_independent_pin": fail,
        "missing_stage_key_rejected_even_after_independent_pin": missing,
        "duplicate_stage_key_rejected_even_after_independent_pin": duplicate,
        "modified_candidate_rejected": mutant,
    }


def _account_and_chart_cases(original, parent):
    import poe_ic_im_mainline_v1_4_bot as formal

    actual = pd.read_csv(DAILY, float_precision="round_trip")
    previous = pd.read_csv(BASE, float_precision="round_trip")
    accepted = guards.compare_prefix(actual, previous, PREVIOUS)
    row = actual.index[actual["product"].eq("IC") & actual.date.eq("2026-11-16")].item()
    modified = actual.copy()
    modified.at[row, "cash_close"] = np.nan
    assert original.compare_prefix(modified, previous, PREVIOUS)["status"] == "PASS"
    null = _rejected(lambda: guards.compare_prefix(modified, previous, PREVIOUS), "non-finite financial field")
    modified.at[row, "cash_close"] = np.inf
    inf = _rejected(lambda: guards.compare_prefix(modified, previous, PREVIOUS), "non-finite financial field")
    modified.at[row, "cash_close"] = actual.at[row, "cash_close"] + 0.01
    changed = _rejected(lambda: guards.compare_prefix(modified, previous, PREVIOUS), "economic close-prefix changed")
    days = guards.expected_exchange_days(formal, "2025-11-17", "2026-11-18")
    calendar_control = guards.verify_chart_gate(actual, parent, officially_expected_days=days)
    assert calendar_control["rows"] == {"IC": 244, "IM": 244}
    failed = _rejected(lambda: guards.verify_chart_gate(
        actual, {**parent, "status": "FAIL"}, officially_expected_days=days), "parent account receipt did not pass")
    missing = "2026-10-30"
    shortened = actual.loc[actual.date.ne(missing)].copy()
    synchronized_missing = _rejected(lambda: guards.verify_chart_gate(
        shortened, parent, officially_expected_days=days), "Missing ['2026-10-30']")
    return {
        "actual_finite_prefix_passed": accepted,
        "original_compare_accepted_na": True,
        "nan_rejected": null,
        "inf_rejected": inf,
        "finite_one_cent_change_rejected": changed,
        "actual_chart_calendar_passed": calendar_control,
        "failed_parent_receipt_rejected": failed,
        "common_missing_actual_trading_day_rejected": synchronized_missing,
    }


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--scratch", type=Path, required=True)
    cli.add_argument("--out", type=Path, required=True)
    args = cli.parse_args()
    original = _load_original(args.scratch)
    parent = guards.read_receipt(PARENT)
    evidence = {
        "status": "PASS_REPAIRED_RESEARCH_GUARDS",
        "market": _market_cases(original, args.scratch),
        "resume": _resume_cases(parent, args.scratch),
        "account_and_chart": _account_and_chart_cases(original, parent),
        "sha256": {str(path.relative_to(guards.ROOT)): guards.sha256(path) for path in (
            SCRIPT, SCRIPT.parent / "replay_guards.py", DAILY, BASE, PARENT,
            FRESH / "refresh_manifest.json", FROZEN / "second_attempt/verified_input_stages.json",
        )},
    }
    args.out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
