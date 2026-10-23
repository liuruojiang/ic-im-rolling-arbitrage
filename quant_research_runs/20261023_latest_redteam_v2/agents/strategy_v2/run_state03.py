from pathlib import Path
import os,sys,subprocess,json
root=Path.cwd();out=Path(__file__).resolve().parent
run=out/'run03';run.mkdir(exist_ok=True)
temp=run/'temp';temp.mkdir(exist_ok=True)
env=dict(os.environ,ICIM_TEST_OUTPUT_DIR=str(run),TEMP=str(temp),TMP=str(temp),ICIM_STATE_DIR=str(temp/'state'),ICIM_REQUIRE_MIGRATION='0',PYTHONDONTWRITEBYTECODE='1',ICIM_DISABLE_EVIDENCE_CAPTURE='1',ICIM_DISABLE_INTERNAL_REFRESH='1')
cmd=[sys.executable,'-B','-X','utf8','_icim_delivery_test_worker.py',str(out/'test_state_reachability.py')]
r=subprocess.run(cmd,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',timeout=90)
(run/'process_output.txt').write_text(r.stdout,encoding='utf-8');print(r.stdout[-14000:]);print('exit',r.returncode)
