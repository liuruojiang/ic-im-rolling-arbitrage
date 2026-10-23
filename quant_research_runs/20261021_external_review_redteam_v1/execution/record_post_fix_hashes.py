"""Record current audit-scope source bytes; do not change historical patch checks."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
files = ["ic_im_put_execution_v1.py", "ic_im_v1_4_policy.py",
         "poe_ic_im_v1_4_state.py", "poe_ic_im_mainline_v1_4_bot.py",
         "test_icim_redteam_execution_20261021.py"]
hashes = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}
earlier = json.loads((HERE/"source_delta_comparison.json").read_text(encoding="utf-8"))["files"]
report = dict(captured_at_utc=datetime.now(timezone.utc).isoformat(),
    scope="Source bytes immediately after post_fix 76-case execution; historical patch match is not a pass requirement",
    result="76 passed, 1 existing Pydantic deprecation warning",
    regression_evidence=["post_fix.xml", "post_fix.txt", "post_fix.json"],
    files=hashes,
    changed_since_initial_audit={name: hashes[name] != digest for name, digest in earlier.items()},
    limitations="Hashes captured after execution; no exact before/after source immutability claim during this run")
(HERE/"post_fix_source_hashes.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
