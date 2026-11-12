"""Run previously written pending audit tests without changing prior artifacts."""
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
OUT = HERE / (sys.argv[1] if len(sys.argv) > 1 else "pending_r1")
OUT.mkdir(exist_ok=False)
TEMP = Path(tempfile.gettempdir()) / ("surface_takeover_" + uuid.uuid4().hex[:8])
TEMP.mkdir(exist_ok=False)
ENTRY = ROOT / "quant_research_runs/20261112_expert_round5_v5/isolated_test_entry.py"
PIN = json.loads((HERE.parents[2] / "candidate_source_pin_r1.json").read_text(encoding="utf-8"))["source"]
FILES = [str(HERE / name) for name in (sys.argv[2:] or ["test_surface_v5_r1_followup.py", "test_capture_fallback_provenance_red.py"])]

def hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(PIN)}

before = hashes()
assert before == PIN
(OUT / "source_before.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
env = {**os.environ, "TEMP": str(TEMP), "TMP": str(TEMP),
       "ICIM_TEST_OUTPUT_DIR": str(OUT), "PYTHONDONTWRITEBYTECODE": "1",
       "ICIM_DISABLE_INTERNAL_REFRESH": "1", "PYTHONIOENCODING": "utf-8",
       "MPLCONFIGDIR": str(TEMP / "mpl"), "ICIM_EVIDENCE_DIR": ""}
start = datetime.now(timezone.utc)
with (OUT / "process_output.txt").open("x", encoding="utf-8") as stream:
    child = subprocess.Popen([sys.executable, "-B", "-X", "utf8", str(ENTRY), *FILES], cwd=ROOT,
                             env=env, stdout=stream, stderr=subprocess.STDOUT)
    try:
        status = child.wait(timeout=60)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], capture_output=True)
        child.wait()
        status = 124
after = hashes()
(OUT / "source_after.json").write_text(json.dumps(after, indent=2), encoding="utf-8")
result = {"exit_code": status, "started_at": start.isoformat(),
          "finished_at": datetime.now(timezone.utc).isoformat(), "watchdog_seconds": 60,
          "source_unchanged": before == after, "source_files": len(before), "files": FILES,
          "test_copies": {Path(name).name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in FILES}}
(OUT / "launcher_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
print((OUT / "process_output.txt").read_text(encoding="utf-8")[-12000:])
raise SystemExit(status)
