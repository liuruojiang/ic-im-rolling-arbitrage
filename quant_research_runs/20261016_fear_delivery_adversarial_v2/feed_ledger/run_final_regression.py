"""Run unchanged assertions; work around CPython Windows mkdir(0700) ACL."""
import contextlib
import io
import json
import os
import sys
import uuid
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT))
os.chdir(ROOT)
TEMP=HERE/("test_temp_"+uuid.uuid4().hex[:8])
TEMP.mkdir(mode=0o777)
original_mkdir=os.mkdir
original_chmod=os.chmod
def mkdir(path,mode=0o777,*,dir_fd=None):
 return original_mkdir(path,0o777 if mode==0o700 else mode,**({} if dir_fd is None else {"dir_fd":dir_fd}))
def chmod(path,mode,**kwargs):
 return original_chmod(path,0o777 if mode==0o700 else mode,**kwargs)
os.mkdir=mkdir
os.chmod=chmod
os.environ.update(TEMP=str(TEMP),TMP=str(TEMP),ICIM_STATE_DIR=str(TEMP/"state"),ICIM_REQUIRE_MIGRATION="0")
import tempfile
tempfile.tempdir=str(TEMP)
import pytest
capture=io.StringIO()
tests=["test_ic_im_fear_grid_v1.py","test_ic_im_fear_source_delivery_20261016.py","test_ic_im_fear_delivery_adversarial_20261016.py"]
with contextlib.redirect_stdout(capture),contextlib.redirect_stderr(capture):
 status=pytest.main(["-q","-p","no:cacheprovider","--basetemp",str(TEMP/"pytest"),"--junitxml",str(HERE/"regression_final.xml"),*tests])
(HERE/"regression_final.txt").write_text(capture.getvalue(),encoding="utf-8")
(HERE/"regression_final_run.json").write_text(json.dumps(dict(status=int(status),test_files=tests,test_only_mkdir_mode_adjustment=True,state_dir=str(TEMP/"state")),indent=2),encoding="utf-8")
print(capture.getvalue())
raise SystemExit(int(status))
