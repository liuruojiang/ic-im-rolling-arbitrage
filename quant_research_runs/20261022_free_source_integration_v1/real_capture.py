"""Actual close builders, research-only ledger copy, then strictly offline replay."""
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    assert not path.exists(), path
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    run = HERE / sys.argv[1]
    run.mkdir(exist_ok=False)
    formal = {p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT / 'runtime').rglob('*') if p.is_file()}
    copied = run / 'ledger_copy'
    for path in (ROOT / 'runtime/ic_im_v1_4_r1').rglob('*.json'):
        dest = copied / path.relative_to(ROOT / 'runtime/ic_im_v1_4_r1')
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    os.environ.update(ICIM_STATE_DIR=str(copied), ICIM_DISABLE_EVIDENCE_CAPTURE='1')
    os.environ.pop('ICIM_EVIDENCE_DIR', None)
    import poe_ic_im_mainline_v1_4_bot as bot
    import poe_ic_im_v1_4_state as state
    import ic_im_signal_evidence_v1 as evidence
    import ic_im_evidence_adapter_v1 as adapter
    latest = state.StateStore(copied).load_latest()
    bot.install_runtime_anchors(state.anchors_from_record(latest))
    anchors = evidence.encode(bot.LIVE_CONTINUATION_ANCHOR)
    now = datetime.now(bot.BEIJING)
    out = {'started_at': now.isoformat(), 'clock_is_actual': True, 'synthetic_market_inputs': False,
           'formal_runtime_write': False, 'anchor_day': latest['verified_day'],
           'core_contract': latest['products']['IC']['v14_core_put_contract'],
           'driver_sha256': sha(Path(__file__)),
           'stages': {}, 'source_sha256': {name: sha(ROOT / name) for name in
            ['poe_ic_im_mainline_v1_4_bot.py', 'ic_im_evidence_adapter_v1.py', 'ic_im_signal_evidence_v1.py', 'poe_ic_im_v1_4_state.py']}}
    save(run / 'baseline.json', {'runtime': formal, 'sources': out['source_sha256'],
                                 'starting_latest_sha256': sha(copied / 'latest.json')})
    signals = {}
    try:
        for product in ['IC', 'IM']:
            clock = datetime.now(bot.BEIJING)
            metadata = {'product': product, 'input_revision': adapter.INPUT_REVISION, 'original_close': True,
                        'capture_kind': 'real_normal_signal_capture', 'build': bot.BUILD_ID,
                        'anchor_day_at_capture': latest['verified_day']}
            try:
                with evidence.capture_session(run / 'evidence', clock.date(), clock, metadata) as session:
                    retries = []
                    for attempt in range(3):
                        attempt_clock = datetime.now(bot.BEIJING)
                        try:
                            with bot.collection_clock(attempt_clock), bot._network_budget(bot.SIGNAL_PRODUCT_NETWORK_BUDGET_SECONDS):
                                signal = bot.build_live_trade_signal(product, attempt_clock, 'close')
                            break
                        except Exception as exc:
                            retries.append({'attempt': attempt + 1, 'at': attempt_clock.isoformat(),
                                            'error_type': type(exc).__name__, 'error': str(exc)[:600]})
                            print(json.dumps({'retry_product': product, **retries[-1]}, ensure_ascii=False), flush=True)
                            if attempt == 2 or not (isinstance(exc, bot.SignalTransportError)
                                    or isinstance(exc, (bot.requests.Timeout, bot.requests.ConnectionError))
                                    or '预算' in str(exc)):
                                raise
                    signals[product] = signal
                    save(run / f'original_{product.lower()}_typed.json', evidence.encode(signal))
                    save(run / f'original_{product.lower()}.json', state._jsonable(signal))
                    # Preserve the observed listing even when no new selection
                    # was necessary. This is a separate bounded evidence call.
                    if product == 'IC':
                        with bot.runtime_clock(datetime.now(bot.BEIJING)), bot._network_budget(18):
                            bot.fetch_510500_expiries_with_failover()
                    adapter._record_http_conflicts(session)
                    roles = {row['role'] for row in session.entries.values()}
                    session.annotate('replay_completeness', {'input_capture_complete': not any(
                        row.get('resolved') is not True for row in session.failures),
                        'present_roles': sorted(roles), 'normal_signal_generated': True,
                        'bounded_same_session_attempts': retries,
                        'signal_eligible': bool(signal.get('persistence_eligible'))})
                out['stages'][product] = {'manifest': str(session.manifest_path), 'complete': session.context['complete'],
                    'eligible': signal.get('persistence_eligible'), 'diagnostic_status': signal.get('diagnostic_status'),
                    'reason': signal.get('diagnostic_reason'), 'failures': session.failures}
            except Exception as exc:
                out['stages'][product] = {'error_type': type(exc).__name__, 'error': str(exc)[:2400],
                    'traceback': traceback.format_exc()}
            print(json.dumps({'product': product, **out['stages'][product]}, ensure_ascii=False), flush=True)
        if set(signals) != {'IC', 'IM'} or not all(s.get('signal_complete') and s.get('persistence_eligible') for s in signals.values()):
            raise RuntimeError('Both real original signals must pass normal eligibility before isolated ledger append')
        record = state.StateStore(copied).append_confirmed_signals(latest, signals)
        save(run / 'isolated_original_record.json', record)
        out['stages']['isolated_append'] = {'verified_day': record['verified_day'], 'sequence': record['sequence']}
        reader, _ = adapter.load_original(bot, run / 'evidence', 'IC', now.date())
        original_urls = {row['url'] for row in reader.context['raw']}
        requests_made, misses, roles = [], [], []
        original_http, original_call = evidence.EvidenceReader.http_response, evidence.EvidenceReader.call
        def trace_http(self, url, params):
            requests_made.append(url)
            try:
                return original_http(self, url, params)
            except evidence.EvidenceUnavailable as exc:
                misses.append({'role': 'http', 'url': url, 'error': str(exc)})
                raise
        def trace_call(self, role, key):
            roles.append(role)
            try:
                return original_call(self, role, key)
            except evidence.EvidenceUnavailable as exc:
                misses.append({'role': role, 'error': str(exc)})
                raise
        evidence.EvidenceReader.http_response, evidence.EvidenceReader.call = trace_http, trace_call
        bot.install_runtime_anchors(state.anchors_from_record(latest))
        def offline(*a, **k):
            raise RuntimeError('Real replay attempted an external request absent from the original evidence')
        bot.requests.get = offline
        with adapter.evidence_location(run / 'evidence'), bot.historical_replay(now.date()):
            replay = bot.build_live_trade_signal('IC', datetime.now(bot.BEIJING), 'close')
        save(run / 'replayed_ic.json', state._jsonable(replay))
        out['stages']['replay'] = {'eligible': replay.get('persistence_eligible'), 'complete': replay.get('signal_complete'),
            'reason': replay.get('diagnostic_reason'), 'misses': misses, 'requests': requests_made,
            'original_urls': sorted(original_urls), 'address_subset': set(requests_made) <= original_urls,
            'roles_read': roles, 'reported_missing': replay.get('original_evidence', {}).get('unrecorded_inputs')}
        compare_path = ROOT / 'quant_research_runs/20261022_calendar_capture_review_v1/replay_acceptance.py'
        spec = importlib.util.spec_from_file_location('strict_real_comparison', compare_path)
        comparison = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(comparison)
        diffs = comparison.compare_signal_values(record['signals']['IC'], state._jsonable(replay))
        save(run / 'field_differences.json', diffs)
        out['stages']['comparison'] = {'differences': len(diffs), 'paths': [row['path'] for row in diffs]}
        import replay_contract
        facts = replay_contract.compare_original_signal(record['signals']['IC'], state._jsonable(replay), reader)
        save(run / 'facts_comparison.json', facts)
        out['stages']['facts_comparison'] = {k: v for k, v in facts.items() if k != 'rawdiff'}
        out['full_dictionary_equal_except_generated_at'] = not diffs
        out['comparison_contract_sha256'] = sha(ROOT / 'docs/ic_im_free_source_replay_contract_20261022_v1_spec.md')
        out['status'] = 'PASS_FACTS_AND_VERIFIED_REPLAY_METADATA' if facts['pass'] and not misses and set(requests_made) <= original_urls and replay.get('persistence_eligible') else 'NOT_ACCEPTED'
    except Exception as exc:
        out.update(status='NOT_ACCEPTED', error_type=type(exc).__name__, error=str(exc)[:2400], traceback=traceback.format_exc())
    finally:
        out['formal_runtime_unchanged'] = formal == {p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT / 'runtime').rglob('*') if p.is_file()}
        out['anchors_restored'] = anchors == evidence.encode(bot.LIVE_CONTINUATION_ANCHOR)
        out['finished_at'] = datetime.now(bot.BEIJING).isoformat()
        save(run / 'receipt.json', out)
        print(json.dumps({k: v for k, v in out.items() if k != 'stages'}, ensure_ascii=False), flush=True)
        assert out['formal_runtime_unchanged']


if __name__ == '__main__':
    main()
