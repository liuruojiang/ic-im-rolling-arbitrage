"""Create the final isolated runner without changing earlier evidence."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
original = (HERE.parent / "run_execution.py").read_text(encoding="utf-8-sig")
runner = original.replace("ROOT = Path(__file__).resolve().parents[3]",
                          "ROOT = Path(__file__).resolve().parents[4]")
runner = runner.replace("'first_run.xml'", "'regression.xml'").replace(
    "'first_run.txt'", "'regression.txt'").replace("'first_run.json'", "'test_run.json'")
prefix = '''import hashlib
from datetime import datetime, timezone
AUDITED = ["ic_im_put_execution_v1.py", "ic_im_v1_4_policy.py",
           "poe_ic_im_v1_4_state.py", "poe_ic_im_mainline_v1_4_bot.py",
           "test_icim_redteam_execution_20261021.py",
           "test_ic_im_external_review_20261021.py",
           "test_ic_im_ordinary_put_route_fix_20261013.py",
           "test_ic_im_put_execution_repairs_20261016.py"]
def source_hashes():
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in AUDITED}
before_hashes = source_hashes()
before_at = datetime.now(timezone.utc).isoformat()
(HERE/"source_hashes_before.json").write_text(json.dumps(dict(captured_at_utc=before_at, files=before_hashes), indent=2), encoding="utf-8")
'''
runner = runner.replace("capture=io.StringIO()", prefix + "\ncapture=io.StringIO()")
suffix = '''after_hashes = source_hashes()
stable = before_hashes == after_hashes
(HERE/"source_hashes_after.json").write_text(json.dumps(dict(captured_at_utc=datetime.now(timezone.utc).isoformat(), files=after_hashes, unchanged_during_run=stable), indent=2), encoding="utf-8")
(HERE/"verification.json").write_text(json.dumps(dict(exit_code=int(status), unchanged_during_run=stable, before=before_hashes, after=after_hashes), indent=2), encoding="utf-8")
if not stable:
    print("SOURCE CHANGED DURING RUN")
    status = 99
'''
runner = runner.replace("print(safe[-17000:])", suffix + "\nprint(safe[-17000:])")
(HERE/"run_execution_final.py").write_text(runner, encoding="utf-8")
print("Final runner prepared; prior evidence untouched")
