"""Final-source recheck; preserves every prior integration evidence round."""
from pathlib import Path
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
import os
import sys
import uuid

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
TASK_TEMP = Path(os.environ["TEMP"]) / ("icim_integration_final_" + uuid.uuid4().hex)
TASK_TEMP.mkdir(mode=0o777)
original_mkdir, original_chmod = os.mkdir, os.chmod
def mkdir(path, mode=0o777, *, dir_fd=None):
    return original_mkdir(path, 0o777 if mode == 0o700 else mode,
                          **({} if dir_fd is None else {"dir_fd": dir_fd}))
def chmod(path, mode, **kwargs):
    return original_chmod(path, 0o777 if mode == 0o700 else mode, **kwargs)
os.mkdir, os.chmod = mkdir, chmod
os.environ.update(TEMP=str(TASK_TEMP), TMP=str(TASK_TEMP),
    ICIM_STATE_DIR=str(TASK_TEMP / "state"), ICIM_REQUIRE_MIGRATION="0", PYTHONDONTWRITEBYTECODE="1")
import tempfile
tempfile.tempdir = str(TASK_TEMP)
import pytest
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
protected = [ROOT / "quant_research_runs/20261021_external_review_v1", HERE.parent]
def preserved():
    return {p.relative_to(ROOT).as_posix(): sha(p)
            for root in protected for p in root.rglob("*")
            if p.is_file() and HERE not in p.parents}
source_names = ["poe_ic_im_mainline_v1_4_bot.py", "poe_ic_im_v1_4_state.py",
    "poe_ic_im_v1_4_server.py", "run_ic_im_v1_4_github_digest.py",
    "ic_im_put_execution_v1.py", "ic_im_v1_4_policy.py", "test_icim_redteam_integration_20261021.py"]
outputs_baseline = ROOT / "quant_research_runs/20261021_external_review_redteam_v1/outputs_baseline.json"
outputs_baseline_before = sha(outputs_baseline)
outputs_metadata = json.loads(outputs_baseline.read_text(encoding="utf-8"))
outputs_metadata_summary = {"created_at": outputs_metadata["created_at"], "scope": outputs_metadata["scope"],
    "file_count": len(outputs_metadata["files"]), "total_bytes": sum(row["size"] for row in outputs_metadata["files"].values()),
    "baseline_sha256": outputs_baseline_before, "note": "Metadata reviewed only; no additional all-output byte comparison claimed by this behavior recheck."}
before = preserved()
source_before = {name: sha(ROOT / name) for name in source_names}
capture = io.StringIO()
with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
    status = pytest.main(["-q", "-p", "no:cacheprovider", "--basetemp", str(TASK_TEMP / "pytest"),
        "--junitxml", str(HERE / "regression.xml"), "-k", "not frozen_source_receipt",
        "test_icim_redteam_integration_20261021.py"])
after = preserved()
source_after = {name: sha(ROOT / name) for name in source_names}
(HERE / "regression.txt").write_text(capture.getvalue(), encoding="utf-8")
(HERE / "run_receipt.json").write_text(json.dumps({"verified_at": datetime.now(timezone.utc).isoformat(),
    "exit_code": int(status), "state_dir": str(TASK_TEMP / "state"), "outputs_baseline_metadata": outputs_metadata_summary, "outputs_baseline_unchanged": sha(outputs_baseline) == outputs_baseline_before,
    "old_and_first_round_evidence_unchanged": before == after,
    "source_before_sha256": source_before, "source_after_sha256": source_after, "source_unchanged_during_tests": source_before == source_after,
    "deselection_reason": "Original source-receipt assertion intentionally applies to pre-fix code only; original receipt unchanged. Current tested sources are hashed here.",
    "scope": "real coordinator/runner/StateStore rejection with synthetic opening outage, exact production cutoff, no network, no production state"}, indent=2), encoding="utf-8")
print(capture.getvalue())
assert before == after
assert source_before == source_after
assert sha(outputs_baseline) == outputs_baseline_before
raise SystemExit(status)
