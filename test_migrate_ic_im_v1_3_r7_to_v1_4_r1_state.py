from __future__ import annotations

import json
from datetime import date

import ic_im_v1_4_policy as policy
import poe_ic_im_v1_3_state as old_state
import poe_ic_im_v1_4_state as new_state
from migrate_ic_im_v1_3_r7_to_v1_4_r1_state import migrate
from test_poe_ic_im_v1_3_state import _signals


def test_one_way_migration_preserves_parent_and_disables_legacy_3x(tmp_path):
    source = tmp_path / "v13"
    target = tmp_path / "v14"
    parent = old_state.StateStore(source).initialize()
    migration = migrate(source, target)
    record = new_state.StateStore(target).load_latest()
    assert record["schema_version"] == 4
    assert record["strategy_version"] == "1.4"
    assert record["strategy_revision"] == "r1"
    assert record["sequence"] == 0
    assert record["verified_day"] == parent["verified_day"]
    assert record["genesis"]["parent_digest"] == parent["digest"]
    assert migration["source_ledger"]["digest"] == parent["digest"]
    for product in ("IC", "IM"):
        anchor = record["products"][product]
        assert anchor["v14_route_state"] == "future"
        assert anchor["v14_short_put_contract"] is None
        assert anchor["v14_core_put_profit3x_eligible"] is False
        policy.validate_extension(product, anchor)
    proof = json.loads((target / "migration_record.json").read_text(encoding="utf-8"))
    assert proof["new_ledger"]["digest"] == record["digest"]


def test_migrated_schema4_chain_advances_atomically_before_effective_date(tmp_path):
    source = tmp_path / "v13"
    target = tmp_path / "v14"
    old_state.StateStore(source).initialize()
    migrate(source, target)
    store = new_state.StateStore(target)
    current = store.load_latest()
    signals = _signals(date(2026, 8, 25))
    for product, signal in signals.items():
        signal.update(policy.default_extension(product))
        signal.update(
            strategy_version="1.4",
            strategy_revision="r1",
            v14_build_id=policy.BUILD_ID,
            v14_rule_revision=policy.RULE_REVISION,
        )
    advanced = store.append_confirmed_signals(current, signals)
    assert advanced["sequence"] == 1
    assert advanced["previous_digest"] == current["digest"]
    assert advanced["verified_day"] == "2026-08-25"
    for product in ("IC", "IM"):
        policy.validate_extension(product, advanced["products"][product])


def test_migration_preserves_ic_both_sleeves_and_forward_updates(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'target'
    old_store = old_state.StateStore(source)
    initial = old_store.initialize()
    signals = _signals(date(2026, 8, 25))
    signals['IC'].update(put_target_core_qty=7, put_target_momentum_qty=7,
                         core_put_target_delta=.25, momentum_put_target_delta=.25,
                         total_put_target_delta=.5)
    parent = old_store.append_confirmed_signals(initial, signals)
    migrate(source, target)
    migrated = new_state.StateStore(target).load_latest()
    anchor = new_state.anchors_from_record(migrated)['IC']
    assert (anchor['verified_core_put_qty'], anchor['verified_momentum_put_qty']) == (7, 7)
    assert anchor['v14_core_put_qty'] == 7
    assert anchor['v14_core_put_contract'] == '510500P2612M07500'
    assert anchor['v14_core_put_security_id'] == '10012099'
    assert anchor['v14_core_put_profit3x_eligible'] is False
    assert migrated['genesis']['parent_digest'] == parent['digest']
    next_signals = _signals(date(2026, 8, 26))
    next_signals['IC'].update(put_target_core_qty=8, put_target_momentum_qty=6,
                              core_put_target_delta=.25, momentum_put_target_delta=.25,
                              total_put_target_delta=.5)
    for product, signal in next_signals.items():
        signal.update(policy.default_extension(product))
        signal.update(strategy_version='1.4', strategy_revision='r1',
                      v14_build_id=policy.BUILD_ID, v14_rule_revision=policy.RULE_REVISION)
    updated = new_state.StateStore(target).append_confirmed_signals(migrated, next_signals)
    assert updated['products']['IC']['verified_core_put_qty'] == 8
    assert updated['products']['IC']['verified_momentum_put_qty'] == 6
