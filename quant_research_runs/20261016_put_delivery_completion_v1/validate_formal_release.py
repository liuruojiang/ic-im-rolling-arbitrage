from pathlib import Path
import json, subprocess, sys, time
r=Path(__file__).resolve().parent
start=time.monotonic()
try:
 result=subprocess.run([sys.executable,'-B','-X','utf8',str(r/'run_formal_checks.py')],timeout=60)
 code=result.returncode
except subprocess.TimeoutExpired:
 code=124
elapsed=time.monotonic()-start
(r/'formal_verification/hard_deadline.json').write_text(json.dumps(dict(exit_code=code,seconds=elapsed,hard_timeout_seconds=60),indent=2))
sys.exit(code)
