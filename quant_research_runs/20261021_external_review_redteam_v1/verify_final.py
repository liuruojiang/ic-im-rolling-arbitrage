"""Read-only final verification against explicit, time-scoped evidence baselines."""
from pathlib import Path
from datetime import datetime, timezone
import difflib
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
def sha(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()
def read(path):
    return json.loads(path.read_text(encoding="utf-8"))
def mismatches(entries, base=ROOT):
    return [p for p, expected in entries.items()
            if not (base / p).is_file() or sha(base / p) != expected]

b = read(HERE / "baseline.json")
old = read(ROOT / "quant_research_runs/20261021_external_review_v1/baseline.json")
old_receipt = read(ROOT / "quant_research_runs/20261021_external_review_v1/final_verification.json")
outputs = read(HERE / "outputs_baseline.json")
changed = mismatches(b["backed_up"])
allowed = {"poe_ic_im_mainline_v1_4_bot.py", "test_ic_im_external_review_20261021.py",
           "test_ic_im_v1_4_integration_guards.py", "notes/mainline_registry.md"}
backups_bad = mismatches(b["backed_up"], HERE / "backup")
prior_bad = mismatches(b["prior_evidence"])
immutable_bad = mismatches(old["immutable"])
historical_receipt_bad = mismatches(old_receipt["source_sha256"], HERE / "backup")
outputs_bad = mismatches({p: v["sha256"] for p, v in outputs["files"].items()})
actual_output_paths = {p.relative_to(ROOT).as_posix() for p in (ROOT / "outputs").rglob("*")
                       if p.is_file() and "__pycache__" not in p.parts}
output_path_delta = sorted(actual_output_paths.symmetric_difference(outputs["files"]))
bound = read(HERE / "final/source_after.json")
bound_bad = mismatches(bound)
receipt = read(HERE / "final/receipt.json")
receipt_bad = mismatches(receipt["artifacts"], HERE / "final")
spec = ROOT / "docs/ic_im_external_review_redteam_20261021_v1_spec.md"
spec_ok = sha(spec) == spec.with_suffix(spec.suffix + ".sha256").read_text().split()[0]
compilation = ["poe_ic_im_mainline_v1_4_bot.py", "test_ic_im_external_review_20261021.py",
               "test_ic_im_v1_4_integration_guards.py"]
compilation += sorted(p.name for p in ROOT.glob("test_icim_redteam_*_20261021.py"))
for relative in compilation:
    compile((ROOT / relative).read_bytes(), relative, "exec")
diff = []
for relative in sorted(changed):
    diff += difflib.unified_diff((HERE / "backup" / relative).read_text(encoding="utf-8").splitlines(True),
                                (ROOT / relative).read_text(encoding="utf-8").splitlines(True),
                                fromfile="before/" + relative, tofile="after/" + relative)
(HERE / "source_delta.patch").write_bytes("".join(diff).encode("utf-8"))
check = subprocess.run(["git", "-c", "safe.directory=" + ROOT.as_posix(), "diff", "--check"],
                       cwd=ROOT, capture_output=True, text=True)
result = dict(verified_at=datetime.now(timezone.utc).isoformat(),
              changed_baseline_files=changed, unexpected_changes=sorted(set(changed) - allowed),
              backup_count=len(b["backed_up"]), backup_mismatches=backups_bad,
              protected_prior_evidence_count=len(b["prior_evidence"]), protected_prior_evidence_mismatches=prior_bad,
              original_selected_immutable_count=len(old["immutable"]), original_selected_immutable_mismatches=immutable_bad,
              historical_source_receipt_matches_pre_audit_backup=not historical_receipt_bad,
              historical_source_receipt_backup_mismatches=historical_receipt_bad,
              prospective_outputs_count=len(outputs["files"]), prospective_outputs_bytes=sum(v["size"] for v in outputs["files"].values()),
              prospective_outputs_mismatches=outputs_bad, prospective_output_path_delta=output_path_delta,
              prospective_scope=outputs["scope"], cumulative_bound_source_mismatches=bound_bad,
              cumulative_receipt_artifact_mismatches=receipt_bad, cumulative=receipt["counts"],
              audit_spec_sha_matches=spec_ok, compiled_files=compilation,
              git_diff_check_exit=check.returncode, git_diff_check_output=check.stdout + check.stderr,
              source_delta_sha256=sha(HERE / "source_delta.patch"),
              reviewed_bot_sha256=sha(ROOT / "poe_ic_im_mainline_v1_4_bot.py"))
result["passed"] = not any([set(changed) - allowed, backups_bad, prior_bad, immutable_bad,
                              historical_receipt_bad, outputs_bad, output_path_delta, bound_bad,
                              receipt_bad, not spec_ok, check.returncode, receipt["exit_code"]])
target = HERE / "final_verification.json"
if target.exists():
    raise SystemExit("Refusing to overwrite final verification")
target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if result["passed"] else 1)
