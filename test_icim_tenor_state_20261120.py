"""Independent boundary probes: diagnostics are not economic ledger inputs."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

import ic_im_quarter_roll_v1_3 as spreads
import poe_ic_im_v1_4_state as state


@pytest.fixture
def verified_records():
    root = Path(__file__).parent / 'tests/fixtures/icim_verified_ledger_20261021/journal'
    paths = [root / '000018-2026-10-20.json', root / '000019-2026-10-21.json']
    return tuple(json.loads(path.read_bytes()) for path in paths)


def test_existing_ledger_and_replayed_economic_anchors_unchanged(verified_records):
    previous, current = verified_records
    original = deepcopy((previous, current))
    state._validate_record(previous)
    state._validate_record(current)
    assert state._digest(previous) == previous['digest'] == current['previous_digest']
    assert state._digest(current) == current['digest']
    assert state._jsonable(state.derive_next_anchors(previous, current['signals'])) == current['products']
    for product, signal in current['signals'].items():
        assert not set(spreads.TENOR_SPREAD_LABELS).intersection(signal)
        assert all('期限贴水' not in line for line in spreads.format_spreads(signal, product=product))
    assert (previous, current) == original


@pytest.mark.parametrize('metadata', [
    {'status': 'unavailable', 'reason': 'missing optional far leg'},
    {'status': 'ok', 'annualized': 99.99, 'points': -99999, 'unrelated': 'untrusted display metadata'},
])
def test_spread_metadata_is_hash_bound_but_never_an_economic_anchor(verified_records, metadata):
    previous, current = verified_records
    changed = deepcopy(current)
    for signal in changed['signals'].values():
        for key in spreads.TENOR_SPREAD_LABELS:
            signal[key] = deepcopy(metadata)
    # Deliberate adversarial injection onto a verified historical input tests
    # policy isolation only. The actual producer does not backfill old dates.
    assert state._jsonable(state.derive_next_anchors(previous, changed['signals'])) == current['products']
    assert state._digest(changed) != current['digest']
    with pytest.raises(RuntimeError, match='SHA-256'):
        state._validate_record(changed)
    changed['digest'] = state._digest(changed)
    state._validate_record(changed)
    assert changed['previous_digest'] == previous['digest']
    assert state._digest(current) == current['digest']
