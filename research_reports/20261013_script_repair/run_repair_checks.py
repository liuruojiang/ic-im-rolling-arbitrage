"""Repeat the complete prior review suite plus repair cases in isolated state."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
PRIOR = ROOT / "research_reports/20261013_latest_adversarial_review"
NEW_TESTS = [
    "test_ic_im_ordinary_put_route_fix_20261013.py",
    "test_ic_im_quote_snapshot_guard_20261013.py",
    "test_ic_im_split_put_summary_20261013.py",
]


def dump(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot():
    paths = set(ROOT.glob("*.py"))
    paths.update((ROOT / "docs").glob("*_spec.md*"))
    paths.add(ROOT / "notes/mainline_registry.md")
    paths.update((ROOT / "runtime/ic_im_v1_4_r1").rglob("*.json"))
    paths.update(path for path in PRIOR.rglob("*") if path.is_file())
    return {str(path.relative_to(ROOT)): sha(path) for path in sorted(paths) if path.is_file()}


def main():
    started = time.perf_counter()
    prior_command = json.loads((PRIOR / "regression_command.json").read_text(encoding="utf-8"))
    groups = {"cumulative_24": prior_command["baseline_files"],
              "additional_6": prior_command["additional_files"], "new_repair_cases": NEW_TESTS}
    files = [name for group in groups.values() for name in group]
    assert all((ROOT / name).is_file() for name in files), "Repair test file missing"
    before = snapshot()
    dump("protected_before_tests.json", before)
    temp = Path("D:/Codex/tmp/icim_repair_20261013_main")
    temp.mkdir(parents=True, exist_ok=True)
    overrides = dict(ICIM_STATE_DIR=str(temp / "state"), ICIM_REQUIRE_MIGRATION="0",
                     TEMP=str(temp), TMP=str(temp), PYTHONDONTWRITEBYTECODE="1",
                     HYPOTHESIS_STORAGE_DIRECTORY=str(temp / "hypothesis"))
    env = dict(os.environ, **overrides)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    command = [sys.executable, "-B", "-X", "utf8", "-m", "pytest", "-q", "-p", "no:cacheprovider",
               "--junitxml", str(OUT / "regression.xml"), *files]
    dump("regression_command.json", {"cwd": str(ROOT), "head": head, "command": command,
         "environment_overrides": overrides, "groups": groups,
         "note": "State guard cases imported by test_poe_ic_im_v1_4_state.py are not collected twice."})
    with (OUT / "regression.log").open("w", encoding="utf-8") as log:
        run = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=240)
    counts = {}
    cases = list(ET.parse(OUT / "regression.xml").getroot().iter("testcase"))
    for group, names in groups.items():
        modules = {Path(name).stem for name in names}
        selected = [case for case in cases if case.attrib.get("classname", "").split(".")[0] in modules]
        counts[group] = {"tests": len(selected),
                         "failures": sum(case.find("failure") is not None for case in selected),
                         "errors": sum(case.find("error") is not None for case in selected),
                         "skipped": sum(case.find("skipped") is not None for case in selected)}
    frozen = []
    for hashfile in sorted((ROOT / "docs").glob("ic_im*spec.md.sha256")):
        expected = hashfile.read_text(encoding="utf-8-sig").strip().split()[0]
        target = Path(str(hashfile)[:-7])
        actual = sha(target) if target.is_file() else None
        frozen.append({"path": str(target.relative_to(ROOT)), "expected": expected,
                       "actual": actual, "match": expected.lower() == actual})
    os.environ.update(overrides)
    sys.path.insert(0, str(ROOT))
    import poe_ic_im_v1_4_state as state
    canonical = state.StateStore(ROOT / "runtime/ic_im_v1_4_r1").load_latest()
    after = snapshot()
    dump("protected_after_tests.json", after)
    original = json.loads((PRIOR / "protected_files_before.json").read_text(encoding="utf-8"))
    canonical_before = {name: digest for name, digest in original.items() if name.startswith("runtime")}
    canonical_after = {name: digest for name, digest in after.items() if name.startswith("runtime")}
    changed = sorted(name for name, digest in original.items() if after.get(name) != digest)
    summary = {"head": head, "exit_code": run.returncode, "counts": counts, "total_cases": len(cases),
               "wall_seconds": round(time.perf_counter() - started, 3),
               "canonical_ledger": {key: canonical.get(key) for key in
                                    ["sequence", "verified_day", "digest", "strategy_revision"]},
               "canonical_json_files": len(canonical_after),
               "canonical_unchanged_from_original_review": canonical_before == canonical_after,
               "protected_files_unchanged_during_tests": before == after,
               "changed_existing_paths_since_review": changed,
               "frozen_spec_hashes": frozen,
               "scope": "Local repair regression; no live fetch, performance certification, mail or deployment."}
    dump("repair_checks.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "frozen_spec_hashes"},
                     ensure_ascii=False, indent=2))
    print(f"frozen_spec_hashes={len(frozen)}, mismatches={sum(not row['match'] for row in frozen)}")
    if before != after or canonical_before != canonical_after or any(not row["match"] for row in frozen):
        return 2
    return run.returncode


if __name__ == "__main__":
    raise SystemExit(main())
