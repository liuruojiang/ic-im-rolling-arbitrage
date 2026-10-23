"""Own runner and own output tree; never calls or rewrites old run_checks."""
from pathlib import Path
import contextlib
import hashlib
import io
import json
import os
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
TASK_TEMP = Path(os.environ["TEMP"]) / ("icim_redteam_integration_" + uuid.uuid4().hex)
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
old_root = ROOT / "quant_research_runs/20261021_external_review_v1"
before = {p.relative_to(old_root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in old_root.rglob("*") if p.is_file()}
capture = io.StringIO()
with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
    status = pytest.main(["-q", "-p", "no:cacheprovider", "--basetemp", str(TASK_TEMP / "pytest"),
        "--junitxml", str(HERE / "regression.xml"), "test_icim_redteam_integration_20261021.py"])
after = {p.relative_to(old_root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in old_root.rglob("*") if p.is_file()}
(HERE / "regression.txt").write_text(capture.getvalue(), encoding="utf-8")
(HERE / "run_receipt.json").write_text(json.dumps({"exit_code": int(status),
    "state_dir": str(TASK_TEMP / "state"), "old_evidence_unchanged": before == after,
    "scope": "synthetic adapter failures, real bot/coordinator/runner/state boundary; no live network"}, indent=2), encoding="utf-8")
print(capture.getvalue())
assert before == after, "old evidence was changed"
raise SystemExit(status)
