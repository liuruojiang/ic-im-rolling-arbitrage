"""Actual-session experiment using only a disposable copy of the formal ledger."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import traceback
from datetime import datetime

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    assert not path.exists(), path
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    run = HERE / 'real_session_01'
    run.mkdir(exist_ok=False)
    spec = ROOT / 'docs/ic_im_real_original_evidence_20261022_v1_spec.md'
    spec.with_suffix('.md.sha256').write_text(sha(spec) + '  ' + spec.name + '\n', encoding='utf-8')
    runtime_before = {p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT/'runtime').rglob('*') if p.is_file()}
    copied = run / 'ledger_copy'
    source = ROOT / 'runtime/ic_im_v1_4_r1'
    for path in source.rglob('*.json'):
        dest = copied / path.relative_to(source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    os.environ['ICIM_STATE_DIR'] = str(copied)
    os.environ.pop('ICIM_EVIDENCE_DIR', None)
    os.environ['ICIM_DISABLE_EVIDENCE_CAPTURE'] = '1'
    import poe_ic_im_mainline_v1_4_bot as bot
    import poe_ic_im_v1_4_state as state
    import ic_im_signal_evidence_v1 as evidence
    import ic_im_evidence_adapter_v1 as adapter
    latest = json.loads((copied/'latest.json').read_text(encoding='utf-8-sig'))
    state._validate_record(latest)
    bot.install_runtime_anchors(state.anchors_from_record(latest))
    anchors_before = evidence.encode(bot.LIVE_CONTINUATION_ANCHOR)
    now = datetime.now(bot.BEIJING)
    out = {'started_at': now.isoformat(), 'clock_is_actual': True, 'synthetic_market_inputs': False,
           'original_anchor_day': latest['verified_day'], 'independent_core': latest['products']['IC']['v14_core_put_contract'],
           'original_core_qty': latest['products']['IC']['v14_core_put_qty'],
           'production_runtime_write': False, 'stages': {}, 'original_signal_errors': {},
           'source_sha256': {n: sha(ROOT/n) for n in ['poe_ic_im_mainline_v1_4_bot.py','ic_im_evidence_adapter_v1.py',
               'ic_im_signal_evidence_v1.py','poe_ic_im_v1_4_state.py']}}
    write(run/'baseline.json', {'runtime':runtime_before,'spec_sha256':sha(spec),
                               'starting_latest_sha256':sha(copied/'latest.json'),'sources':out['source_sha256']})
    originals = {}
    original_annotate = evidence.CaptureSession.annotate
    def measure(self, name, value):
        metadata = evidence.decode(self.metadata)
        product = metadata.get('product')
        if name == 'replay_completeness' and metadata.get('capture_kind') == 'independent_daily_inputs':
            print('ORIGINAL_BUILD ' + str(product), flush=True)
            try:
                with bot.historical_replay(None):
                    signal = bot.build_live_trade_signal(product, datetime.now(bot.BEIJING), 'close')
                originals[product] = signal
                write(run / f'original_{product.lower()}_typed.json', evidence.encode(signal))
                write(run / f'original_{product.lower()}.json', state._jsonable(signal))
                print(json.dumps({'product':product,'original_complete':signal.get('signal_complete'),
                    'eligible':signal.get('persistence_eligible'),'diagnostic_status':signal.get('diagnostic_status'),
                    'reason':signal.get('diagnostic_reason')},ensure_ascii=False),flush=True)
            except Exception as exc:
                out['original_signal_errors'][product] = {'type':type(exc).__name__,'message':str(exc)[:1200]}
                print('ORIGINAL_ERROR ' + str(product) + ' ' + type(exc).__name__,flush=True)
            adapter._record_http_conflicts(self)
            value = {**value, 'measured_original_signal': product in originals,
                     'input_capture_complete': not any(row.get('resolved') is not True for row in self.failures)}
        return original_annotate(self,name,value)
    evidence.CaptureSession.annotate = measure
    try:
        with bot.collection_clock(now):
            captured = adapter.capture_daily(bot, run/'evidence', now)
        out['stages']['capture'] = captured
        write(run/'capture.json',captured)
        evidence.CaptureSession.annotate = original_annotate
        print('CAPTURE_DONE '+captured['status'],flush=True)
        if set(originals) == {'IC','IM'} and all(s.get('signal_complete') and s.get('persistence_eligible') for s in originals.values()):
            temporary = state.StateStore(copied)
            appended = temporary.append_confirmed_signals(latest, originals)
            out['stages']['isolated_append'] = {'status':'PASS','verified_day':appended['verified_day'],
                'sequence':appended['sequence'],'digest':appended['digest']}
            write(run/'isolated_original_record.json',appended)
        else:
            out['stages']['isolated_append'] = {'status':'BLOCKED','reason':'Both real original signals must pass normal persistence eligibility.'}
        reader, replay_clock = adapter.load_original(bot,run/'evidence','IC',now.date())
        out['stages']['real_package'] = {'status':'PASS','manifest_sha256':reader.manifest_sha256,
            'raw_responses':len(reader.context['raw']),'typed_inputs':len(reader.context['entries'])}
        urls = {row['url'] for row in reader.context['raw']}
        requests, misses, roles = [], [], []
        http, typed = evidence.EvidenceReader.http_response, evidence.EvidenceReader.call
        def trace_http(self,url,params):
            requests.append(url)
            try:
                return http(self,url,params)
            except evidence.EvidenceUnavailable as exc:
                misses.append({'kind':'http','error':str(exc)})
                raise
        def trace_typed(self,role,key):
            roles.append(role)
            try:
                return typed(self,role,key)
            except evidence.EvidenceUnavailable as exc:
                misses.append({'kind':'typed','role':role,'error':str(exc)})
                raise
        evidence.EvidenceReader.http_response, evidence.EvidenceReader.call = trace_http, trace_typed
        bot.install_runtime_anchors(state.anchors_from_record(latest))
        def offline(*args,**kwargs):
            raise RuntimeError('Real-package replay attempted unrecorded external network')
        bot.requests.get = offline
        with adapter.evidence_location(run/'evidence'), bot.historical_replay(now.date()):
            replay = bot.build_live_trade_signal('IC',datetime.now(bot.BEIJING),'close')
        write(run/'replayed_ic_typed.json',evidence.encode(replay))
        write(run/'replayed_ic.json',state._jsonable(replay))
        out['stages']['replay'] = {'complete':replay.get('signal_complete'),'eligible':replay.get('persistence_eligible'),
            'diagnostic_status':replay.get('diagnostic_status'),'reason':replay.get('diagnostic_reason'),
            'caught_evidence_unavailable':misses,'requests':requests,'original_urls':sorted(urls),
            'address_subset':set(requests)<=urls,'read_roles':roles,
            'reported_missing':replay.get('original_evidence',{}).get('unrecorded_inputs')}
        comparison_path = ROOT/'quant_research_runs/20261022_calendar_capture_review_v1/replay_acceptance.py'
        spec_cmp = importlib.util.spec_from_file_location('actual_package_compare',comparison_path)
        compare = importlib.util.module_from_spec(spec_cmp)
        spec_cmp.loader.exec_module(compare)
        original_record_path = run/'isolated_original_record.json'
        if original_record_path.exists():
            original_signal = json.loads(original_record_path.read_text())['signals']['IC']
            differences = compare.compare_signal_values(original_signal,state._jsonable(replay))
            write(run/'field_differences.json',differences)
            out['stages']['field_comparison'] = {'status':'PASS' if not differences else 'FAIL',
                'differences':len(differences),'paths':[d['path'] for d in differences]}
        else:
            out['stages']['field_comparison'] = {'status':'BLOCKED','reason':'No eligible original isolated ledger result.'}
        out['status'] = ('PASS' if out['stages']['field_comparison']['status']=='PASS' and not misses
                         and set(requests)<=urls and replay.get('persistence_eligible') else 'NOT_ACCEPTED')
    except Exception as exc:
        out.update(status='NOT_ACCEPTED',error_type=type(exc).__name__,error=str(exc)[:2000],traceback=traceback.format_exc())
        print('EXPERIMENT_ERROR '+type(exc).__name__,flush=True)
    finally:
        evidence.CaptureSession.annotate = original_annotate
        after = {p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT/'runtime').rglob('*') if p.is_file()}
        out['formal_runtime_unchanged'] = after == runtime_before
        out['anchors_unchanged'] = evidence.encode(bot.LIVE_CONTINUATION_ANCHOR) == anchors_before
        out['finished_at'] = datetime.now(bot.BEIJING).isoformat()
        write(run/'receipt.json',out)
        print(json.dumps(out,ensure_ascii=False),flush=True)
    assert out['formal_runtime_unchanged']


if __name__ == '__main__':
    main()
