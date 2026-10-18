"""Keep Python 3.14's restrictive Windows temp ACL out of sandbox-only test directories."""
import os
from pathlib import Path
import sys
import tempfile

root=Path(__file__).resolve().parents[3]
here=Path(__file__).resolve().parent
temporary=here/'tmp_adapter'
temporary.mkdir(exist_ok=True)
tempfile.tempdir=str(temporary)
original_mkdir=os.mkdir
def mkdir(path, mode=0o777, *, dir_fd=None):
    resolved=Path(path).resolve()
    if resolved.is_relative_to(here) and mode==0o700:
        mode=0o777
    return original_mkdir(path, mode, dir_fd=dir_fd)
os.mkdir=mkdir
import pytest
cloud=root/'quant_research_runs/20261016_fear_single_source_delivery_v1/cloud/tests'
names=['test_ic_im_v1_4_daily_digest.py','test_ic_im_v1_4_fear_sources.py','test_ic_im_v1_4_workflow.py','test_ic_im_v1_4_readiness.py','test_ic_im_v1_4_readiness_handoff.py']
raise SystemExit(pytest.main([str(cloud/name) for name in names]+['-q','-p','no:cacheprovider','--basetemp='+str(here/'pytest_tmp_adapter'),'--junitxml='+str(here/'targeted_regression_adapter.xml')]))
