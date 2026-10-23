from pathlib import Path
import json
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
manifest=json.loads((ROOT/"docs/ic_im_v1_4_script_delivery_sync_20261013_source_manifest.json").read_text(encoding="utf-8"))
tests=list(dict.fromkeys(manifest["required_local_tests"]+[
    "test_ic_im_put_execution_repairs_20261016.py","test_ic_im_option_maintenance_notice_20261016.py",
    "test_ic_im_put_delivery_completion_20261016.py","test_ic_im_fear_source_delivery_20261016.py",
    "test_ic_im_fear_delivery_adversarial_20261016.py","test_ic_im_month_spreads_20261018.py",
    "test_ic_im_external_review_20261021.py","test_ic_im_external_review_display_cache_20261021.py", "test_ic_im_external_review_round2_20261021.py",
]+[p.name for p in sorted(ROOT.glob("test_icim_redteam_*_20261021.py"))]+[p.name for p in sorted(ROOT.glob("test_icim_round2_audit_*_20261021.py"))]))
result=subprocess.run([sys.executable,str(HERE/"run_checks.py"),*tests,*sys.argv[1:]],cwd=ROOT,timeout=60)
raise SystemExit(result.returncode)
