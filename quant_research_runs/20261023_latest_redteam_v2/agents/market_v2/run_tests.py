from pathlib import Path
import os,sys,json,subprocess,hashlib
here=Path(__file__).resolve().parent;root=here.parents[3]
os.environ.update(ICIM_STATE_DIR=str(here/'isolated_state'),ICIM_REQUIRE_MIGRATION='0',ICIM_DISABLE_EVIDENCE_CAPTURE='1',ICIM_DISABLE_INTERNAL_REFRESH='1',PYTHONDONTWRITEBYTECODE='1')
sys.path.insert(0,str(root));import pytest
status=pytest.main(['-q','-p','no:cacheprovider','-c',str(root/'docs/icim_delivery_pytest.ini'),'--rootdir',str(root),'--junitxml',str(here/'regression.xml'),str(here/'test_market_v2.py')])
(here/'receipt.json').write_text(json.dumps({'exit_code':int(status),'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'test_sha256':hashlib.sha256((here/'test_market_v2.py').read_bytes()).hexdigest()},indent=2),encoding='utf-8')
raise SystemExit(status)
