"""Execute only newly affected surface contracts against an explicit source pin."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
OUT = HERE / sys.argv[1]
OUT.mkdir(exist_ok=False)
PIN_PATH = (ROOT / sys.argv[2]).resolve()
PIN = json.loads(PIN_PATH.read_text(encoding="utf-8"))
TEMP = Path(tempfile.gettempdir()) / ("surface_affected_" + uuid.uuid4().hex[:8])
TEMP.mkdir(exist_ok=False)
ENTRY = ROOT / "quant_research_runs/20261112_expert_round5_v5/isolated_test_entry.py"
TESTS = [str(HERE / name) for name in (sys.argv[3:] or ["test_available_outcome_preservation.py"])]

def hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(PIN["source"])}

before = hashes()
assert before == PIN["source"]
(OUT / "source_before.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
env = {**os.environ, "TEMP": str(TEMP), "TMP": str(TEMP), "ICIM_SURFACE_AUDIT_PIN": str(PIN_PATH),
       "ICIM_TEST_OUTPUT_DIR": str(OUT), "PYTHONDONTWRITEBYTECODE": "1",
       "ICIM_DISABLE_INTERNAL_REFRESH": "1", "PYTHONIOENCODING": "utf-8",
       "MPLCONFIGDIR": str(TEMP / "mpl"), "ICIM_EVIDENCE_DIR": ""}
started = datetime.now(timezone.utc)
with (OUT / "process_output.txt").open("x", encoding="utf-8") as stream:
    child = subprocess.Popen([sys.executable, "-B", "-X", "utf8", str(ENTRY), *TESTS],
                             cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
    try:
        status = child.wait(timeout=60)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], capture_output=True)
        child.wait()
        status = 124
after = hashes()
(OUT / "source_after.json").write_text(json.dumps(after, indent=2), encoding="utf-8")
result = {"exit_code": status, "started_at": started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
          "source_before_equals_after": before == after, "source_files": len(before), "watchdog_seconds": 60,
          "source_pin_path": str(PIN_PATH), "source_pin_sha256": hashlib.sha256(PIN_PATH.read_bytes()).hexdigest(),
          "test_sha256": {str(Path(name.split("::", 1)[0]).relative_to(ROOT)):
                          hashlib.sha256(Path(name.split("::", 1)[0]).read_bytes()).hexdigest() for name in TESTS}}
(OUT / "launcher_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
print((OUT / "process_output.txt").read_text(encoding="utf-8")[-12000:])
raise SystemExit(status)
