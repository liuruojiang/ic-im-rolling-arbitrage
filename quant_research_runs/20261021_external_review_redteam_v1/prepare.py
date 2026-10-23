"""Snapshot the reviewed workspace and preserve the original remediation evidence."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import shutil

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

target=HERE/"baseline.json"
if target.exists():
    raise SystemExit("Audit baseline exists; refusing to overwrite")
files=[p for p in ROOT.glob("*.py") if not p.name.startswith("test_icim_redteam_")]
for folder in ["docs","notes","runtime"]:
    files.extend(p for p in (ROOT/folder).rglob("*") if p.is_file() and p.suffix in {".md",".json",".sha256"})
entries={}
for path in files:
    relative=path.relative_to(ROOT)
    backup=HERE/"backup"/relative
    backup.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(path,backup)
    entries[relative.as_posix()]=sha(path)
prior=ROOT/"quant_research_runs/20261021_external_review_v1"
prior_evidence={p.relative_to(ROOT).as_posix():sha(p) for p in prior.rglob("*")
                if p.is_file() and "backup" not in p.relative_to(prior).parts
                and "__pycache__" not in p.parts}
receipt=dict(created_at=datetime.now(timezone.utc).isoformat(),backed_up=entries,prior_evidence=prior_evidence)
target.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding="utf-8")
spec=ROOT/"docs/ic_im_external_review_redteam_20261021_v1_spec.md"
spec.with_suffix(spec.suffix+".sha256").write_text(sha(spec)+"  "+spec.name+"\n",encoding="utf-8")
print(json.dumps(dict(backed_up=len(entries),prior_evidence_protected=len(prior_evidence))))
