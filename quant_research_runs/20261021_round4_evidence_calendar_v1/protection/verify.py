"""Read-only integrity checks against retained pre-edit manifests."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUND = HERE.parent
ROOT = ROUND.parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare(mapping):
    differences = []
    for relative, expected in mapping.items():
        path = ROOT / relative
        if not path.is_file() or sha(path) != expected:
            differences.append(relative)
    return {"checked": len(mapping), "unchanged": not differences, "differences": differences}


def main():
    baseline = json.loads((ROUND / "baseline.json").read_text(encoding="utf-8"))
    prior = json.loads((ROOT / "quant_research_runs/20261021_round3_source_validation_v1/baseline.json").read_text(encoding="utf-8"))
    older = json.loads((ROOT / "quant_research_runs/20261021_expert_round3_repairs_v1/protected_before.json").read_text(encoding="utf-8"))
    result = {"created_at": datetime.now(timezone.utc).isoformat(),
        "current_prior_artifacts_and_runtime": compare(baseline["protected"]),
        "preceding_protected_set": compare(prior["protected"]),
        "older_protected_set": compare(older)}
    backup_bad = [relative for relative, digest in baseline["backup"].items()
                  if sha(ROUND / "backup" / relative) != digest]
    result["backup"] = {"checked": len(baseline["backup"]), "unchanged": not backup_bad, "differences": backup_bad}
    allowed_changes = {"poe_ic_im_mainline_v1_4_bot.py", "ic_im_evidence_adapter_v1.py",
                       "ic_im_signal_evidence_v1.py"}
    changed = [relative for relative, digest in baseline["prior_binding"].items()
               if not (ROOT / relative).is_file() or sha(ROOT / relative) != digest]
    result["source_delta"] = {"changed_prior_bound_files": changed,
                              "unexpected_changes": sorted(set(changed) - allowed_changes)}
    expected_runtime = {relative for relative in baseline["protected"] if relative.startswith("runtime/")}
    runtime = {p.relative_to(ROOT).as_posix() for p in (ROOT / "runtime").rglob("*") if p.is_file()
               and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts}
    result["runtime"] = {"files": len(runtime), "json_files": sum(p.endswith(".json") for p in runtime),
                         "paths_equal": runtime == expected_runtime,
                         "added": sorted(runtime - expected_runtime), "missing": sorted(expected_runtime - runtime)}
    result["passed"] = (all(result[name]["unchanged"] for name in
        ("current_prior_artifacts_and_runtime", "preceding_protected_set", "older_protected_set", "backup"))
        and not result["source_delta"]["unexpected_changes"] and result["runtime"]["paths_equal"])
    target = HERE / "verification_after_tests.json"
    if target.exists():
        raise FileExistsError("do not overwrite protection observations")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
