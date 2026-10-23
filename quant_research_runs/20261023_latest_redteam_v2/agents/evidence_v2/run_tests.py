from pathlib import Path
import os,sys,json,hashlib,subprocess
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
sys.path.insert(0,str(ROOT))
state=HERE/'temp';state.mkdir(exist_ok=True)
os.environ.update(ICIM_STATE_DIR=str(state/'state'),ICIM_REQUIRE_MIGRATION='0',ICIM_DISABLE_EVIDENCE_CAPTURE='1',ICIM_DISABLE_INTERNAL_REFRESH='1',PYTHONDONTWRITEBYTECODE='1',TEMP=str(state),TMP=str(state))
orig=os.mkdir
def mkdir(path,mode=0o777,*,dir_fd=None):return orig(path,0o777 if mode==0o700 else mode,**({} if dir_fd is None else {'dir_fd':dir_fd}))
os.mkdir=mkdir
import pytest
status=pytest.main(['-q','-p','no:cacheprovider','-c',str(ROOT/'docs/icim_delivery_pytest.ini'),'--rootdir',str(ROOT),'-o','addopts=','--basetemp',str(state/'pytest'),'--junitxml',str(HERE/'regression.xml'),str(HERE/'test_batch_identity.py')])
(HERE/'run_result.json').write_text(json.dumps({'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'exit_code':int(status),'oracle_sha256':hashlib.sha256((ROOT/'quant_research_runs/20261022_free_source_integration_v1/wire_probe_01/response.bin').read_bytes()).hexdigest(),'source_sha256':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in ['poe_ic_im_mainline_v1_4_bot.py','ic_im_signal_evidence_v1.py','ic_im_evidence_adapter_v1.py']}},indent=2),encoding='utf-8')
raise SystemExit(status)
