"""Verify this run without modifying frozen outputs or runtime payloads."""
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
from evidence_gate import validate_receipt

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
FINAL = HERE / "final_verified"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def mismatches(mapping, base=ROOT):
    return [name for name, digest in mapping.items()
            if not (base/name).is_file() or sha(base/name) != digest]


baseline = read(HERE/"baseline.json")
counts = validate_receipt(FINAL)
bound = read(FINAL/"source_after.json")
changed_bound = mismatches(bound)
bad_backups = mismatches(baseline["backup"], HERE/"backup")
bad_outputs = mismatches(baseline["protected_outputs"])
bad_prior = mismatches(baseline["protected_prior_evidence"])
changed_existing = mismatches(baseline["backup"])
allowed = {"poe_ic_im_mainline_v1_4_bot.py", "notes/mainline_registry.md"}
unexpected = [name for name in changed_existing if name not in allowed]
runtime_old = {name: digest for name, digest in baseline["backup"].items() if name.startswith("runtime/")}
runtime_now = {p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT/"runtime").rglob("*")
               if p.is_file() and p.name != ".ledger.lock"}
runtime_unchanged = runtime_old == runtime_now
diff = subprocess.run(["git", "-c", "safe.directory="+ROOT.as_posix(), "diff", "--check"],
                      cwd=ROOT, capture_output=True, text=True)
patch = []
for name in sorted(changed_existing):
    a, b = (HERE/"backup"/name), ROOT/name
    patch.extend(difflib.unified_diff(a.read_text(encoding="utf-8").splitlines(True),
                                    b.read_text(encoding="utf-8").splitlines(True),
                                    fromfile="before/"+name, tofile="after/"+name))
(HERE/"source_delta.patch").write_bytes("".join(patch).encode("utf-8"))
dependencies = {name: sha(ROOT/name) for name in ["poe_ic_im_mainline_v1_4_bot.py",
                  "ic_im_adjusted_put_migration_v1.py", "ic_im_sse_contract_identity_v1.py"]}
result = dict(checked_at=datetime.now(timezone.utc).isoformat(), counts=counts,
              source_binding_files=len(bound), changed_bound=changed_bound,
              backup_files=len(baseline["backup"]), bad_backups=bad_backups,
              protected_outputs=len(baseline["protected_outputs"]), bad_outputs=bad_outputs,
              protected_prior_evidence=len(baseline["protected_prior_evidence"]), bad_prior=bad_prior,
              changed_existing=changed_existing, unexpected_existing_changes=unexpected,
              runtime_unchanged=runtime_unchanged, git_diff_check_exit=diff.returncode,
              production_dependency_hashes=dependencies, deployed=False,
              a_engine_supported=False, historical_dates_fully_covered=False,
              remaining_deselected=read(FINAL/"receipt.json")["deselected"])
result["passed"] = not any([changed_bound,bad_backups,bad_outputs,bad_prior,unexpected,
                            not runtime_unchanged,diff.returncode != 0])
target = HERE/"final_verification.json"
if target.exists():
    raise RuntimeError("Refuse to overwrite final verification")
target.write_bytes(json.dumps(result,ensure_ascii=False,indent=2).encode("utf-8"))
print(json.dumps(result,ensure_ascii=False,indent=2))
if not result["passed"]:
    raise SystemExit(1)
