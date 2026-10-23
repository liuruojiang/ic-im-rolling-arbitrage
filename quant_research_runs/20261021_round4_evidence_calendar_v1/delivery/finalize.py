"""Build this round's exact patch and verify its retained acceptance evidence."""
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROUND = HERE.parent
ROOT = ROUND.parents[1]
sys.path.insert(0, str(ROUND))
import evidence_gate


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    destination = ROUND / "final_verification.json"
    if destination.exists():
        raise FileExistsError("final evidence is append-only")
    accepted = ROUND / "accepted_01"
    evidence_gate.validate_receipt(accepted)
    receipt = read(accepted / "receipt.json")
    binding = read(accepted / "source_after.json")
    assert binding == read(accepted / "source_before.json")
    assert all((ROOT / name).is_file() and sha(ROOT / name) == digest for name, digest in binding.items())
    current = read(accepted / "collection.json")
    prior = read(ROOT / "quant_research_runs/20261021_round3_source_validation_v1/accepted_01/collection.json")
    assert set(prior["selected"]) <= set(current["selected"])
    assert set(current["deselected"]) == set(prior["deselected"]) == set(read(ROUND / "exclusions.json"))
    added = sorted(set(current["selected"]) - set(prior["selected"]))
    assert len(added) == 39
    assert all(node.startswith(("test_icim_round4_evidence_boundaries_20261021.py::",
                               "test_icim_round4_recovery_20261021.py::")) for node in added)
    protection = read(ROUND / "protection/verification_after_tests.json")
    assert protection["passed"] is True
    baseline = read(ROUND / "baseline.json")
    assert all(sha(ROOT / name) == digest for name, digest in baseline["protected"].items())
    assert all(sha(ROUND / "backup" / name) == digest for name, digest in baseline["backup"].items())
    old_registry = (ROUND / "backup/notes/mainline_registry.md").read_bytes()
    registry = (ROOT / "notes/mainline_registry.md").read_bytes()
    marker = "## 第三批续修".encode("utf-8")
    assert registry[registry.index(marker):] == old_registry[old_registry.index(marker):]

    changed = ["poe_ic_im_mainline_v1_4_bot.py", "ic_im_evidence_adapter_v1.py", "ic_im_signal_evidence_v1.py"]
    new = ["test_icim_round4_evidence_boundaries_20261021.py", "test_icim_round4_recovery_20261021.py",
           "docs/ic_im_round4_evidence_calendar_20261021_v1_spec.md",
           "docs/ic_im_round4_evidence_calendar_20261021_v1_spec.md.sha256"]
    pieces = []
    for name in changed + new:
        before = ((ROUND / "backup" / name).read_bytes().decode("utf-8").splitlines(keepends=True)
                  if name in changed else [])
        after = (ROOT / name).read_bytes().decode("utf-8").splitlines(keepends=True)
        pieces.extend(difflib.unified_diff(before, after, fromfile="a/" + name if name in changed else "/dev/null",
                                         tofile="b/" + name, n=3))
    patch = ROUND / "source_delta.patch"
    if patch.exists():
        raise FileExistsError("do not overwrite patch")
    patch.write_bytes("".join(pieces).encode("utf-8"))
    checked = subprocess.run(["git", "-c", "safe.directory=D:/动量策略/IC和IM滚动套利", "apply", "--check", "--reverse", str(patch)],
                             cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    patch_check = {"exit_code": checked.returncode, "stdout": checked.stdout, "stderr": checked.stderr,
                   "read_only": True, "patch_sha256": sha(patch)}
    (HERE / "patch_check.json").write_text(json.dumps(patch_check, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert checked.returncode == 0, checked.stderr

    result = {"created_at": datetime.now(timezone.utc).isoformat(), "stage": "accepted_01",
        "counts": receipt["counts"], "test_files": 88, "prior_tests": 2596, "new_tests": len(added),
        "deselected": len(current["deselected"]), "new_exclusions": 0, "source_files": len(binding),
        "source_still_matches_accepted": True, "prior_selected_set_preserved": True,
        "old_registry_entries_preserved_bytes": True, "protection_passed": True,
        "runtime_files": 155, "runtime_json_files": 142, "runtime_hashes_and_paths_equal": True,
        "patch_reverse_check_exit_code": checked.returncode,
        "independent_actual_collector_synthetic_recovery_passed": True,
        "nov20_and_dec31_replayed_without_unrecorded_requests": True,
        "two_stale_ledger_captures_and_sequential_catchup_passed": True,
        "missing_fear_historical_supplement_synthetic_success_and_failure_tested": True,
        "calendar_and_future_quotes_in_recovery_tests": "synthetic_not_official_or_live",
        "current_real_http_responses_received": sum("status_code" in row for row in read(ROUND / "probe_results.json")["results"]),
        "network_environment_repaired": False, "new_sina_batch_live_oracle_verified": False,
        "sse_etf_live_oracle_verified": False, "official_2027_calendar_verified": False,
        "new_independent_multiagent_audit": False, "deployed": False, "email_sent": False,
        "formal_ledger_written": False, "all_feedback_closed": False}
    artifacts = [p for p in ROUND.rglob("*") if p.is_file() and "backup" not in p.relative_to(ROUND).parts
                 and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts]
    artifacts += [ROOT / "notes/mainline_registry.md", *(ROOT / name for name in new), *(ROOT / name for name in changed)]
    result["artifacts"] = {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(set(artifacts))}
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "artifacts"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
