"""Read-only review of the real checkout; tests use an isolated model ledger.

No live market fetch, canonical ledger append, mail or remote mutation is run.
Existing unit fixtures are implementation evidence, not investment performance.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASELINE = [
    "test_delivery_transport_retry.py", "test_ohlcv_provider_validation.py",
    "test_ic_im_v1_4_policy.py", "test_poe_ic_im_v1_4_state.py",
    "test_migrate_ic_im_v1_3_r7_to_v1_4_r1_state.py",
    "test_ic_im_v1_4_integration_guards.py", "test_run_ic_im_v1_4_github_digest.py",
    "test_adversarial_icim_delivery.py", "test_im_put_policy.py",
    "test_adversarial_im_put_execution_20260908.py",
    "test_adversarial_im_ledger_20260908.py", "test_adversarial_im_report_iv_20260908.py",
    "test_im_put_price_basis.py", "test_live_mo_put_midpoint_20260908.py",
    "test_grid_half_release_20260913.py", "test_im_grid160_half_release_20260914.py",
    "test_ic_im_v1_4_ic_csi500_forward.py", "test_adversarial_market_20261007.py",
    "test_adversarial_state_20261007.py", "test_adversarial_delivery_20261007.py",
    "test_adversarial_policy_20261007.py", "test_adversarial_cffex_transport_20261009.py",
    "test_adversarial_cffex_close_runner_20261009.py", "test_tencent_ohlcv_pagination_20261012.py",
]
ADDITIONAL = [
    "test_ic_im_v1_4_repeat_roll_20260925.py",
    "test_ic_im_v1_4_market_maker_quotes_20260925.py",
    "test_ic_im_fear_grid_v1.py", "test_ic_im_daily_valuation.py",
    "test_ic_im_chinabond.py", "test_ic_im_quarter_roll_v1_3.py",
]


def dump(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def snapshot():
    paths = set(ROOT.glob("*.py"))
    paths.update((ROOT / "docs").glob("*_spec.md*"))
    paths.add(ROOT / "notes/mainline_registry.md")
    paths.update((ROOT / "runtime/ic_im_v1_4_r1").rglob("*.json"))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths) if p.is_file()}


def main():
    started = time.perf_counter()
    before = snapshot()
    dump("protected_files_before.json", before)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    env = dict(os.environ)
    temp = Path("D:/Codex/tmp/icim_review_20261013_main")
    temp.mkdir(parents=True, exist_ok=True)
    env.update(ICIM_STATE_DIR=str(temp / "state"), ICIM_REQUIRE_MIGRATION="0",
               TEMP=str(temp), TMP=str(temp), PYTHONDONTWRITEBYTECODE="1",
               HYPOTHESIS_STORAGE_DIRECTORY=str(temp / "hypothesis"))
    command = [sys.executable, "-B", "-X", "utf8", "-m", "pytest", "-q",
               "-p", "no:cacheprovider", "--junitxml", str(OUT / "regression.xml"),
               *BASELINE, *ADDITIONAL]
    dump("regression_command.json", {"cwd": str(ROOT), "head": head, "command": command,
         "environment_overrides": {k: env[k] for k in ["ICIM_STATE_DIR", "ICIM_REQUIRE_MIGRATION",
         "TEMP", "TMP", "PYTHONDONTWRITEBYTECODE", "HYPOTHESIS_STORAGE_DIRECTORY"]},
         "baseline_files": BASELINE, "additional_files": ADDITIONAL,
         "note": "test_poe_ic_im_v1_4_state.py imports state_guards; do not collect twice."})
    with (OUT / "regression.log").open("w", encoding="utf-8") as log:
        run = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=240)
    counts = {}
    if (OUT / "regression.xml").exists():
        for group, files in [("cumulative_24", BASELINE), ("additional_6", ADDITIONAL)]:
            modules = {Path(p).stem for p in files}
            cases = [c for c in ET.parse(OUT / "regression.xml").getroot().iter("testcase")
                     if c.attrib.get("classname", "").split(".")[0] in modules]
            counts[group] = {"tests": len(cases), "failures": sum(c.find("failure") is not None for c in cases),
                             "errors": sum(c.find("error") is not None for c in cases),
                             "skipped": sum(c.find("skipped") is not None for c in cases)}
    frozen = []
    for shafile in sorted((ROOT / "docs").glob("ic_im*spec.md.sha256")):
        expected = shafile.read_text(encoding="utf-8-sig").strip().split()[0]
        target = Path(str(shafile)[:-7])
        actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
        frozen.append({"path": str(target.relative_to(ROOT)), "expected": expected,
                       "actual": actual, "match": expected.lower() == actual})
    sys.path.insert(0, str(ROOT))
    os.environ.update({"ICIM_STATE_DIR": str(temp / "readonly_state"), "ICIM_REQUIRE_MIGRATION": "0"})
    import poe_ic_im_v1_4_state as state
    canonical = state.StateStore(ROOT / "runtime/ic_im_v1_4_r1").load_latest()
    after = snapshot()
    dump("protected_files_after.json", after)
    summary = {"head": head, "exit_code": run.returncode, "counts": counts,
               "wall_seconds": round(time.perf_counter() - started, 3),
               "canonical_ledger": {k: canonical.get(k) for k in ["sequence", "verified_day", "digest", "strategy_revision"]},
               "canonical_json_files": len(list((ROOT / "runtime/ic_im_v1_4_r1").rglob("*.json"))),
               "protected_file_count": len(before), "protected_files_unchanged": before == after,
               "changed_protected_paths": sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k)),
               "frozen_spec_hashes": frozen,
               "evidence_scope": "Existing offline tests, frozen-spec hashes, read-only canonical hash-chain load; no performance recertification or external delivery."}
    dump("review_checks.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "frozen_spec_hashes"}, ensure_ascii=False, indent=2))
    print(f"frozen_spec_hashes={len(frozen)}, mismatches={sum(not x['match'] for x in frozen)}")
    return run.returncode


if __name__ == "__main__":
    raise SystemExit(main())
