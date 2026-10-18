"""Read-only publication audit; all execution happens in disposable exports."""
from __future__ import annotations
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(r"D:\动量策略\IC和IM滚动套利")
CLOUD = Path(r"D:\Codex\tmp\icim_release_20261013\cloud")
OUT = ROOT / "research_reports/20261013_release_sync"
BOX = OUT / "post_sync_policy_sandbox"
PIN = "427cf45fd78710583c8028f7eca1ed88968036ee"
MAIN = "43cbc5e36179425d99e2c9447f9745109215339b"
CLOUD_MAIN = "0f0fec83f549f2db942fa5633593af48dc90476a"
BASE = "68dcc8d978ae5dae4a60c5fbd4a5c8c3c2a2d300"
PYTHON = sys.executable


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def snapshot(root):
    return {p.relative_to(root).as_posix(): sha(p.read_bytes()) for p in sorted(root.rglob("*")) if p.is_file()}


def export(root, commit, destination):
    destination.mkdir(parents=True, exist_ok=False)
    data = git(root, "archive", "--format=zip", commit)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            assert (destination / member.filename).resolve().is_relative_to(destination.resolve())
        archive.extractall(destination)
    return sha(data)


def check_run(name, command, cwd, env):
    with (OUT / f"{name}.log").open("w", encoding="utf-8") as log:
        value = subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=60)
    if value.returncode:
        raise RuntimeError(f"{name} failed; see {name}.log")
    return {"name": name, "command": command, "cwd": str(cwd), "returncode": value.returncode}


def main():
    report = {"status": "IN_PROGRESS", "scope": "Independent read-only audit of exact released source plus isolated adversarial tests; no sends"}
    ledger = ROOT / "runtime/ic_im_v1_4_r1"
    report["production_ledger_before"] = snapshot(ledger)
    report["refs"] = {}
    for label, root, expected in (("strategy", ROOT, MAIN), ("cloud", CLOUD, CLOUD_MAIN)):
        head = git(root, "rev-parse", "HEAD").decode().strip()
        remote = git(root, "ls-remote", "origin", "refs/heads/main").decode().split()[0]
        assert head == remote == expected
        report["refs"][label] = {"local_head": head, "remote_main": remote}
    assert subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", PIN, MAIN]).returncode == 0
    preflight = json.loads((OUT / "policy_preflight.json").read_text(encoding="utf-8"))
    report["five_source_hashes"] = []
    for entry in preflight["source_files"]:
        rel = entry["path"]
        actual = sha(git(ROOT, "show", f"{PIN}:{rel}"))
        assert actual == entry["sha256"] == sha((ROOT / rel).read_bytes())
        report["five_source_hashes"].append({"path": rel, "sha256": actual, "matches_repaired_and_local": True})
    report["frozen_specs_unchanged"] = []
    for entry in preflight["repair_integrity"]["frozen_specs"]:
        rel = entry["path"].replace("\\", "/")
        old, new = git(ROOT, "show", f"{BASE}:{rel}"), git(ROOT, "show", f"{PIN}:{rel}")
        assert old == new == (ROOT / rel).read_bytes()
        report["frozen_specs_unchanged"].append({"path": rel, "sha256": sha(new)})
    changes = git(ROOT, "diff", "--name-only", BASE, PIN).decode().splitlines()
    assert len(changes) == 8 and not any("recovery" in p or p.startswith("research_reports/") for p in changes)
    report["strategy_execution_diff"] = changes
    tracked = git(ROOT, "ls-tree", "-r", "--name-only", PIN).decode().splitlines()
    assert not any("20261013_recovery_put_research" in p or Path(p).name in {"recovery_account.py", "recovery_cycle_controller.py"} for p in tracked)
    report["recovery_research_excluded"] = True
    daily = (CLOUD / ".github/workflows/ic-im-v1-4-daily-digest.yml").read_text()
    pins = set(re.findall(r"repository: liuruojiang/ic-im-rolling-arbitrage\s+ref: ([0-9a-f]{40})\b", daily))
    assert pins == {PIN}
    report["immutable_execution_pin"] = PIN
    for name in ("ic-im-v1-4-delivery-readiness.yml", "delivery-regression.yml"):
        source = (CLOUD / ".github/workflows" / name).read_text()
        assert "ic-im-v1-4-daily-digest.yml" in source and "pins" in source
    source = (CLOUD / ".github/workflows/delivery-regression.yml").read_text()
    block = source.split("- name: Test ICIM source failover and persistent ledger invariants", 1)[1].split("- name: Preserve", 1)[0]
    tests = re.findall(r"\btest_[A-Za-z0-9_]+\.py\b", block)
    expected = [p for p in json.loads((ROOT / "research_reports/20261013_script_repair/regression_command.json").read_text())["command"] if p.startswith("test_")]
    assert len(tests) == len(set(tests)) == 36
    assert set(expected) <= set(tests) and "test_ic_im_v1_4_state_guards.py" not in tests
    report["cloud_36_test_allowlist"] = tests
    report["local_33_are_subset"] = True
    BOX.mkdir(exist_ok=False)
    strategy = BOX / "strategy"
    automation = BOX / "automation"
    report["export_sha256"] = {"strategy": export(ROOT, PIN, strategy), "cloud": export(CLOUD, CLOUD_MAIN, automation)}
    env = dict(os.environ, ICIM_STATE_DIR=str(BOX / "state"), ICIM_REQUIRE_MIGRATION="0",
               TEMP=str(BOX), TMP=str(BOX), PYTHONDONTWRITEBYTECODE="1",
               HYPOTHESIS_STORAGE_DIRECTORY=str(BOX / "hypothesis"))
    fixture = automation / "tests/fixtures/icim/quarter_roll_20261013"
    manifest = json.loads((fixture / "manifest.json").read_text())
    copied = []
    for entry in manifest["files"]:
        payload = (fixture / entry["payload_relative_path"]).read_bytes()
        saved = (ROOT / entry["source_relative_path"]).read_bytes()
        assert payload == saved and sha(payload) == entry["payload_sha256"] == entry["source_sha256"]
        assert len(payload) == entry["payload_bytes"] == entry["source_bytes"]
        copied.append({"source": entry["source_relative_path"], "bytes": len(payload), "sha256": sha(payload)})
    assert len(copied) == 7 and sum(x["bytes"] for x in copied) == 1834809
    report["real_fixture_exact_source_parity"] = copied
    prepare = [PYTHON, "-B", "-X", "utf8", str(fixture / "prepare_fixture.py"), "--strategy-root", str(strategy)]
    check_run("post_sync_policy_fixture", prepare, automation, env)
    for source_rel, target_rel in (("tests/fixtures/icim/v14_ledger_20261008", "runtime/ic_im_v1_4_r1"),):
        shutil.copytree(automation / source_rel, strategy / target_rel)
    target = strategy / "data/ic_510500_put_proxy_validation_v1/sina_000905_index.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(automation / "tests/fixtures/icim/sina_000905_index.csv", target)
    identity = "import json; import ic_im_v1_4_policy as p; import poe_ic_im_mainline_v1_4_bot as b; import run_ic_im_v1_4_github_digest as r; import poe_ic_im_v1_4_state as s; print(json.dumps(dict(BUILD=b.BUILD_ID,RULE=p.RULE_REVISION,DELIVERY=r.DELIVERY_REVISION,schema=s.SCHEMA_VERSION,revision=s.STRATEGY_REVISION)))"
    ids = json.loads(subprocess.check_output([PYTHON, "-B", "-X", "utf8", "-c", identity], cwd=strategy, env=env))
    assert ids == {"BUILD": "v1.4-20261008-r1-ic-csi500-abs40-fix11", "RULE": "ic_im_v1_4_ic_csi500_ma105_w16_abs40_static_20261008_v1", "DELIVERY": "20261008-v14-ic-csi500-abs40-fix11", "schema": 4, "revision": "r1"}
    report["runtime_identities"] = ids
    groups = [("post_sync_policy_strategy", strategy, ["test_ic_im_ordinary_put_route_fix_20261013.py", "test_ic_im_split_put_summary_20261013.py", "test_ic_im_quote_snapshot_guard_20261013.py", "test_ic_im_quarter_roll_v1_3.py"]),
              ("post_sync_policy_cloud", automation, ["tests/test_ic_im_v1_4_ordinary_route_sync_20261013.py", "tests/test_ic_im_v1_4_put_display.py", "tests/test_ic_im_v1_4_daily_digest.py"])]
    report["tests"] = []
    for name, cwd, selection in groups:
        xml = OUT / f"{name}.xml"
        command = [PYTHON, "-B", "-X", "utf8", "-m", "pytest", "-q", "-p", "no:cacheprovider", *selection, "--basetemp", str(BOX / (name + "_tmp")), "--junitxml", str(xml)]
        result = check_run(name, command, cwd, env)
        parsed = ET.parse(xml)
        result.update(tests=len(parsed.findall(".//testcase")), failures=len(parsed.findall(".//failure")), errors=len(parsed.findall(".//error")), skipped=len(parsed.findall(".//skipped")))
        assert not any(result[k] for k in ("failures", "errors", "skipped"))
        report["tests"].append(result)
    latest = json.loads((ledger / "latest.json").read_text(encoding="utf-8"))
    previous = None
    records = sorted((ledger / "journal").glob("*.json"))
    for sequence, p in enumerate(records):
        item = json.loads(p.read_text(encoding="utf-8"))
        digest = sha(json.dumps({k:v for k,v in item.items() if k != "digest"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
        assert item["sequence"] == sequence and item["previous_digest"] == previous and item["digest"] == digest
        previous = digest
    assert item == latest and latest["verified_day"] == "2026-10-13" and latest["sequence"] == 13
    report["canonical_chain_read_only_validation"] = {"records": len(records), "sequence": latest["sequence"], "verified_day": latest["verified_day"], "digest": latest["digest"], "status": "PASS", "method": "Direct JSON reads and canonical SHA256; no StateStore or lock acquired"}
    report["production_ledger_after"] = snapshot(ledger)
    assert report["production_ledger_before"] == report["production_ledger_after"]
    report["production_ledger_unchanged"] = True
    for root, expected in ((ROOT, MAIN), (CLOUD, CLOUD_MAIN)):
        assert git(root, "rev-parse", "HEAD").decode().strip() == expected
        assert not git(root, "diff", "--name-only")
    report["production_tracked_code_unchanged"] = True
    report["status"] = "PASS_SOURCE_AND_ISOLATED_ADVERSARIAL_TESTS"
    report["delivery_boundary"] = "No readiness result, SMTP receipt, dedupe marker or inbox acceptance claimed. Parent owns those checks separately."
    (OUT / "post_sync_policy_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "tests": report["tests"], "canonical": report["canonical_chain_read_only_validation"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
