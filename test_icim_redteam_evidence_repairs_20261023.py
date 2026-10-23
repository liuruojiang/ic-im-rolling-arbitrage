"""EV2-01 regression: real saved quote plus explicitly mutated identifiers.

All archives are temporary. The second quote changes only the saved packet's
identifier to exercise projection; its market values are not an independent
real quote. No production evidence or ledger is written.
"""
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest
import requests

import ic_im_signal_evidence_v1 as evidence
import poe_ic_im_mainline_v1_4_bot as bot

ROOT = Path(__file__).resolve().parent
DAY = date(2026, 10, 22)
CLOCK = datetime(2026, 10, 22, 18, tzinfo=bot.BEIJING)
URL = 'https://hq.sinajs.cn/etag.php'
ONE, TWO, THREE = 'P_OP_mo2611P6000', 'P_OP_mo2611P6100', 'P_OP_mo2611P6200'
ORACLE = ROOT / 'quant_research_runs/20261022_free_source_integration_v1/wire_probe_01/response.bin'


def response(body):
    result = requests.Response()
    result.status_code, result.encoding = 200, 'gb18030'
    result._content, result._content_consumed = body, True
    return result


def packet(symbol=ONE):
    body = ORACLE.read_bytes()
    first = next(value.strip() for value in body.split(b';') if value.strip()) + b';'
    assert b'hq_str_P_OP_mo2611P6000=' in first
    return first.replace(ONE.encode(), symbol.encode())


def price():
    return float(packet().split(b'=')[1].strip(b';').strip(b'"').split(b',')[2])


def opening_price():
    return float(packet().split(b'=')[1].strip(b';').strip(b'"').split(b',')[9])


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, 'request',
                        lambda *a, **k: pytest.fail('network forbidden'))
    monkeypatch.setattr(evidence, '_now', lambda: CLOCK)


BAD_PREFIXES = [b'x', b'not_', b'$', b'0', b'object.', '中'.encode('gb18030')]


@pytest.mark.parametrize('prefix', BAD_PREFIXES)
def test_direct_quote_rejects_nonexact_identifier(monkeypatch, prefix):
    body = packet().replace(b'hq_str_', prefix + b'hq_str_')
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: response(body))
    with bot.runtime_clock(CLOCK), pytest.raises(RuntimeError):
        bot.verify_sina_option_quote('MO2611-P-6000', DAY, price(), CLOCK)


@pytest.mark.parametrize('prefix', BAD_PREFIXES)
def test_direct_opening_quote_rejects_nonexact_identifier(monkeypatch, prefix):
    body = packet().replace(b'hq_str_', prefix + b'hq_str_')
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: response(body))
    with bot.runtime_clock(CLOCK), pytest.raises(RuntimeError):
        bot.verify_sina_option_open('MO2611-P-6000', DAY, opening_price(), CLOCK)


@pytest.mark.parametrize('extra', ['duplicate', 'conflicting', 'other_contract'])
def test_direct_opening_quote_rejects_duplicate_or_extra_assignment(monkeypatch, extra):
    second = packet(TWO) if extra == 'other_contract' else packet()
    if extra == 'conflicting':
        second = second.replace(b'="', b'="different,', 1)
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: response(packet() + second))
    with bot.runtime_clock(CLOCK), pytest.raises(RuntimeError):
        bot.verify_sina_option_open('MO2611-P-6000', DAY, opening_price(), CLOCK)


@pytest.mark.parametrize('prefix', BAD_PREFIXES)
def test_capture_rejects_nonexact_identifier_before_complete_seal(monkeypatch, tmp_path, prefix):
    body = (packet() + packet(TWO)).replace(b'hq_str_', prefix + b'hq_str_')
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: response(body))
    frame = pd.DataFrame({'instrument': ['MO2611-P-6000', 'MO2611-P-6100']})
    with bot.runtime_clock(CLOCK), evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        with pytest.raises(RuntimeError):
            bot._prefetch_mo_replay_verification(frame, CLOCK)
    assert not session.context['complete']
    with pytest.raises(evidence.EvidenceUnavailable):
        evidence.load_session(tmp_path, DAY)


def old_archive(tmp_path, records):
    """Generic recorder models an old complete archive without new prefetch validation."""
    with evidence.capture_session(tmp_path, DAY, CLOCK, {'old_archive_mutation': True}) as session:
        session.call('mo.replay_verification', {'day': DAY}, lambda: {'state_decision_made': False})
        for symbols, body in records:
            session.raw_response(URL, {'list': ','.join(symbols)}, response(body))
    assert session.context['complete']
    return evidence.load_session(tmp_path, DAY)


@pytest.mark.parametrize('prefix', BAD_PREFIXES)
def test_old_integrity_valid_archive_cannot_launder_identifier_on_projection(monkeypatch, tmp_path, prefix):
    body = (packet() + packet(TWO)).replace(b'hq_str_', prefix + b'hq_str_')
    reader = old_archive(tmp_path, [([ONE, TWO], body)])
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: pytest.fail('replay escaped to HTTP'))
    with bot.runtime_clock(CLOCK), evidence.replay_session(reader), pytest.raises(RuntimeError):
        bot.verify_sina_option_quote('MO2611-P-6000', DAY, price(), CLOCK)


@pytest.mark.parametrize('mutation', ['duplicate', 'conflicting_duplicate', 'missing', 'extra',
                                     'valid_plus_malformed', 'empty_payload', 'commented', 'quoted'])
def test_batch_parser_rejects_ambiguous_or_partial_body_in_capture_and_replay(monkeypatch, tmp_path, mutation):
    body = packet() + packet(TWO)
    if mutation == 'duplicate':
        body += packet()
    elif mutation == 'conflicting_duplicate':
        body += packet().replace(b'="', b'="different,', 1)
    elif mutation == 'missing':
        body = packet()
    elif mutation == 'extra':
        body += packet(THREE)
    elif mutation == 'valid_plus_malformed':
        body += packet().replace(b'hq_str_', b'not_hq_str_')
    elif mutation == 'empty_payload':
        body = packet() + f'var hq_str_{TWO}="";'.encode()
    elif mutation == 'commented':
        body = b'/*' + body + b'*/'
    else:
        body = b"'" + body + b"'"
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: response(body))
    frame = pd.DataFrame({'instrument': ['MO2611-P-6000', 'MO2611-P-6100']})
    with bot.runtime_clock(CLOCK), pytest.raises(RuntimeError):
        bot._prefetch_mo_replay_verification(frame, CLOCK)
    reader = old_archive(tmp_path, [([ONE, TWO], body)])
    with pytest.raises(evidence.EvidenceUnavailable):
        reader.http_response(URL, {'list': ONE})


@pytest.mark.parametrize('style', ['original', 'no_var', 'spaces', 'final_semicolon_optional'])
def test_legal_batch_keeps_exact_original_span_and_real_price(monkeypatch, tmp_path, style):
    first, second = packet(), packet(TWO)
    if style == 'no_var':
        first, second = first.removeprefix(b'var '), second.removeprefix(b'var ')
    elif style == 'spaces':
        first, second = first.replace(b'=', b' = '), second.replace(b'=', b' = ')
    elif style == 'final_semicolon_optional':
        second = second.removesuffix(b';')
    body = b'\r\n\t' + first + b'\n' + second + b'\r\n'
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: response(body))
    frame = pd.DataFrame({'instrument': ['MO2611-P-6000', 'MO2611-P-6100']})
    with bot.runtime_clock(CLOCK), evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        bot._prefetch_mo_replay_verification(frame, CLOCK)
    assert session.context['complete']
    reader = evidence.load_session(tmp_path, DAY)
    assert reader.http_response(URL, {'list': ONE})['body'] == first
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: pytest.fail('replay escaped to HTTP'))
    with bot.runtime_clock(CLOCK), evidence.replay_session(reader):
        assert bot.verify_sina_option_quote('MO2611-P-6000', DAY, price(), CLOCK)['lastprice'] == price()
        assert bot.verify_sina_option_open('MO2611-P-6000', DAY, opening_price(), CLOCK)['openprice'] == opening_price()
    assert reader.missing_inputs == []


def test_duplicate_requested_symbol_does_not_hide_bad_original_batch(tmp_path):
    reader = old_archive(tmp_path, [([ONE, TWO, ONE], packet() + packet(TWO))])
    with pytest.raises(evidence.EvidenceUnavailable):
        reader.http_response(URL, {'list': ONE})


@pytest.mark.parametrize('fault', ['conflicting', 'malformed'])
def test_overlapping_original_batches_cannot_hide_conflict_or_bad_identity(tmp_path, fault):
    body = packet() + packet(THREE)
    if fault == 'malformed':
        body = body.replace(b'hq_str_', b'xhq_str_')
    else:
        body = body.replace(b'="', b'="changed,', 1)
    reader = old_archive(tmp_path, [([ONE, TWO], packet() + packet(TWO)), ([ONE, THREE], body)])
    with pytest.raises(evidence.EvidenceUnavailable):
        reader.http_response(URL, {'list': ONE})
