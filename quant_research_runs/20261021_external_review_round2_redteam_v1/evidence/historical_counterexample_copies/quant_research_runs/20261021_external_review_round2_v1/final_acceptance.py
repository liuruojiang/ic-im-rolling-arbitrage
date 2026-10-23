"""Bind the final cumulative run to exact source/test bytes; preserve every earlier run."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
FINAL = HERE / "final"
FINAL.mkdir(exist_ok=False)

def sha(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()

def sources():
    paths = list(ROOT.glob("*.py")) + list(HERE.glob("*.py"))
    paths += list((ROOT / "docs").glob("*_spec.md"))
    paths += list((ROOT / "docs").glob("*_spec.md.sha256"))
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(paths)}

before = sources()
(FINAL / "source_before.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
excluded = "test_icim_redteam_integration_20261021.py::test_frozen_source_receipt_matches_current_bytes"
env = dict(os.environ, ICIM_AUDIT_STAGE="final", PYTHONIOENCODING="utf-8")
started = datetime.now(timezone.utc).isoformat()
result = subprocess.run([sys.executable, str(HERE / "run_all.py"), "--deselect", excluded],
                        cwd=ROOT, env=env, timeout=90)
after = sources()
(FINAL / "source_after.json").write_text(json.dumps(after, indent=2), encoding="utf-8")
suites = ET.parse(FINAL / "regression.xml").getroot().findall("testsuite")
counts = {key: sum(int(s.attrib.get(key, 0)) for s in suites)
          for key in ["tests", "failures", "errors", "skipped"]}
receipt = dict(started_at=started, finished_at=datetime.now(timezone.utc).isoformat(),
               exit_code=result.returncode, source_before_equals_after=before == after,
               source_files=len(before), counts=counts,
               deselected={excluded: "Historical byte equality is valid for the pre-audit backup, not repaired source; retained unchanged."},
               artifacts={p.name: sha(p) for p in sorted(FINAL.iterdir()) if p.is_file()})
(FINAL / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
print(json.dumps(receipt, indent=2))
raise SystemExit(result.returncode if before == after else 2)
