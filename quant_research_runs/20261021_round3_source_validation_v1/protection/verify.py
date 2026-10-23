"""Check prior accepted artifacts, frozen files and runtime without mutation."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


cache = {}


def sha(path):
    path = path.resolve()
    if path not in cache:
        if not path.is_file():
            cache[path] = None
        else:
            value = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    value.update(block)
            cache[path] = value.hexdigest()
    return cache[path]


def check(mapping, base=ROOT):
    differences = [{"path": n, "expected": h, "actual": sha(base / n)}
                   for n, h in mapping.items() if sha(base / n) != h]
    return {"checked": len(mapping), "changed_or_missing": differences}


def main():
    baseline = read(HERE / "baseline.json")
    previous = ROOT / "quant_research_runs/20261021_expert_round3_repairs_v1"
    protected = read(previous / "protected_before.json")
    current_protected = baseline["protected"]
    root_runtime = {n: h for n, h in current_protected.items() if n.startswith("runtime/")}
    actual_runtime = {f.relative_to(ROOT).as_posix(): sha(f)
                      for f in (ROOT / "runtime").rglob("*") if f.is_file()}
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "previous_round_and_current_runtime": check(current_protected),
        "previous_frozen_protection_set": check(protected),
        "pre_edit_backups": check(baseline["backup"], HERE / "backup"),
        "previous_source_binding": check(baseline["prior_binding"]),
        "root_runtime": {"before": len(root_runtime), "after": len(actual_runtime),
                         "json_before": sum(n.endswith(".json") for n in root_runtime),
                         "hashes_and_paths_equal": root_runtime == actual_runtime},
    }
    spec = ROOT / "docs/ic_im_round3_source_validation_20261021_v1_spec.md"
    report["preregistered_spec_hash_matches"] = sha(spec) == spec.with_name(spec.name + ".sha256").read_text(encoding="utf-8-sig").split()[0]
    source_changes = report["previous_source_binding"]["changed_or_missing"]
    report["only_authorized_production_source_changed"] = {row["path"] for row in source_changes} == {"poe_ic_im_mainline_v1_4_bot.py"}
    report["previous_bot_matches_pre_edit_backup"] = baseline["prior_binding"]["poe_ic_im_mainline_v1_4_bot.py"] == sha(HERE / "backup/poe_ic_im_mainline_v1_4_bot.py")
    report["passed"] = all([
        not report["previous_round_and_current_runtime"]["changed_or_missing"],
        not report["previous_frozen_protection_set"]["changed_or_missing"],
        not report["pre_edit_backups"]["changed_or_missing"],
        report["root_runtime"]["hashes_and_paths_equal"],
        report["preregistered_spec_hash_matches"],
        report["only_authorized_production_source_changed"],
        report["previous_bot_matches_pre_edit_backup"],
    ])
    target_name = "verification_after_tests.json" if "--after-tests" in sys.argv[1:] else "verification.json"
    (HERE / "protection" / target_name).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "protected": report["previous_round_and_current_runtime"]["checked"],
                      "frozen": report["previous_frozen_protection_set"]["checked"],
                      "runtime": report["root_runtime"],
                      "changes": [row["path"] for row in source_changes]}, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
