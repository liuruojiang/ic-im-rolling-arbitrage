"""Read-only final integrity checks; write only this round's verification result."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path


ROUND = Path(__file__).resolve().parents[1]
ROOT = ROUND.parents[1]
PRIOR = ROOT / "quant_research_runs/20261021_batch3_adversarial_v1"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


_digests = {}


def digest(path):
    path = path.resolve()
    if path not in _digests:
        if not path.is_file():
            _digests[path] = None
        else:
            result = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    result.update(block)
            _digests[path] = result.hexdigest()
    return _digests[path]


def check(mapping, base=ROOT):
    changes = []
    for name, expected in mapping.items():
        actual = digest(base / name)
        if actual != expected:
            changes.append({"path": name, "expected": expected, "actual": actual})
    return {"checked": len(mapping), "changed_or_missing": changes}


def main():
    initial = read(PRIOR / "baseline.json")
    protected = read(ROUND / "protected_before.json")
    backups = read(ROUND / "baseline.json") | read(ROUND / "additional_backups.json")
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "prior_backups": check(initial["backup"], PRIOR / "backup"),
        "prior_evidence": check(initial["protected_prior_evidence"]),
        "prior_frozen_outputs": check(initial["protected_outputs"]),
        "current_frozen_runtime_and_reports": check(protected),
        "new_backups": check(backups, ROUND / "backup"),
    }
    runtime_before = {k: v for k, v in protected.items() if k.startswith("runtime/")}
    runtime_after = {
        f.relative_to(ROOT).as_posix(): digest(f)
        for f in (ROOT / "runtime").rglob("*") if f.is_file()
    }
    report["runtime_files"] = {
        "before": len(runtime_before), "after": len(runtime_after),
        "json_before": sum(k.endswith(".json") for k in runtime_before),
        "json_after": sum(k.endswith(".json") for k in runtime_after),
        "added": sorted(set(runtime_after) - set(runtime_before)),
        "missing": sorted(set(runtime_before) - set(runtime_after)),
        "hashes_equal": runtime_before == runtime_after,
    }
    report["current_outputs"] = check({k: v for k, v in protected.items() if k.startswith("outputs/")})
    spec = importlib.util.spec_from_file_location("round3_final_evidence_gate", ROUND / "evidence_gate.py")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    accepted = ROUND / "accepted_02"
    independent = ROUND / "audit_p1_final/accepted_02"
    final_map = read(accepted / "source_after.json")
    independent_map = read(independent / "source_after.json")
    report["final_tests"] = {
        "counts": gate.validate_receipt(accepted),
        "current_binding": check(final_map),
        "independent_counts": gate.validate_receipt(independent),
        "independent_binding_equals_final": final_map == independent_map,
        "declared_exclusions_match": set(read(ROUND / "exclusions.json")) == set(read(accepted / "collection.json")["deselected"]),
    }
    selected = read(accepted / "collection.json")["selected"]
    by_file = Counter(n.split("::", 1)[0] for n in selected)
    new_files = {k: v for k, v in by_file.items() if k.startswith("test_icim_round3_")}
    report["final_tests"].update({
        "test_files": len(by_file), "new_test_files": new_files,
        "new_tests": sum(new_files.values()), "applicable_prior_tests": len(selected) - sum(new_files.values()),
        "deselected": len(read(accepted / "collection.json")["deselected"]),
    })
    observations = read(ROUND / "protection/observations.json")
    observation = observations["valuation_runtime_cache"]
    old_evidence_changes = report["prior_evidence"]["changed_or_missing"]
    expected_change = [{
        "path": observation["path"], "expected": observation["expected_old_sha256"],
        "actual": observation["observed_sha256"],
    }]
    report["runtime_cache_observation"] = {
        **observation,
        "matches_preserved_observation": old_evidence_changes == expected_change,
        "preserved_observed_copy_sha256": digest(ROUND / "protection/valuation_runtime_cache_observed.json"),
        "frozen_original_snapshot_sha256": digest(ROOT / observation["frozen_copy"]),
    }
    report["test_cache_restore"] = {
        **observations["test_cache"],
        "current_sha256": digest(ROOT / "quant_research_runs/20261021_expert_repairs_v1/.pytest_cache/v/cache/nodeids"),
    }
    spec_path = ROOT / "docs/ic_im_expert_round3_repairs_20261021_v1_spec.md"
    declared_spec = spec_path.with_name(spec_path.name + ".sha256").read_text(encoding="utf-8-sig").split()[0]
    report["preregistered_spec"] = {"declared_sha256": declared_spec, "actual_sha256": digest(spec_path)}
    clean_categories = ["prior_backups", "prior_frozen_outputs", "current_frozen_runtime_and_reports", "new_backups", "current_outputs"]
    checks = [not report[k]["changed_or_missing"] for k in clean_categories]
    checks.extend([
        report["runtime_files"]["hashes_equal"],
        not report["final_tests"]["current_binding"]["changed_or_missing"],
        report["final_tests"]["independent_binding_equals_final"],
        report["final_tests"]["declared_exclusions_match"],
        report["runtime_cache_observation"]["matches_preserved_observation"],
        report["runtime_cache_observation"]["preserved_observed_copy_sha256"] == observation["observed_sha256"],
        report["runtime_cache_observation"]["frozen_original_snapshot_sha256"] == observation["expected_old_sha256"],
        report["test_cache_restore"]["current_sha256"] == observations["test_cache"]["restored_exact_pre_edit_sha256"],
        digest(spec_path) == declared_spec,
    ])
    report["all_prior_paths_unchanged"] = not old_evidence_changes
    report["immutable_files_verified"] = all(checks)
    report["status"] = "verified_with_runtime_cache_observation" if all(checks) else "verification_failed"
    target = ROUND / "protection/verification_02.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "counts": report["final_tests"]["counts"],
        "new_tests": report["final_tests"]["new_tests"],
        "prior_tests": report["final_tests"]["applicable_prior_tests"],
        "test_files": report["final_tests"]["test_files"],
        "source_files": report["final_tests"]["current_binding"]["checked"],
        "protection_counts": {k: report[k]["checked"] for k in clean_categories + ["prior_evidence"]},
        "runtime_files": report["runtime_files"],
        "remaining_changes": old_evidence_changes,
    }, ensure_ascii=False, indent=2))
    if not all(checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
