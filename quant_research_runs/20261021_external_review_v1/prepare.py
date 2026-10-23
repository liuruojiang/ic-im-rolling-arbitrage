"""Capture the pre-change workspace without touching production state."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
RUN = Path(__file__).resolve().parent

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    target = RUN / "baseline.json"
    if target.exists():
        raise SystemExit("Baseline already captured; refusing to overwrite")
    files = list(ROOT.glob("*.py"))
    for folder in ("docs", "notes", "runtime"):
        files.extend(p for p in (ROOT / folder).rglob("*")
                     if p.is_file() and p.suffix in {".md", ".json", ".sha256"})
    # Freeze immutable first outputs too; hash only (no expensive duplicate).
    immutable = [p for p in (ROOT / "outputs").rglob("*")
                 if p.is_file() and (p.name.endswith("_spec.md") or p.suffix == ".sha256"
                                    or "ic_im_system_mainlines_v1" in p.parts)]
    entries = {}
    for path in files:
        rel = path.relative_to(ROOT)
        backup = RUN / "backup" / rel
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        entries[rel.as_posix()] = sha(path)
    git = ["git", "-c", "safe.directory=" + ROOT.as_posix()]
    receipt = dict(created_at=datetime.now(timezone.utc).isoformat(),
                   head=subprocess.check_output(git + ["rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                   status=subprocess.check_output(git + ["status", "--short"], cwd=ROOT, text=True),
                   backed_up=entries,
                   immutable={p.relative_to(ROOT).as_posix(): sha(p) for p in immutable})
    target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"backed_up": len(entries), "immutable": len(immutable), "head": receipt["head"]}))

if __name__ == "__main__":
    main()
