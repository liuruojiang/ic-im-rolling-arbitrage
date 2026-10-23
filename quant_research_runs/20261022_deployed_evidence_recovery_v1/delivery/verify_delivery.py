"""Verify this recovery audit without changing production or archived inputs."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
RUN = ROOT / "quant_research_runs/20261022_deployed_evidence_recovery_v1"
D = RUN / "delivery"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


base = read(RUN / "baseline.json")
assert all(sha(ROOT / n) == h for n, h in base["source_pin"]["files"].items())
runtime = {p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT / "runtime").rglob("*") if p.is_file()}
assert runtime == base["runtime"], "formal runtime paths or bytes changed"
assert all((ROOT / n).is_file() and sha(ROOT / n) == h for n, h in base["original_evidence"].items())
old = base["previous_acceptance"]
assert sha(ROOT / old["path"]) == old["sha256"]
bound = read(ROOT / "quant_research_runs/20261022_free_source_integration_v1/accepted_02/source_after.json")
assert all((ROOT / n).is_file() and sha(ROOT / n) == h for n, h in bound.items())
trial = read(RUN / "probe_02/receipt.json")
assert trial["status"] == "PASS" and len(trial["cases"]) == 5
assert trial["driver_sha256"] == sha(RUN / "recovery_audit.py")
assert all(c["status"] == "PASS" for c in trial["cases"])
for c in trial["cases"]:
    if c["negative"]:
        assert c["atomic_rejection"] and c["advanced_sessions"] == 0
        assert c["before"] == c["after"]
    else:
        assert c["zero_external_http"] and not c["network_attempts"] and c["idempotent"]
        assert c["health"]["status"] == "ok" and c["after"]["verified_day"] == "2026-10-22"
        assert c["after"]["sequence"] == c["before"]["sequence"] + 1
paths = read(D / "recovery_paths_comparison.json")
assert paths["status"] == "PASS" and not paths["differences"]
assert paths["comparison_source_sha256"] == sha(D / "compare_recovery_paths.py")
for name, expected in [("deployed_directory", 0), ("wrong_directory", 2)]:
    receipt = read(D / (name + "_preflight_receipt.json"))
    value = read(D / (name + "_preflight_stdout.json"))
    assert receipt["exit_code"] == receipt["expected_exit_code"] == expected
    assert not value["network_attempts"] and value["ledger_paths_and_bytes_unchanged"]
spec = ROOT / "docs/ic_im_deployed_evidence_recovery_20261022_v1_spec.md"
assert sha(spec) == Path(str(spec) + ".sha256").read_text(encoding="utf-8").split()[0]
old_registry = (RUN / "backup/notes/mainline_registry.md").read_text(encoding="utf-8")
assert (ROOT / "notes/mainline_registry.md").read_text(encoding="utf-8").endswith(old_registry.split("\n", 1)[1])
launch = read(ROOT / "quant_research_runs/20261022_free_source_integration_v1/delivery/launch_receipt.json")
pid = int(launch["pid"])
process = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
    f"Get-Process -Id {pid} -ErrorAction Stop | Select-Object Id,ProcessName,StartTime,CPU | ConvertTo-Json"],
    capture_output=True, encoding="utf-8")
assert process.returncode == 0, process.stderr
process_info = json.loads(process.stdout)
assert process_info["Id"] == pid and process_info["ProcessName"] == "python"
receipts = sorted((ROOT / "data/icim_v14_original_inputs/capture_receipts").rglob("*.json"))
live = read(receipts[-1])
assert live["pid"] == pid and live["status"] in {"captured", "already_captured"}
assert live["ledger_paths_and_bytes_unchanged"]
assert all(x["status"] == "captured" for x in live["capture"]["products"].values())
last = read(ROOT / "runtime/ic_im_v1_4_r1/latest.json")
result = {"status": "PASS", "verified_at": datetime.now().astimezone().isoformat(),
          "production_source_files_unchanged": len(base["source_pin"]["files"]),
          "formal_runtime_files_unchanged": len(runtime),
          "formal_ledger": {k: last[k] for k in ["verified_day", "sequence", "digest"]},
          "existing_archive_files_unchanged": len(base["original_evidence"]),
          "archive_new_receipts_allowed": True, "previous_2918_test_receipt_unchanged": True,
          "previous_bound_files_unchanged": len(bound), "previous_tests_rerun": False,
          "end_to_end_scenarios_passed": 5, "actual_offline_preflights_passed": 2,
          "recovery_path_fact_comparison": paths["status"],
          "collector": process_info, "collector_latest_receipt": str(receipts[-1]),
          "collector_status": live["status"], "spec_sha256": sha(spec),
          "source_sha256": base["source_pin"]["files"],
          "simulated_scheduler_clock_disclosed": trial["scheduler_clock_is_simulated"],
          "remote_poe_configuration_changed": False, "historical_nav_recalculated": False}
(D / "final_verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k: result[k] for k in ["status", "production_source_files_unchanged",
    "formal_runtime_files_unchanged", "end_to_end_scenarios_passed", "actual_offline_preflights_passed", "collector_status"]}))
