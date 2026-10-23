from pathlib import Path
import os,sys,json,hashlib
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
sys.path.insert(0,str(ROOT))
original_mkdir=os.mkdir
original_chmod=os.chmod
def mkdir(path,mode=0o777,*,dir_fd=None):
    return original_mkdir(path,0o777 if mode==0o700 else mode,**({} if dir_fd is None else {'dir_fd':dir_fd}))
def chmod(path,mode,**kwargs):return original_chmod(path,0o777 if mode==0o700 else mode,**kwargs)
os.mkdir=mkdir;os.chmod=chmod
scratch=HERE/'temp';scratch.mkdir(exist_ok=True)
os.environ.update(ICIM_STATE_DIR=str(scratch/'state'),ICIM_REQUIRE_MIGRATION='0',ICIM_DISABLE_INTERNAL_REFRESH='1',PYTHONDONTWRITEBYTECODE='1',TEMP=str(scratch),TMP=str(scratch))
os.environ.pop('PYTEST_ADDOPTS',None)
import tempfile;tempfile.tempdir=str(scratch)
import pytest
status=pytest.main(['-q','-p','no:cacheprovider','-c',str(ROOT/'docs/icim_delivery_pytest.ini'),'--rootdir',str(ROOT),'--basetemp',str(scratch/'pytest'),'--junitxml',str(HERE/'results.xml'),str(HERE/'test_state_v2.py')])
(HERE/'process_exit.json').write_text(json.dumps({'pytest_exit_code':int(status)}),encoding='utf-8')
raise SystemExit(status)
