"""Check retained receipts, frozen inputs and formal runtime without writing them."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUND = HERE.parent
ROOT = ROUND.parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def compare(mapping):
    differences = []
    for name, digest in mapping.items():
        path = ROOT / name
        if not path.is_file() or sha(path) != digest:
            differences.append(name)
    return {"checked": len(mapping), "unchanged": not differences, "differences": differences}


def main():
    destination = HERE / "verification_after_tests.json"
    if destination.exists():
        raise FileExistsError("preserve first integrity observation")
    baseline = read(ROUND / "baseline.json")
    groups = {
        "current_prior_artifacts_and_runtime": baseline["protected"],
        "preceding_protected_set": read(ROOT / "quant_research_runs/20261021_round4_evidence_calendar_v1/baseline.json")["protected"],
        "earlier_protected_set": read(ROOT / "quant_research_runs/20261021_round3_source_validation_v1/baseline.json")["protected"],
        "oldest_protected_set": read(ROOT / "quant_research_runs/20261021_expert_round3_repairs_v1/protected_before.json"),
    }
    result = {"created_at": datetime.now(timezone.utc).isoformat(),
              **{name: compare(mapping) for name, mapping in groups.items()}}
    backup_bad = [name for name, digest in baseline["backup"].items() if sha(ROUND / "backup" / name) != digest]
    result["backup"] = {"checked": len(baseline["backup"]), "unchanged": not backup_bad, "differences": backup_bad}
    changed = [name for name, digest in baseline["prior_binding"].items()
               if not (ROOT / name).is_file() or sha(ROOT / name) != digest]
    result["source_delta"] = {"changed_prior_bound_files": changed,
                              "unexpected_changes": sorted(set(changed) - {"ic_im_signal_evidence_v1.py"})}
    expected = {name for name in baseline["protected"] if name.startswith("runtime/")}
    actual = {path.relative_to(ROOT).as_posix() for path in (ROOT / "runtime").rglob("*") if path.is_file()
              and "__pycache__" not in path.parts and ".pytest_cache" not in path.parts}
    result["runtime"] = {"files": len(actual), "json_files": sum(name.endswith(".json") for name in actual),
                         "paths_equal": expected == actual, "added": sorted(actual - expected), "missing": sorted(expected - actual)}
    result["quarter_roll_copy_matches_source"] = (
        sha(ROUND / "dependencies/ic_im_quarter_roll_v1_3.py")
        == sha(ROOT / "ic_im_quarter_roll_v1_3.py") == baseline["quarter_roll_source_copy_sha256"])
    result["passed"] = (all(result[name]["unchanged"] for name in [*groups, "backup"])
        and not result["source_delta"]["unexpected_changes"] and result["runtime"]["paths_equal"]
        and result["quarter_roll_copy_matches_source"])
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
