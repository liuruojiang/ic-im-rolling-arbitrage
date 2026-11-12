"""Read-only resumption audit of existing ledger-role evidence; no market/tests."""
from datetime import datetime, timezone
import ast
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
V5 = ROOT / "quant_research_runs/20261112_expert_round5_v5"
OLD = V5 / "audits/ledger/r1"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(names):
    return {name: sha(ROOT / name) if (ROOT / name).is_file() else None for name in names}


pin = load(V5 / "candidate_source_pin_r1.json")
initial = load(V5 / "initial_source_pin.json")
source_before = snapshot(pin["source"])
current_differences = [n for n, h in pin["source"].items() if source_before[n] != h]
assert set(current_differences) <= {"ic_im_evidence_adapter_v1.py", "test_icim_expert_round5_20261112.py"}, current_differences
binding = load(OLD / "run_binding.json")
assert binding["candidate_pin_sha256"] == sha(V5 / "candidate_source_pin_r1.json")
assert binding["product_main_sha256"] == pin["main_sha256"]
report = {
    "created_at": datetime.now(timezone.utc).isoformat(),
    "purpose": "resume and qualify retained ledger-role audit artifacts",
    "new_pytest_executions": 0,
    "new_market_computation": False,
    "formal_ledger_write": False,
    "network_requests": 0,
    "candidate_pin_sha256": sha(V5 / "candidate_source_pin_r1.json"),
    "source_files": len(source_before),
    "current_source_differences_from_r1": current_differences,
    "current_source_difference_reason": "root-authorized P2 provenance repair; old results are qualified for r1 only",
    "main_sha256": pin["main_sha256"],
    "runs": [],
    "artifact_sha256": {},
}
for run in binding["runs"]:
    label = run["attempt"]
    output = OLD / label
    launcher = load(output / "launcher_result.json")
    guard = load(output / "network_guard.json")
    collection = load(output / "collection.json")
    xml = output / "regression.xml"
    assert sha(xml) == run["xml_sha256"]
    assert launcher == run["launcher"]
    assert guard == run["network_guard"]
    assert not guard["external_connection_attempts"] and guard["real_nonlocal_connections"] == 0
    assert launcher["watchdog_seconds"] == 60
    assert launcher["worker_sha256"] == source_before["_icim_delivery_test_worker.py"]
    assert launcher["entry_sha256"] == sha(V5 / "isolated_test_entry.py")
    cases = []
    for case in ET.parse(xml).findall(".//testcase"):
        status = ("failed" if case.find("failure") is not None else
                  "error" if case.find("error") is not None else
                  "skipped" if case.find("skipped") is not None else "passed")
        cases.append({"name": case.attrib["name"], "status": status})
    assert [(c["name"], c["status"]) for c in cases] == [(c["name"], c["status"]) for c in run["cases"]]
    assert sorted(n.rsplit("::", 1)[-1] for n in collection["executed"]) == sorted(c["name"] for c in cases)
    assert all(n in collection["collected"] for n in collection["executed"])
    report["runs"].append({"attempt": label, "exit_code": launcher["exit_code"],
        "tests": len(cases), "passed": sum(c["status"] == "passed" for c in cases),
        "failed": [c["name"] for c in cases if c["status"] != "passed"],
        "selected_nodes": collection["executed"], "xml_sha256": sha(xml)})
    for name in ["collection.json", "launcher_result.json", "network_guard.json", "regression.xml", "test_run.json"]:
        path = output / name
        report["artifact_sha256"][str(path.relative_to(ROOT))] = sha(path)

classification = load(OLD / "fixture_failure_classification.json")
assert classification["classification"] == "audit_fixture_assumption"
assert len(classification["failed_nodes"]) == 2
assert report["runs"][0]["failed"] == classification["failed_nodes"]
assert binding["original_passes_reused"] == 33 and binding["corrected_and_added_passes"] == 5
assert binding["tests_repeated_after_success"] == 0
assert binding["fixture_original_sha256"] == sha(OLD / "test_attempt_1_original.py.txt")
assert binding["fixture_final_sha256"] == sha(OLD / "test_ledger_listing_outcome_routes.py")
report["fixture_failure_disposition"] = classification
report["qualified_prior_passes"] = 38
report["prior_passes_reused"] = 33
report["corrected_and_added_passes"] = 5

index = load(OLD / "retained_capture_index.json")
assert len(index) == binding["retained_archives"] == 40
manifest_count = 0
complete = 0
valid_blobs = set()
missing = []
corrupt = []
for archive in index:
    archive_root = Path(archive["retained_archive"])
    for saved in archive["manifests"]:
        manifest = Path(saved["retained"])
        assert sha(manifest) == saved["file_sha256"]
        envelope = load(manifest)
        body = envelope["body"]
        canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        assert hashlib.sha256(canonical).hexdigest() == saved["body_sha256"] == envelope["sha256"]
        assert body["complete"] is saved["complete"]
        manifest_count += 1
        complete += int(body["complete"])
        refs = list(body["entries"].values()) + body["raw"]
        for ref in refs:
            blob = archive_root / "blobs" / (ref["sha256"] + ref["suffix"])
            if not blob.exists():
                assert blob.name in archive["missing_blobs_preserved"]
                missing.append(str(blob.relative_to(ROOT)))
            elif sha(blob) != ref["sha256"] or blob.stat().st_size != ref["bytes"]:
                corrupt.append(str(blob.relative_to(ROOT)))
            else:
                valid_blobs.add(str(blob.relative_to(ROOT)))
assert len(missing) == 1 and len(set(corrupt)) == 1, (missing, corrupt)
report["retained_capture_integrity"] = {
    "archives": len(index), "manifests": manifest_count,
    "complete_manifest_count": complete, "incomplete_manifest_count": manifest_count - complete,
    "valid_blob_paths": len(valid_blobs),
    "intentional_missing_blob_paths": missing,
    "intentional_corrupt_blob_paths": sorted(set(corrupt)),
    "all_manifest_file_and_body_hashes_match_retained_index": True,
}

observation = load(OLD / "attempt_2/observations/successful_sina_provenance_observation.json")
assert observation["query_cache"]["failures"][0]["source"] == "SSE"
assert observation["archive_envelope"]["failures"] == []
assert observation["query_cache"]["months"] == observation["archive_envelope"]["months"]
report["open_findings_at_r1"] = [{
    "id": "LEDGER-V5-PROVENANCE-1", "severity": "P2",
    "issue": "SSE listing failure followed by valid Sina success loses the earlier failure in the captured available envelope",
    "source": "ic_im_evidence_adapter_v1.py collect_listing_result successful branch",
    "evidence": "../r1/attempt_2/observations/successful_sina_provenance_observation.json",
    "scope": "source provenance fidelity; admitted chain identity/expiry validation remains intact",
    "reported_to_root": True,
}]
report["limits"] = [
    "November market dates, prices and wire responses are synthetic controls based on saved schema fixtures.",
    "The real candidate/policy/capture/reader/readiness functions are exercised, but the outer market builder is replaced and eligibility forced for capture binding; this is not a full market build or StateStore append qualification.",
    "Readiness scaffolding contains explicitly unconsumed placeholder roles; it does not certify those roles as market data.",
    "The wrong-calendar shape acceptance case constructs a local trusted archive; actual providers reject that list and no remote reachability is established.",
    "The SSE 14:50 close-finalization concern remains outside this revision; replacing that fixture with 19:00 versus 18:00 tests the existing nonfuture gate without claiming a new 15:00 finalization policy.",
]
for key in ["protected", "runtime"]:
    values = snapshot(initial[key])
    mismatches = [n for n, h in initial[key].items() if values[n] != h]
    report[key + "_verification"] = {"count": len(values), "mismatches": mismatches}
    assert not mismatches, (key, mismatches)
source_after = snapshot(pin["source"])
assert source_before == source_after
report["source_before_equals_after"] = True
for name in ["run_binding.json", "fixture_failure_classification.json", "retained_capture_index.json",
             "test_attempt_1_original.py.txt", "test_ledger_listing_outcome_routes.py",
             "attempt_2/observations/successful_sina_provenance_observation.json"]:
    path = OLD / name
    report["artifact_sha256"][str(path.relative_to(ROOT))] = sha(path)
report["status"] = "prior_ledger_evidence_verified_with_one_open_provenance_finding"
(HERE / "source_before.json").write_text(json.dumps(source_before, indent=2), encoding="utf-8")
(HERE / "source_after.json").write_text(json.dumps(source_after, indent=2), encoding="utf-8")
with (HERE / "summary.json").open("x", encoding="utf-8") as stream:
    json.dump(report, stream, ensure_ascii=False, indent=2)
print(json.dumps({k: report[k] for k in ["status", "qualified_prior_passes", "source_files", "new_pytest_executions", "retained_capture_integrity"]}, ensure_ascii=False))
