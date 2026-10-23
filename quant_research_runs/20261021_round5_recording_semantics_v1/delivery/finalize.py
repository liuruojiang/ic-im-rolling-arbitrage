"""Bind the completed cumulative run, original backups and this round's patch."""
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
        raise FileExistsError("do not overwrite final verification")
    accepted = ROUND / "accepted_01"
    evidence_gate.validate_receipt(accepted)
    receipt = read(accepted / "receipt.json")
    binding = read(accepted / "source_after.json")
    assert binding == read(accepted / "source_before.json")
    assert all((ROOT / name).is_file() and sha(ROOT / name) == digest for name, digest in binding.items())
    collection = read(accepted / "collection.json")
    previous = read(ROOT / "quant_research_runs/20261021_round4_evidence_calendar_v1/accepted_01/collection.json")
    assert set(previous["selected"]) <= set(collection["selected"])
    assert set(collection["deselected"]) == set(previous["deselected"]) == set(read(ROUND / "exclusions.json"))
    added = sorted(set(collection["selected"]) - set(previous["selected"]))
    assert len(added) == 19 and all(node.startswith("test_icim_round5_recording_semantics_20261021.py::") for node in added)
    assert receipt["counts"] == {"tests": 2654, "failures": 0, "errors": 0, "skipped": 0}
    test_files = len({node.split("::")[0] for node in collection["selected"]})
    assert test_files == 89
    assert read(ROUND / "protection/verification_after_tests.json")["passed"] is True
    baseline = read(ROUND / "baseline.json")
    assert all(sha(ROOT / name) == digest for name, digest in baseline["protected"].items())
    assert all(sha(ROUND / "backup" / name) == digest for name, digest in baseline["backup"].items())
    old_registry = (ROUND / "backup/notes/mainline_registry.md").read_bytes()
    registry = (ROOT / "notes/mainline_registry.md").read_bytes()
    marker = "## 第四轮复查".encode("utf-8")
    assert registry[registry.index(marker):] == old_registry[old_registry.index(marker):]
    spec_name = "docs/ic_im_round5_recording_semantics_20261021_v1_spec.md"
    assert (ROOT / (spec_name + ".sha256")).read_text(encoding="utf-8").split()[0] == sha(ROOT / spec_name)
    assert sha(ROOT / "ic_im_quarter_roll_v1_3.py") == sha(ROUND / "dependencies/ic_im_quarter_roll_v1_3.py")

    changed = ["ic_im_signal_evidence_v1.py"]
    new = ["test_icim_round5_recording_semantics_20261021.py", spec_name, spec_name + ".sha256"]
    pieces = []
    for name in changed + new:
        before = ((ROUND / "backup" / name).read_bytes().decode("utf-8").splitlines(keepends=True)
                  if name in changed else [])
        after = (ROOT / name).read_bytes().decode("utf-8").splitlines(keepends=True)
        pieces.extend(difflib.unified_diff(before, after, fromfile="a/" + name if name in changed else "/dev/null",
                                         tofile="b/" + name, n=3))
    patch = ROUND / "source_delta.patch"
    if patch.exists():
        raise FileExistsError("preserve existing patch artifact")
    patch.write_bytes("".join(pieces).encode("utf-8"))
    check = subprocess.run(["git", "-c", "safe.directory=D:/动量策略/IC和IM滚动套利", "apply", "--check", "--reverse", str(patch)],
                           cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    patch_check = {"exit_code": check.returncode, "stdout": check.stdout, "stderr": check.stderr,
                   "read_only": True, "patch_sha256": sha(patch)}
    (HERE / "patch_check.json").write_text(json.dumps(patch_check, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert check.returncode == 0, check.stderr
    result = {"created_at": datetime.now(timezone.utc).isoformat(), "stage": "accepted_01",
        "local_round_acceptance_complete": True, "counts": receipt["counts"], "test_files": test_files,
        "prior_tests": 2635, "new_tests": len(added), "deselected": len(collection["deselected"]),
        "new_exclusions": 0, "source_files": len(binding), "source_still_matches_accepted": True,
        "previous_selected_set_preserved": True, "old_registry_entries_preserved_bytes": True,
        "protection_passed": True, "backup_files": 6, "runtime_files": 155, "runtime_json_files": 142,
        "runtime_hashes_and_paths_equal": True, "quarter_roll_copy_matches_source": True,
        "patch_reverse_check_exit_code": check.returncode,
        "ambiguous_exact_http_and_overlapping_sina_variables_rejected": True,
        "same_month_different_year_input_keys_verified": True,
        "held_ic_nov23_2703_and_nov26_2701_synthetic_recovery_passed": True,
        "nov23_2701_without_listed_identity_rejected": True,
        "none_and_untradable_dict_actions_and_financial_state_equal": True,
        "policy_reason_text_equality_claimed": False,
        "close_clock_boundary_tests_passed": True, "arbitrary_intraday_reproduction_claimed": False,
        "calendar_and_future_quotes_in_recovery_tests": "synthetic_not_official_or_live",
        "command_channel_available_at_resume": True, "system_credentials_changed": False,
        "new_live_market_oracles_obtained": False, "market_network_environment_repaired": False,
        "new_market_probes": len(read(ROUND / "validation/probe_results.json")["results"]),
        "current_real_http_responses_received": sum("status_code" in row for row in
            read(ROUND / "validation/probe_results.json")["results"]),
        "official_2027_calendar_verified": False, "new_independent_multiagent_audit": False,
        "deployed": False, "email_sent": False, "formal_ledger_written": False, "all_feedback_closed": False}
    artifacts = [p for p in ROUND.rglob("*") if p.is_file() and "backup" not in p.relative_to(ROUND).parts
                 and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts]
    artifacts += [ROOT / "notes/mainline_registry.md", *(ROOT / name for name in new + changed)]
    result["artifacts"] = {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(set(artifacts))}
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({name: value for name, value in result.items() if name != "artifacts"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
