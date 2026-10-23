"""Independent takeover audit: synthetic IM holding, actual isolated StateStore."""
from datetime import date
import pytest
import requests
import test_icim_round2_audit_lifecycle_20261021 as f
from test_icim_round3_policy_repairs_20261021 import short_anchor


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, 'request', lambda *a, **k: pytest.fail('network forbidden'))


def im_pair(tmp_path, monkeypatch):
    for module in [f.state, f.bot, f.bot.fear_grid]:
        monkeypatch.setattr(module, 'datetime', f.SyntheticCloseClock)
    store, head = f.recent_store(tmp_path / 'ledger')
    anchors = f.state.anchors_from_record(head)
    held = anchors['IM']
    f.ledger._empty_core(held, 'IM')
    held.update(short_anchor('IM', v14_short_put_contract='MO2612-P-7200', v14_short_put_entry_price_day=f.PREVIOUS,
                             v14_last_event_id='IM:2026-10-19:2026-10-20:ENTER_SHORT_PUT:MO2612-P-7200'))
    head['products']['IM'] = f.state._jsonable(held)
    head['digest'] = f.state._digest(head)
    f.state._validate_record(head)
    store._atomic_write(store.journal_dir / '000000-2026-10-20.json', head)
    store._atomic_write(store.latest_path, head)
    head = store.load_latest()
    anchors = f.state.anchors_from_record(head)
    pair = {p: f.close_evidence(f.ledger._signal(p, anchors[p], f.DAY)) for p in ['IC', 'IM']}
    for s in pair.values():
        s['v14_opening_events'] = []
    assert pair['IM']['v14_action'] == 'HOLD'
    return store, head, pair


def test_im_valid_mark_appends_and_reopens(tmp_path, monkeypatch):
    store, head, pair = im_pair(tmp_path, monkeypatch)
    result = store.append_confirmed_signals(head, pair)
    reopened = f.state.StateStore(store.root).load_latest()
    assert reopened['sequence'] == head['sequence'] + 1
    assert reopened['digest'] == result['digest']


@pytest.mark.parametrize('fault', ['missing', 'wrong_day', 'wrong_contract', 'pending', 'fake_enter', 'fake_roll', 'cost_basis', 'expiry'])
def test_im_bad_mark_or_forged_exemption_cannot_advance(tmp_path, monkeypatch, fault):
    store, head, pair = im_pair(tmp_path, monkeypatch)
    signal = pair['IM']
    before = f.snapshot(store.root)
    if fault == 'wrong_day':
        signal['v14_short_put_mark_day'] = f.PREVIOUS
    elif fault == 'wrong_contract':
        signal['v14_short_put_mark_contract'] = 'MO2612-P-7100'
    else:
        signal.pop('v14_short_put_mark', None)
        if fault == 'pending':
            signal.update(v14_roll_pending=True, v14_action='WAIT_SHORT_PUT_ROLL', v14_roll_trigger_day=f.DAY)
        elif fault == 'fake_enter':
            signal['v14_action'] = 'ENTER_SHORT_PUT'
        elif fault == 'fake_roll':
            signal['v14_action'] = 'ROLL_SHORT_PUT'
        elif fault == 'cost_basis':
            signal.update(v14_short_put_cost_basis=None, v14_short_put_entry_price_day=None)
        elif fault == 'expiry':
            signal['v14_short_put_expiry'] = f.DAY
    with pytest.raises(RuntimeError):
        store.append_confirmed_signals(head, pair)
    assert f.state.StateStore(store.root).load_latest()['digest'] == head['digest']
    assert f.snapshot(store.root) == before
