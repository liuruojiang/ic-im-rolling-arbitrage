"""Read-only counterexamples. No server import, network, ledger, or old evidence writes."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import hashlib
import json
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime
from threading import Event, Thread
from types import SimpleNamespace
import numpy as np
import pytest
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_icim_batch3_old_call_20261021 as seed

class Output:
    def __init__(self): self.text, self.files = [], []
    def write(self, value): self.text.append(str(value))
    def attach_file(self, **value): self.files.append(value)

sources = {Path(__file__).resolve()}
for module in tuple(sys.modules.values()):
    filename = getattr(module, '__file__', None)
    if filename:
        path = Path(filename).resolve()
        if path.suffix == '.py' and path.is_relative_to(ROOT): sources.add(path)
def hashes():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(sources)}
before = hashes()
result = {}
checks = []
def check(name, value):
    assert value, name
    checks.append(name)

with pytest.MonkeyPatch.context() as m:
    def no_network(*a, **k): raise AssertionError('unexpected network')
    m.setattr(bot.requests, 'get', no_network)
    clock = datetime(2027, 1, 4, 16, tzinfo=bot.BEIJING)
    m.setattr(bot, '_now_beijing', lambda: clock)
    for query in ['查询', '信号', '实时信号']:
        out = Output()
        @contextmanager
        def capture(): yield out
        m.setattr(bot, '_sm', capture)
        m.setattr(bot.poe, 'query', SimpleNamespace(text=query), raising=False)
        bot.ICIMMainlinesBot().run()
        result['public_' + query] = ''.join(out.text)
    for query in ['查询', '信号']:
        check('public_2027_' + query + '_global_failure',
              '查询失败' in result['public_' + query] and
              'CalendarCoverageError' in result['public_' + query] and
              '最后逐腿核验快照' not in result['public_' + query])
    check('public_2027_intraday_isolates_products',
          '查询失败' not in result['public_实时信号'] and
          result['public_实时信号'].count('calendar_unverified') == 2)

with pytest.MonkeyPatch.context() as m:
    seed.wire(m, held=False)
    m.setattr(bot.requests, 'get', no_network)
    stamp = seed.CLOCK.isoformat()
    fear = dict(fear_source_policy_revision=bot.fear_grid.SOURCE_POLICY_REVISION,
                fear_fetch_started_at=stamp, fear_retrieved_at=stamp,
                fear_csv_sha256=None, fear_greed_index=None, fear_data_date=None,
                fear_data_status='unavailable', fear_unavailable_reason='synthetic sources unavailable',
                fear_available_sources=[], fear_selected_source=None,
                fear_cross_validation='unavailable', fear_page_updated_at=None,
                fear_page_data_date=None, fear_source_url=None)
    m.setattr(bot.fear_grid, 'fetch_score', lambda *a: deepcopy(fear))
    signal = bot.build_live_trade_signal('IM', seed.CLOCK, 'close')
    result['grid'] = {k: signal.get(k) for k in
        ['signal_complete', 'persistence_eligible', 'diagnostic_only', 'close_confirmed',
         'grid_target', 'grid_action', 'grid_transition_reason', 'fear_data_status']}
    confirmed = bot.fear_grid.publication_view(signal).get('grid_target_confirmed', True)
    result['grid']['publication_confirmed'] = confirmed
    check('full_builder_grid_unconfirmed_but_eligible', confirmed is False and
          signal['signal_complete'] is True and signal['persistence_eligible'] is True)
    with bot.runtime_clock(seed.CLOCK):
        for name, fn in [('persistence_gate', state.require_persistence_eligible),
                         ('fear_grid_state_validator', state.validate_fear_grid_signal),
                         ('full_new_delivery_validator', state.validate_delivery_values)]:
            fn(signal, 'IM')
            result['grid'][name] = 'PASS'
            check(name + '_accepts_unconfirmed_grid', True)

out = Output()
signal = bot.make_diagnostic_signal('IM', seed.CLOCK, 'synthetic')
signal['diagnostic_model']['numpy_integer'] = np.int64(7)
signal['diagnostic_model']['numpy_boolean'] = np.bool_(True)
bot._write_diagnostic_signal(out, 'IM', signal)
export = json.loads(out.files[0]['contents'])['diagnostic_model']
result['numpy_export'] = {k: dict(value=v, type=type(v).__name__)
                          for k, v in export.items() if k.startswith('numpy')}
check('numpy_integer_exported_as_string', type(export['numpy_integer']) is str)
check('numpy_boolean_exported_as_string', type(export['numpy_boolean']) is str)

with pytest.MonkeyPatch.context() as m:
    seed.wire(m, held=False)
    original = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    ready, release = Event(), Event()
    thread_errors = []
    def controlled_projection(product, *a):
        # Controlled private compute; public builder lock/finally and public snapshot are real.
        projected = deepcopy(bot.LIVE_CONTINUATION_ANCHOR[product])
        projected['post_core_contract'] = 'IM_UNVERIFIED_PROJECTION'
        bot.LIVE_CONTINUATION_ANCHOR[product] = projected
        ready.set()
        if not release.wait(5): raise AssertionError('release timeout')
        return {'v14_action': 'HOLD'}
    m.setattr(bot, '_build_live_trade_signal', controlled_projection)
    def build():
        try: bot.build_live_trade_signal('IM', seed.CLOCK, 'close')
        except BaseException as exc: thread_errors.append(repr(exc))
    builder = Thread(target=build)
    builder.start()
    try:
        assert ready.wait(3)
        out = Output()
        @contextmanager
        def capture(): yield out
        m.setattr(bot, '_sm', capture)
        m.setattr(bot.poe, 'query', SimpleNamespace(text='查询IM'), raising=False)
        m.setattr(bot, '_now_beijing', lambda: datetime(2026, 10, 21, 14, tzinfo=bot.BEIJING))
        bot.ICIMMainlinesBot().run()
        result['public_snapshot_during_projection'] = ''.join(out.text)
        check('public_snapshot_publishes_projection_as_verified',
              'IM_UNVERIFIED_PROJECTION' in ''.join(out.text) and '最后逐腿核验快照' in ''.join(out.text))
        safe_done, safe = Event(), {}
        def safe_copy():
            with bot.STRATEGY_QUERY_LOCK:
                safe.update(deepcopy(bot.LIVE_CONTINUATION_ANCHOR['IM']))
            safe_done.set()
        reader = Thread(target=safe_copy)
        reader.start()
        check('locked_copy_waits_for_builder', not safe_done.wait(.15))
    finally:
        release.set()
        builder.join(3)
    reader.join(3)
    assert not thread_errors and not builder.is_alive() and not reader.is_alive(), thread_errors
    check('builder_restores_original_anchor', bot.LIVE_CONTINUATION_ANCHOR == original)
    check('locked_deepcopy_reads_original_anchor', safe == original['IM'])
    result['safe_snapshot_contract'] = safe['post_core_contract']

def calendar_failure(day): raise bot.CalendarCoverageError('synthetic missing calendar')
notice = bot.option_maintenance_notice
check('maintenance_callback_exception_is_swallowed',
      notice._valid_maintenance_day(date(2027, 1, 15), calendar_failure) is False)
check('reminder_callback_exception_is_swallowed',
      notice.reminder('IM', dict(market_date='2027-01-15', close_confirmed=False,
                      put_monthly_reset_execution_date='2027-01-15'),
                      is_session=calendar_failure) == '')
after = hashes()
check('source_hashes_unchanged', before == after)
result.update(check_count=len(checks), checks=checks, source_before=before, source_after=after,
              source_unchanged=before == after, exit_code=0,
              note='Counterexamples intentionally assert existing defects; no production fix applied.')
destination = Path(__file__).with_name('display_review_repro_receipt.json')
with destination.open('x', encoding='utf-8') as handle:
    json.dump(result, handle, ensure_ascii=False, indent=2, default=str)
print(json.dumps(dict(check_count=len(checks), checks=checks, source_unchanged=before == after,
                     exit_code=0, receipt=str(destination)), ensure_ascii=False, indent=2))
