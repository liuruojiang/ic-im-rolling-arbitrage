"""Real 64-symbol MO response and isolated wire/evidence serialization checks."""
from datetime import date, datetime
import json
from pathlib import Path
import re

import pandas as pd
import pytest
import requests

import poe_ic_im_mainline_v1_4_bot as bot
import ic_im_signal_evidence_v1 as evidence

SAVED = Path(__file__).resolve().parent / 'quant_research_runs/20261022_free_source_integration_v1/wire_probe_01'
CLOCK = datetime.fromisoformat('2026-10-22T18:30:00+08:00')
DAY = date(2026, 10, 22)


def response(body):
    result = requests.Response()
    result.status_code, result.encoding = 200, 'gb18030'
    result._content, result._content_consumed = body, True
    return result


def test_real_mo_batch_preserves_logical_evidence_and_exact_variables(monkeypatch, tmp_path):
    receipt = json.loads((SAVED / 'receipt.json').read_text(encoding='utf-8'))
    symbols = receipt['requested']
    body = (SAVED / 'response.bin').read_bytes()
    wire = []
    def get(url, **kwargs):
        wire.append((url, kwargs['params']))
        return response(body)
    monkeypatch.setattr(bot.requests, 'get', get)
    instruments = []
    for symbol in symbols:
        match = re.fullmatch(r'P_OP_mo([0-9]{4})P([0-9]+)', symbol)
        instruments.append('MO' + match[1] + '-P-' + match[2])
    quotes = pd.DataFrame({'instrument': instruments})
    with bot.runtime_clock(CLOCK), evidence.capture_session(tmp_path, DAY, CLOCK, {'purpose':'real MO wire fixture'}) as session:
        result = bot._prefetch_mo_replay_verification(quotes, CLOCK)
    assert result['symbols'] == sorted(symbols)
    assert wire == [('https://hq.sinajs.cn/etag.php', 'list=' + ','.join(sorted(symbols)))]
    assert len(session.raw) == 1
    assert evidence.decode(session.raw[0]['params']) == {'list': ','.join(sorted(symbols))}
    reader = evidence.load_session(tmp_path, DAY, required_roles={'mo.replay_verification'})
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: pytest.fail('replay must not request current data'))
    with bot.runtime_clock(CLOCK), evidence.replay_session(reader):
        assert bot._prefetch_mo_replay_verification(quotes, CLOCK) == result
    assert reader.missing_inputs == []


@pytest.mark.parametrize('url,params,expected', [
    ('https://hq.sinajs.cn/etag.php', {'list':'P_OP_mo2611P6000,P_OP_mo2611P6100'}, 'list=P_OP_mo2611P6000,P_OP_mo2611P6100'),
    ('https://hq.sinajs.cn/', {'list':'CON_OP_10012149,CON_OP_10012150'}, 'list=CON_OP_10012149,CON_OP_10012150'),
    ('https://example.invalid/', {'list':'A,B'}, {'list':'A,B'}),
    ('https://hq.sinajs.cn/etag.php', {'list':'A,B','other':'x'}, {'list':'A,B','other':'x'}),
    ('https://hq.sinajs.cn/etag.php', {'list':'A,B&token=x'}, {'list':'A,B&token=x'}),
    ('https://hq.sinajs.cn/etag.php', {'list':'A'}, {'list':'A'}),
])
def test_only_safe_sina_csv_params_change_wire_encoding(monkeypatch, url, params, expected):
    calls = []
    def get(*args, **kwargs):
        calls.append(kwargs['params'])
        return response(b'')
    monkeypatch.setattr(bot.requests, 'get', get)
    result = bot._evidence_http_get(url, params=params)
    result.close()
    assert calls == [expected]


def test_empty_per_symbol_mo_response_is_not_complete_evidence(monkeypatch):
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: response(b'var hq_str_P_OP_mo2611P6000="";'))
    quotes = pd.DataFrame({'instrument':['MO2611-P-6000']})
    with pytest.raises(RuntimeError, match='不完整'):
        bot._prefetch_mo_replay_verification(quotes, CLOCK)
