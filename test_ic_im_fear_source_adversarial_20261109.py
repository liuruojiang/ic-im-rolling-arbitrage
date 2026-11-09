"""Adversarial expected invariants; only saved source bytes, no network/state."""
import gzip,sys
from pathlib import Path
from datetime import datetime,date
import pytest
import ic_im_fear_grid_v1 as feed
ROOT=Path(__file__).parent/'tests/fixtures/fear_split_20261109'
CSV=gzip.decompress((ROOT/'source.csv.gz').read_bytes())
PAGE=gzip.decompress((ROOT/'page.html.gz').read_bytes())
class Clock(datetime):
 @classmethod
 def now(cls,tz=None):return datetime(2026,11,9,20,0,tzinfo=tz)
def collect(monkeypatch,csv,page):
 def get(url,limit):
  value=csv if url==feed.CSV_URL else page
  if value is None:raise RuntimeError('isolated source unavailable')
  return value
 monkeypatch.setattr(feed,'datetime',Clock)
 monkeypatch.setattr(feed,'_bounded_get',get)
 return feed._fetch_score_uncached(date(2026,11,9),'close')
def test_invalid_optional_composite_does_not_remove_valid_legacy(monkeypatch):
 result=collect(monkeypatch,CSV.replace(b'2026-11-06,10.01,18.97',b'2026-11-06,101,18.97'),PAGE.replace(b'>10.01</p>',b'>101</p>'))
 assert result['fear_greed_index']==18.97,result
 assert result.get('fear_composite_reference',{}).get('value') is None

def test_missing_optional_composite_page_card_preserves_legacy_page(monkeypatch):
 result=collect(monkeypatch,None,PAGE.replace('恐惧贪婪指数</p>'.encode(),'新增卡未发布</p>'.encode()))
 assert result['fear_greed_index']==18.97,result

def test_mixed_schema_cannot_claim_composite_is_original_metric(monkeypatch):
 result=collect(monkeypatch,b'date,fear_greed_index\n2026-11-06,10.01\n',PAGE)
 assert result['fear_greed_index'] in (None,18.97),result

def test_valid_new_observation_survives_invalid_old_input_without_substitution(monkeypatch):
 result=collect(monkeypatch,CSV.replace(b'2026-11-06,10.01,18.97',b'2026-11-06,10.01,nan'),PAGE.replace(b'>18.97</p>',b'>nan</p>'))
 assert result['fear_greed_index'] is None
 assert result['fear_composite_reference']['value']==10.01
 assert result['fear_composite_reference']['cross_validation']=='matched'

def test_optional_failure_does_not_remove_confirmed_legacy_grid(monkeypatch):
 result=collect(monkeypatch,CSV.replace(b'2026-11-06,10.01,18.97',b'2026-11-06,101,18.97'),PAGE.replace(b'>10.01</p>',b'>101</p>'))
 signal={**result,'market_date':'2026-11-09','close_confirmed':True,
         'grid_current':.5,'grid_target':.5,'grid_action':'HOLD','total_units_target':1.0}
 view=feed.publication_view(signal)
 assert view.get('grid_target_confirmed',True) is True and view['grid_target']==.5

def test_old_page_label_after_redefinition_cannot_claim_legacy_identity(monkeypatch):
 page=('<p class="fg-stat-label">最新日期</p><p class="fg-stat-value">2026-11-06</p>'
       '<p class="fg-stat-label">最新指数值</p><p class="fg-stat-value">10.01</p>'
       '<p class="fg-update">数据日 2026-11-06 · 更新 2026-11-07 06:19</p>').encode()
 assert collect(monkeypatch,None,page)['fear_greed_index'] is None


@pytest.mark.parametrize('late_source', ['csv', 'page'])
def test_response_crossing_source_cutover_cannot_use_ambiguous_old_shape(monkeypatch,late_source):
 current=[datetime(2026,11,6,23,59,59,tzinfo=feed.BEIJING)]
 class CrossingClock(datetime):
  @classmethod
  def now(cls,tz=None): return current[0]
 page=('<p class="fg-stat-label">最新日期</p><p class="fg-stat-value">2026-11-06</p>'
       '<p class="fg-stat-label">最新指数值</p><p class="fg-stat-value">10.01</p>'
       '<p class="fg-update">数据日 2026-11-06 · 更新 2026-11-07 00:00</p>').encode()
 def get(url,limit):
  source='csv' if url==feed.CSV_URL else 'page'
  if source!=late_source: raise RuntimeError('isolated unavailable')
  current[0]=datetime(2026,11,7,0,0,1,tzinfo=feed.BEIJING)
  return b'date,fear_greed_index\n2026-11-06,10.01\n' if source=='csv' else page
 monkeypatch.setattr(feed,'datetime',CrossingClock);monkeypatch.setattr(feed,'_bounded_get',get)
 result=feed._fetch_score_uncached(date(2026,11,6),'close')
 assert result['fear_greed_index'] is None,result


def test_each_source_uses_own_response_cutover_clock(monkeypatch):
 current=[datetime(2026,11,6,23,59,58,tzinfo=feed.BEIJING)]
 class CrossingClock(datetime):
  @classmethod
  def now(cls,tz=None): return current[0]
 page=('<p class="fg-stat-label">最新日期</p><p class="fg-stat-value">2026-11-06</p>'
       '<p class="fg-stat-label">最新指数值</p><p class="fg-stat-value">10.01</p>'
       '<p class="fg-update">数据日 2026-11-06 · 更新 2026-11-07 00:00</p>').encode()
 def get(url,limit):
  if url==feed.CSV_URL:
   current[0]=datetime(2026,11,6,23,59,59,tzinfo=feed.BEIJING)
   return b'date,fear_greed_index\n2026-11-06,18.85\n'
  current[0]=datetime(2026,11,7,0,0,1,tzinfo=feed.BEIJING)
  return page
 monkeypatch.setattr(feed,'datetime',CrossingClock);monkeypatch.setattr(feed,'_bounded_get',get)
 result=feed._fetch_score_uncached(date(2026,11,6),'close')
 assert result['fear_greed_index']==18.85
 assert result['fear_available_sources']==['csv']
