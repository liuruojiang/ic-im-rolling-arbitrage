"""Compare original signal facts and separately validate replay context metadata.

This is an acceptance helper, never a signal producer. Full raw differences are
retained. Only three explicitly named calculation clocks, the exact expected
generation-context transition, and two verified replay envelope fields are
separated from facts. Market/quote/execution clocks and all notes/IV fields stay
in the exact comparison.
"""
from copy import deepcopy
from datetime import date, datetime, time
import json
import re
from zoneinfo import ZoneInfo

from ic_im_signal_evidence_v1 import EvidenceUnavailable, decode

BEIJING = ZoneInfo('Asia/Shanghai')
CALCULATION_CLOCKS = ('fetch_time', 'signal_calculation_started_at', 'signal_generated_at')
REPLAY_ADDITIONS = ('original_evidence', 'historical_evidence_supplements')
ENVELOPE_FIELDS = {'manifest_sha256', 'decision_at', 'acquired_until', 'replay_runtime',
                   'input_revision', 'unrecorded_inputs', 'replay_completeness'}


def _differences(a, b, path='$'):
    rows = []
    if type(a) is not type(b):
        return [{'path': path, 'reason': 'type', 'original': a, 'replayed': b}]
    if isinstance(a, dict):
        for key in sorted(a.keys() | b.keys()):
            if key not in a or key not in b:
                rows.append({'path': path + '.' + key, 'reason': 'missing',
                             'original_present': key in a, 'replayed_present': key in b})
            else:
                rows.extend(_differences(a[key], b[key], path + '.' + key))
    elif isinstance(a, list):
        if len(a) != len(b):
            rows.append({'path': path, 'reason': 'length', 'original': len(a), 'replayed': len(b)})
        else:
            for index, (x, y) in enumerate(zip(a, b)):
                rows.extend(_differences(x, y, f'{path}[{index}]'))
    elif a != b:
        rows.append({'path': path, 'reason': 'value', 'original': a, 'replayed': b})
    return rows


def _json_value(value):
    def serialize(obj):
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        raise TypeError('Unsupported non-JSON replay annotation value')
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False, default=serialize))


def _calculation_clock(value, field):
    if not isinstance(value, str):
        raise ValueError('calculation timestamp must be a string')
    if field == 'fetch_time':
        if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}', value):
            raise ValueError('fetch_time must retain its documented Beijing format')
        return datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(tzinfo=BEIJING)
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'
                        r'(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})', value):
        raise ValueError('calculation clock must be an aware ISO timestamp')
    return datetime.fromisoformat(value).astimezone(BEIJING)


def compare_original_signal(original_json, replayed_json, reader):
    """Return facts_differences, audit_errors, full rawdiff and an explicit pass.

    ``reader`` must be the independently verified original EvidenceReader.
    Network miss counting and URL-subset checks remain independent gates in
    the calling acceptance driver; this function never claims to perform them.
    """
    raw = _differences(original_json, replayed_json)
    errors = []
    if not isinstance(original_json, dict) or not isinstance(replayed_json, dict):
        return {'pass': False, 'facts_differences': raw,
                'audit_errors': ['Both signals must be JSON objects'], 'rawdiff': raw}
    original, replayed = deepcopy(original_json), deepcopy(replayed_json)
    original_clocks, replay_clocks = {}, {}
    for field in CALCULATION_CLOCKS:
        for label, signal, clocks in [('original', original, original_clocks),
                                      ('replayed', replayed, replay_clocks)]:
            try:
                clocks[field] = _calculation_clock(signal[field], field)
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(f'{label}.{field}: {exc}')
            signal.pop(field, None)
    try:
        day = reader.day
        decision = reader.decision_at.astimezone(BEIJING)
        closed = reader.closed_at.astimezone(BEIJING)
        floor_decision, floor_closed = decision.replace(microsecond=0), closed.replace(microsecond=0)
        if (not isinstance(day, date) or decision.date() != day or closed.date() != day
                or closed < decision or decision.time() < time(15)):
            errors.append('reader original acquisition is not an ordered same-day close')
        if reader.context.get('complete') is not True:
            errors.append('reader manifest is not complete')
        if any(row.get('resolved') is not True for row in reader.context.get('failures', [])):
            errors.append('reader manifest contains unresolved failures')
        if type(reader.missing_inputs) is not list or reader.missing_inputs:
            errors.append('reader has missing original inputs')
        if original.get('market_date') != str(day) or replayed.get('market_date') != str(day):
            errors.append('both market dates must exactly match the original reader day')
        if original.get('close_confirmed') is not True or replayed.get('close_confirmed') is not True:
            errors.append('only two confirmed close signals satisfy this contract')
        if (reader.metadata.get('original_close') is not True
                or reader.metadata.get('product') != original.get('product')):
            errors.append('reader metadata does not identify the original signal product/close')
        if len(original_clocks) == len(CALCULATION_CLOCKS):
            ordered = [original_clocks[key] for key in CALCULATION_CLOCKS]
            if not (floor_decision <= ordered[0] <= ordered[1] <= ordered[2] <= floor_closed):
                errors.append('original calculation clocks fall outside their ordered acquisition interval')
        if len(replay_clocks) == len(CALCULATION_CLOCKS):
            if replay_clocks['fetch_time'] != floor_closed:
                errors.append('replay fetch_time must be the original verified acquisition finish')
            if not (floor_closed <= replay_clocks['signal_calculation_started_at'] <= replay_clocks['signal_generated_at']):
                errors.append('replay calculation clocks precede verified evidence or are reversed')
        envelope = replayed.get('original_evidence')
        if not isinstance(envelope, dict) or set(envelope) != ENVELOPE_FIELDS:
            errors.append('replay original_evidence must have the exact declared audit fields')
        else:
            expected = {'manifest_sha256': reader.manifest_sha256,
                        'decision_at': reader.decision_at.isoformat(),
                        'acquired_until': reader.closed_at.isoformat(),
                        'replay_runtime': closed.isoformat(),
                        'input_revision': reader.metadata.get('input_revision'),
                        'unrecorded_inputs': [],
                        'replay_completeness': _json_value(decode(reader.context.get('annotations', {}).get('replay_completeness')))}
            if not isinstance(expected['input_revision'], str) or not expected['input_revision']:
                errors.append('original reader input revision is missing')
            if not isinstance(expected['manifest_sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', expected['manifest_sha256']):
                errors.append('original reader manifest hash is invalid')
            if type(envelope.get('unrecorded_inputs')) is not list or envelope['unrecorded_inputs']:
                errors.append('replay must report an explicitly empty list of unrecorded inputs')
            annotation = expected['replay_completeness']
            if not isinstance(annotation, dict) or annotation.get('input_capture_complete') is not True:
                errors.append('original capture completeness annotation is not explicitly true')
            for difference in _differences(expected, envelope, '$.original_evidence'):
                errors.append('replay metadata binding: ' + difference['path'] + ': ' + difference['reason'])
        if type(replayed.get('historical_evidence_supplements')) is not list or replayed['historical_evidence_supplements']:
            errors.append('exact original-signal recovery must have an explicitly empty supplements list')
    except (AttributeError, KeyError, TypeError, ValueError, EvidenceUnavailable) as exc:
        errors.append('invalid original reader metadata: ' + str(exc))
    if original.get('signal_generation_timing') != 'close_confirmed' or replayed.get('signal_generation_timing') != 'retrospective_replay':
        errors.append('unexpected generation-context transition')
    original.pop('signal_generation_timing', None)
    replayed.pop('signal_generation_timing', None)
    for field in REPLAY_ADDITIONS:
        if field in original:
            errors.append('original signal unexpectedly already contains replay audit field: ' + field)
        replayed.pop(field, None)
    facts = _differences(original, replayed)
    return {'pass': not errors and not facts, 'facts_differences': facts,
            'audit_errors': errors, 'rawdiff': raw,
            'calculation_clock_fields': list(CALCULATION_CLOCKS),
            'generation_context_transition': {'original': original_json.get('signal_generation_timing'),
                                              'replayed': replayed_json.get('signal_generation_timing')},
            'independent_gates_required': ['original isolated ledger append', 'zero EvidenceUnavailable',
                                           'replay HTTP URL subset of original URLs']}
