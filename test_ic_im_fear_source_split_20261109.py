"""Real published split source: keep six-component input, display composite."""
import gzip
from pathlib import Path
from datetime import date, datetime

import pytest
import ic_im_fear_grid_v1 as feed

FIXTURE = Path(__file__).parent / 'tests/fixtures/fear_split_20261109'


def test_real_csv_retains_six_component_input():
    values = feed._parse_csv(gzip.decompress((FIXTURE / 'source.csv.gz').read_bytes()))
    assert values[date(2026, 11, 6)] == 18.97
    assert values[date(2026, 11, 6)] != 10.01


def test_real_page_retains_six_component_input():
    day, value, updated = feed._parse_page(gzip.decompress((FIXTURE / 'page.html.gz').read_bytes()))
    assert day == date(2026, 11, 6) and value == 18.97
    assert updated.date() == date(2026, 11, 7)


@pytest.mark.parametrize('raw', [
    b'date,fear_greed_index,unknown\n2026-11-06,10.01,18.97\n',
    b'date,fear_greed_index,short_term_sentiment\n2026-11-06,10.01,\n',
    b'date,fear_greed_index,short_term_sentiment\n2026-11-06,10.01,nan\n',
    b'date,fear_greed_index,short_term_sentiment\n2026-11-06,10.01,101\n',
    b'date,fear_greed_index,short_term_sentiment\n2026-11-06,10.01,18.97\n2026-11-06,10.01,18.97\n',
])
def test_split_source_never_substitutes_composite_for_missing_legacy(raw):
    with pytest.raises(ValueError): feed._parse_csv(raw)


def real_feed(monkeypatch, mode='close'):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 9, 20 if mode == 'close' else 14, 20, tzinfo=tz)
    monkeypatch.setattr(feed, 'datetime', Clock)
    monkeypatch.setattr(feed, '_validation_clock', lambda: Clock.now(feed.BEIJING))
    monkeypatch.setattr(feed, '_bounded_get', lambda url, limit: gzip.decompress(
        (FIXTURE / ('source.csv.gz' if url == feed.CSV_URL else 'page.html.gz')).read_bytes()))
    return feed._fetch_score_uncached(date(2026, 11, 9), mode)


@pytest.mark.parametrize('mode', ['intraday', 'close'])
def test_saved_real_split_sources_preserve_fallback_and_display_both(monkeypatch, mode):
    evidence = real_feed(monkeypatch, mode)
    assert evidence['fear_greed_index'] == 18.97
    assert evidence['fear_data_date'] == '2026-11-06'
    assert evidence['fear_cross_validation'] == 'matched'
    assert evidence['fear_data_status'] == ('close_previous_session' if mode == 'close' else 'intraday_previous_session')
    assert evidence['fear_composite_reference']['value'] == 10.01
    assert evidence['fear_composite_reference']['role'] == 'reference_only'
    assert evidence['fear_composite_reference']['data_date'] == '2026-11-06'
    assert evidence['fear_composite_reference']['cross_validation'] == 'matched'
    for value in ('18.97', '10.01', 'short_term_sentiment', '仅观察', '原账本原值不回写'):
        assert value in feed.source_disclosure(evidence)


def test_new_composite_cannot_trigger_grid_exit(monkeypatch):
    evidence = real_feed(monkeypatch)
    evidence['fear_composite_reference']['value'] = 99
    assert feed.transition('IC', .5, 'fear_only', 2, evidence['fear_greed_index']) == (.5, 'fear_only', 'hold_grid')


@pytest.mark.parametrize('replacement', ['<p class="fg-stat-label">短期市场情绪</p>', '短期市场情绪'])
def test_new_page_without_legacy_score_cannot_fall_back_to_main(replacement):
    raw = gzip.decompress((FIXTURE / 'page.html.gz').read_bytes()).decode('utf8')
    with pytest.raises(RuntimeError): feed._parse_page(raw.replace(replacement, 'missing').encode())


def test_duplicate_legacy_page_card_is_rejected():
    raw = gzip.decompress((FIXTURE / 'page.html.gz').read_bytes())
    duplicate = '<p class="fg-stat-label">短期市场情绪</p><p class="fg-stat-value">18.97</p>'.encode()
    with pytest.raises(RuntimeError): feed._parse_page(raw + duplicate)
