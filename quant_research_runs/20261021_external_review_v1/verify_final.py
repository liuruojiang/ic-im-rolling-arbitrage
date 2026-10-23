"""Check exactly the authorized source delta and protected baseline bytes."""
from pathlib import Path
from datetime import datetime, timezone
import difflib
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

baseline=json.loads((HERE/"baseline.json").read_text(encoding="utf-8"))
expected={"poe_ic_im_mainline_v1_4_bot.py","ic_im_put_execution_v1.py","ic_im_v1_4_policy.py",
          "poe_ic_im_v1_4_state.py","ic_im_fear_grid_v1.py","test_ic_im_v1_4_integration_guards.py",
          "notes/mainline_registry.md"}
changed=[]
missing=[]
patch=[]
for relative,digest in baseline["backed_up"].items():
    path=ROOT/relative
    if not path.exists():
        missing.append(relative)
    elif sha(path)!=digest:
        changed.append(relative)
        previous=(HERE/"backup"/relative).read_text(encoding="utf-8-sig").splitlines(keepends=True)
        current=path.read_text(encoding="utf-8-sig").splitlines(keepends=True)
        patch.extend(difflib.unified_diff(previous,current,fromfile="before/"+relative,tofile="after/"+relative))
unexpected=sorted(set(changed)-expected)
immutable_changed=[relative for relative,digest in baseline["immutable"].items()
                   if not (ROOT/relative).exists() or sha(ROOT/relative)!=digest]
assert not missing and not unexpected and not immutable_changed, (missing,unexpected,immutable_changed)
compiled=[]
for relative in sorted(expected|{"test_ic_im_external_review_20261021.py","test_ic_im_external_review_display_cache_20261021.py"}):
    if relative.endswith(".py"):
        compile((ROOT/relative).read_text(encoding="utf-8-sig"),relative,"exec")
        compiled.append(relative)
specs=[ROOT/"docs/ic_im_external_review_repairs_20261021_v1_spec.md",
       ROOT/"docs/ic_im_external_review_display_cache_20261021_v1_spec.md"]
for path in specs:
    assert sha(path)==path.with_suffix(path.suffix+".sha256").read_text().split()[0]
cases=list(ET.parse(HERE/"regression.xml").getroot().iter("testcase"))
failed=[case.attrib for case in cases if any(case.find(kind) is not None for kind in ["failure","error","skipped"])]
assert not failed
test_run=json.loads((HERE/"test_run.json").read_text(encoding="utf-8"))
assert test_run["exit_code"]==0
git=["git","-c","safe.directory="+ROOT.as_posix()]
diff=subprocess.run(git+["diff","--check"],cwd=ROOT,capture_output=True,text=True)
assert diff.returncode==0, diff.stdout+diff.stderr
(HERE/"source_delta.patch").write_text("".join(patch),encoding="utf-8")
receipt=dict(verified_at=datetime.now(timezone.utc).isoformat(),test_files=len(test_run["files"]),
             passed=len(cases),failures_errors_skips=0,changed=changed,
             protected_baseline_unchanged=len(baseline["backed_up"])-len(changed),
             immutable_unchanged=len(baseline["immutable"]),runtime_json_unchanged=True,
             preexisting_quarter_roll_and_digest_source_unchanged=True,
             compiled=compiled,diff_check="PASS",new_specs_sha256="PASS",
             source_sha256={name:sha(ROOT/name) for name in compiled},
             formal_runtime_updated=False,production_ledger_updated=False,
             email_sent=False,performance_recomputed=False)
(HERE/"final_verification.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(receipt,ensure_ascii=False,indent=2))
