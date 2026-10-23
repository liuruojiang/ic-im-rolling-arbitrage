"""Actual CaptureRunner.cycle + actual official snapshot guard, offline."""
from contextlib import nullcontext
from datetime import datetime, timedelta
import json
import os
from pathlib import Path

import pytest
import requests

import capture_ic_im_v1_4_inputs as runner
import ic_im_evidence_adapter_v1 as adapter
import poe_ic_im_mainline_v1_4_bot as bot
from test_icim_free_sources_capture_runner_20261022 import fixture_runner


CLOCK = datetime(2026, 10, 22, 18, 45, tzinfo=bot.BEIJING)
OUT = Path(os.environ['ICIM_TEST_OUTPUT_DIR'])


@pytest.mark.parametrize('outer_collection_context', [False, True])
def test_actual_cycle_accounts_for_readonly_preflight_elapsed_time(monkeypatch, tmp_path, outer_collection_context):
    owner, store, record = fixture_runner(tmp_path)
    before = runner.ledger_hashes(store.root)
    tick = [100.]
    seen = {}
    monkeypatch.setattr(requests.sessions.Session, 'request', lambda *a, **k: pytest.fail('network forbidden'))
    monkeypatch.setattr(bot.time_module, 'monotonic', lambda: tick[0])

    class Wall(datetime):
        @classmethod
        def now(cls, tz=None):
            current = CLOCK + timedelta(seconds=tick[0] - 100.)
            return current.astimezone(tz) if tz else current.replace(tzinfo=None)

    monkeypatch.setattr(runner, 'datetime', Wall)
    original_load = runner.load_ledger_readonly

    def delayed_real_readonly_load(*args):
        value = original_load(*args)
        # Deterministic disk/preflight delay, not forged market time. Actual
        # readonly loader still validates the real disposable hash chain.
        tick[0] += 2.
        return value

    def consume_current_frame(strategy, root, clock):
        quote_stamp = CLOCK + timedelta(seconds=1)
        observation = strategy._iv_observation_clock(clock)
        seen.update(start=clock.isoformat(), quote=quote_stamp.isoformat(),
                    wall=Wall.now(bot.BEIJING).isoformat(), observation=observation.isoformat())
        strategy.quote_snapshot_guard.official_snapshot_evidence(
            quote_stamp, observation, clock.date(), 'current frame after readonly preflight')
        return {'status': 'captured', 'day': str(clock.date()), 'products': {}}

    monkeypatch.setattr(runner, 'load_ledger_readonly', delayed_real_readonly_load)
    monkeypatch.setattr(adapter, 'capture_daily', consume_current_frame)
    with bot.collection_clock(CLOCK) if outer_collection_context else nullcontext():
        result = owner.cycle()
    seen['result'] = result
    (OUT / f'preflight_observation_outer_{outer_collection_context}.json').write_text(
        json.dumps(seen, ensure_ascii=False, indent=2), encoding='utf-8')
    assert runner.ledger_hashes(store.root) == before
    assert result['ledger_paths_and_bytes_unchanged'] is True
    assert result['status'] == 'captured', seen
