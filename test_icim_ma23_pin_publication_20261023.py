"""Publication gate adversary; copied real input bytes, synthetic acquisition clock."""
from datetime import date, datetime
import importlib.util
import json
from pathlib import Path
import uuid

import pytest
import requests

import capture_ic_im_v1_4_inputs as runner
import ic_im_evidence_adapter_v1 as adapter
import ic_im_signal_evidence_v1 as evidence
import poe_ic_im_mainline_v1_4_bot as bot
from test_icim_free_sources_capture_runner_20261022 import fixture_runner

ROOT = Path(__file__).resolve().parent
DAY = date(2026, 10, 22)
CLOCK = datetime(2026, 10, 22, 18, 45, tzinfo=bot.BEIJING)
SAVED = ROOT / 'quant_research_runs/20261023_production_provenance_v1/selected_original_bundle'


def test_unpinned_executed_module_rejected_after_capture_cannot_leave_usable_original(tmp_path, monkeypatch):
    owner, store, _ = fixture_runner(tmp_path)
    before = runner.ledger_hashes(store.root)
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: pytest.fail('offline original replay audit'))
    monkeypatch.setattr(evidence, '_now', lambda: CLOCK)
    pinfile = tmp_path / 'pin.json'
    pinfile.write_text(json.dumps({'schema': runner.PIN_SCHEMA, 'files': runner.loaded_sources()}), encoding='utf-8')
    owner.pin = runner.SourcePin(pinfile)
    module_name = 'ic_im_ma23_unpinned_' + uuid.uuid4().hex
    folder = ROOT / 'quant_research_runs/20261023_multiround_adversarial_v1/agents/concurrency/pin_dynamic_deps'
    folder.mkdir(exist_ok=True)
    module_path = folder / (module_name + '.py')
    module_path.write_text('calls = 0\ndef consume(value):\n global calls\n calls += 1\n return value\n', encoding='utf-8')
    observed = {}
    def capture(strategy, output, clock):
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(__import__('sys').modules, module_name, module)
        spec.loader.exec_module(module)
        products = {}
        def consume(value):
            result = module.consume(value)
            observed['module_calls'] = module.calls
            return result
        for product in ['IC', 'IM']:
            original = evidence.load_session(SAVED, DAY, metadata_filter={'product': product})
            metadata = {**original.metadata, 'test_scope': 'synthetic acquisition with copied verified real inputs'}
            with evidence.capture_session(output, DAY, CLOCK, metadata) as session:
                for row in original.context['entries'].values():
                    role, key = row['role'], evidence.decode(row['key'])
                    session.call(role, key, lambda role=role, key=key: consume(original.call(role, key)))
                for row in original.context['raw']:
                    response = requests.Response()
                    response.status_code, response.encoding = row['status_code'], row['encoding']
                    response.headers.update(row['headers'])
                    response._content, response._content_consumed = evidence._verified_blob(SAVED, row), True
                    session.raw_response(row['url'], evidence.decode(row['params']), response)
            products[product] = {'status': 'captured', 'manifest': str(session.manifest_path), 'complete': session.context['complete']}
            # Before the fix these are structurally usable originals, not just
            # arbitrary scope roles. A correct pre-publication guard may now
            # seal incomplete earlier; do not demand that invalid pack load.
            if session.context['complete'] is True:
                adapter.load_original(bot, output, product, DAY)
        observed.update(module_calls=module.calls, products=products)
        return {'status': 'captured', 'day': str(DAY), 'products': products}
    monkeypatch.setattr(adapter, 'capture_daily', capture)
    with pytest.raises(runner.SourcePinError, match='does not cover'):
        owner.cycle(CLOCK)
    assert observed['module_calls'] > 0
    assert runner.ledger_hashes(store.root) == before
    usable = {}
    for product in ['IC', 'IM']:
        try:
            usable[product] = adapter.has_original(owner.evidence_dir, product, DAY)
        except evidence.EvidenceUnavailable:
            usable[product] = False
    assert usable == {'IC': False, 'IM': False}, observed
