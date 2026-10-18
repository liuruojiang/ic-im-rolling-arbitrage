"""Independent cloud consumer cases for volatile raw HTML with stable semantic data."""
import copy
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[3]
CLOUD=ROOT/'quant_research_runs/20261016_fear_single_source_delivery_v1/cloud'
sys.path.insert(0,str(CLOUD/'scripts'))
spec=importlib.util.spec_from_file_location('sourcefixture',CLOUD/'tests/test_ic_im_v1_4_fear_sources.py')
sourcefixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(sourcefixture)
import build_ic_im_v1_4_digest as digest
if os.environ.get('AUDIT_DYNAMIC_BASELINE')=='1':
    spec=importlib.util.spec_from_file_location('baseline_digest',Path(__file__).with_name('original_db84_build_digest.py'))
    digest=importlib.util.module_from_spec(spec);spec.loader.exec_module(digest)

def test_dynamic_page_only_raw_hash_may_differ(monkeypatch):
    payload=sourcefixture.payload(monkeypatch,source='page')
    # Two valid archived raw HTML documents can include different nonce/telemetry data.
    sys.path.insert(0,str(ROOT))
    import ic_im_fear_grid_v1 as feed
    body=('<p class="fg-stat-label">最新日期</p><p class="fg-stat-value">2026-10-09</p>'
          '<p class="fg-stat-label">最新指数值</p><p class="fg-stat-value">40.00</p>'
          '<p class="fg-update">数据日 2026-10-09 · 更新 2026-10-09 15:03</p>')
    raw1=(body+'<script nonce="abc">void 0</script>').encode('utf8')
    raw2=(body+'<script nonce="def">void 0</script>').encode('utf8')
    assert feed._parse_page(raw1)==feed._parse_page(raw2)
    assert hashlib.sha256(raw1).hexdigest()!=hashlib.sha256(raw2).hexdigest()
    payload['signals']['IC']['fear_page_sha256']=hashlib.sha256(raw1).hexdigest()
    payload['signals']['IM']['fear_page_sha256']=hashlib.sha256(raw2).hexdigest()
    before=copy.deepcopy(payload)
    digest.validate_success_payload(payload)
    digest.build_success(payload,'','')
    digest.build_success_html(payload,'')
    assert payload==before

def test_different_selected_fear_readings_must_be_rejected(monkeypatch):
    payload=sourcefixture.payload(monkeypatch,source='page')
    # Each side is internally valid, but the two products did not use one Fear observation.
    payload['signals']['IM'].update(fear_greed_index=41.,fear_page_value=41.)
    with pytest.raises(ValueError,match='Fear source snapshots disagree'):
        digest.validate_success_payload(payload)

def test_different_page_publication_clocks_must_be_rejected(monkeypatch):
    payload=sourcefixture.payload(monkeypatch,source='page')
    payload['signals']['IM']['fear_page_updated_at']='2026-10-09T15:04:00+08:00'
    with pytest.raises(ValueError,match='Fear source snapshots disagree'):
        digest.validate_success_payload(payload)

