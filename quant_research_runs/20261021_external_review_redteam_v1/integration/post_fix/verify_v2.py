"""Distinguish existing manifest consistency from missing historical anchoring."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
V2 = ROOT / "outputs/ic_im_system_mainlines_v2"
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
manifest = json.loads((V2 / "output_manifest.json").read_text(encoding="utf-8"))
entries = {name: {"expected_sha256": row["sha256"], "actual_sha256": sha(V2 / name),
                  "expected_size": row["size"], "actual_size": (V2 / name).stat().st_size,
                  "matches": sha(V2 / name) == row["sha256"] and (V2 / name).stat().st_size == row["size"]}
           for name, row in manifest.items()}
git = ["git", "-c", "safe.directory=" + ROOT.as_posix()]
def command(args):
    result = subprocess.run(git + args, cwd=ROOT, capture_output=True, text=True)
    return {"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
relative = "outputs/ic_im_system_mainlines_v2"
result = {"verified_at": datetime.now(timezone.utc).isoformat(),
    "existing_manifest_sha256": sha(V2 / "output_manifest.json"), "manifest_entries": entries,
    "all_12_existing_manifest_entries_match": all(row["matches"] for row in entries.values()),
    "head_tree": command(["ls-tree", "-r", "HEAD", "--", relative]),
    "all_ref_history": command(["log", "--all", "--format=%H", "--", relative]),
    "tracked_index": command(["ls-files", "--stage", "--", relative]),
    "ignored_manifest": command(["check-ignore", relative + "/output_manifest.json"]),
    "conclusion": "All 12 payload files match the current existing frozen output_manifest. The 13th file is that manifest itself, with no self-entry and no available committed Git bytes/history in this checkout. Therefore this is internal consistency, not independently anchored proof of unchanged bytes since the original freeze or previous repair.",
    "acceptable_supplemental_wording": "The supplemental full-outputs baseline was captured at its recorded audit time. Subsequent comparisons prove preservation from that time only. It does not retrospectively establish protection or unchanged bytes throughout the preceding repair. Current v2 payload files separately match the existing manifest, whose historical root hash is unanchored in the available checkout."}
(HERE / "v2_manifest_verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=True, indent=2))
