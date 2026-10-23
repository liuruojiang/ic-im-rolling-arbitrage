"""Read-only independent evidence reconciliation, with output in this directory."""
from pathlib import Path
import hashlib
import difflib
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OLD = ROOT / "quant_research_runs/20261021_external_review_v1"
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
baseline = json.loads((OLD / "baseline.json").read_text(encoding="utf-8"))
receipt = json.loads((OLD / "final_verification.json").read_text(encoding="utf-8"))
backup_mismatches = [p for p, h in baseline["backed_up"].items() if sha(OLD / "backup" / p) != h]
current_changed = [p for p, h in baseline["backed_up"].items() if sha(ROOT / p) != h]
patch = []
for relative in current_changed:
    previous = (OLD / "backup" / relative).read_text(encoding="utf-8-sig").splitlines(keepends=True)
    current = (ROOT / relative).read_text(encoding="utf-8-sig").splitlines(keepends=True)
    patch.extend(difflib.unified_diff(previous, current, fromfile="before/" + relative, tofile="after/" + relative))
immutable_mismatches = [p for p, h in baseline["immutable"].items() if sha(ROOT / p) != h]
covered = set(baseline["immutable"]) | set(baseline["backed_up"])
v2_uncovered = [p.relative_to(ROOT).as_posix()
                for p in (ROOT / "outputs/ic_im_system_mainlines_v2").rglob("*")
                if p.is_file() and p.relative_to(ROOT).as_posix() not in covered]
cases = list(ET.parse(OLD / "regression.xml").getroot().iter("testcase"))
run = json.loads((OLD / "test_run.json").read_text(encoding="utf-8"))
result = {
    "verified_at": datetime.now(timezone.utc).isoformat(),
    "backup_entries": len(baseline["backed_up"]), "backup_mismatches": backup_mismatches,
    "current_changed": current_changed, "changed_equals_receipt": set(current_changed) == set(receipt["changed"]),
    "source_delta_matches_reconstruction": "".join(patch) == (OLD / "source_delta.patch").read_text(encoding="utf-8"),
    "immutable_entries": len(baseline["immutable"]), "immutable_mismatches": immutable_mismatches,
    "receipt_source_mismatches": [p for p, h in receipt["source_sha256"].items() if sha(ROOT / p) != h],
    "old_test_count": len(cases), "old_files": len(run["files"]),
    "old_failed_or_skipped": [case.attrib for case in cases if any(case.find(t) is not None for t in ["failure", "error", "skipped"])],
    "v2_files_outside_baseline_protection": v2_uncovered,
    "limitation": "No claim of actual v2 mutation; these files lack a before-hash in this repair's baseline.",
    "raw_capture_note": "Saved Sina field/adjusted identity and SSE-row binding evidence; CFFEX live archive probe explicitly failed; no real complete 2026-10-21 IC+IM ledger/digest success run claimed."
}
(HERE / "evidence_verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=True, indent=2))
