"""Same-start FIX11 account replay on the audited, snapshot-pinned inputs."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import requests

from current_history_adapter import CurrentHistory, load, sha
from repair_guards.replay_guards import (
    FROZEN, compare_prefix, expected_exchange_days, read_receipt,
    verify_chart_gate, verify_frozen_market_snapshot, verify_resume_receipt,
)


HERE = Path(__file__).resolve().parent
START = date(2025, 11, 18)
ANCHOR = date(2025, 11, 17)
ORIGINAL_ENGINE_SHA = '4cdd4fddaaff908e67a0fefdf985051d003adb0b05328fa4feebfc972e59bf12'
ORIGINAL_ADAPTER_SHA = '0fe1846907d376d51ffae5b8dd425d9877d3f155175917db302a181b61025ec1'
ORIGINAL_REPORT_SHA = '544836669f984fa3827da9ed5f2e1b46cdd77a7519f78217d8fa973d3a0bd068'
ORIGINAL_DAILY_SHA = '9edb370d8f6df979c2c6a85c660f5c068443443f302bff3e960faa8bcde9506d'
ORIGINAL_EVENTS_SHA = '4b41ea9da224c7db9185230107a40de07fb34e5aed29cb61c418f8e0bafe324e'


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False, default=str) + '\n', encoding='utf-8')


def apply_verified_terminal_filter(chain, end):
    if chain.sources._ic_cache:
        raise RuntimeError('terminal quote repair must precede any cached IC reads')
    repair = HERE / 'repair_source'
    receipt = read_receipt(repair / 'verification.json')
    name = 'sina_510500_puts_20261118_close.csv.gz'
    target = repair / 'terminal_market_repro' / name
    if receipt.get('status') != 'PASS' or sha(target) != receipt['candidate_outputs_sha256'][name]:
        raise RuntimeError('terminal classification repair is not verified')
    fixed = pd.read_csv(target, parse_dates=['date'], dtype={'security_id': str})
    frozen = chain.sources.ic_noon
    keys = ['date', 'security_id']
    fixed = fixed.set_index(keys, verify_integrity=True).sort_index()
    frozen = frozen.set_index(keys, verify_integrity=True).sort_index()
    if not fixed.index.equals(frozen.index) or set(fixed.index.get_level_values('date')) != {pd.Timestamp(end)}:
        raise RuntimeError('terminal classification repair has different dated identities')
    for field in ['raw_close', 'volume', 'source_stamp', 'source']:
        if not fixed[field].equals(frozen[field]):
            raise RuntimeError(f'terminal classification repair changed raw {field}')
    prices = ['open', 'high', 'low', 'close']
    published = frozen[prices].to_numpy(float)
    repaired = fixed[prices].to_numpy(float)
    changed = ~np.isclose(published, repaired, rtol=0, atol=1e-14, equal_nan=True).all(axis=1)
    if fixed.index[changed].tolist() != [(pd.Timestamp(end), '10012149')] or not fixed.loc[changed, prices].isna().all().all():
        raise RuntimeError('repair must quarantine only the confirmed midnight quote')
    chain.sources.ic_noon = fixed.reset_index()
    master = chain.sources.ic_master
    selected = master.date.eq(pd.Timestamp(end)) & master.security_id.eq('10012149')
    if selected.sum() != 1:
        raise RuntimeError('terminal repaired Greeks lack a unique master identity')
    master.loc[selected, ['delta', 'implied_volatility']] = np.nan
    return {'status': 'PASS', 'security_id': '10012149', 'day': str(end), 'repair_receipt_sha256': sha(repair / 'verification.json'),
            'admitted_quote_sha256': sha(target), 'original_raw_price_and_volume_preserved': True}


def compare_event_prefix(a, b, end):
    a = a.loc[a.date.le(end)].reset_index(drop=True)
    b = b.loc[b.date.le(end)].reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, check_dtype=False, rtol=1e-13, atol=1e-8)
    return len(a)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scratch', type=Path, required=True)
    args = parser.parse_args()
    raw = FROZEN / 'fresh_sources/extended_inputs'
    signal, end, manifest = verify_frozen_market_snapshot(raw, raw.parent / 'refresh_manifest.json')
    prior_report = FROZEN / 'full_account_verification.json'
    prior_daily = FROZEN / 'full_account_daily.csv.gz'
    prior_events = FROZEN / 'full_account_events.csv.gz'
    for path, pin in ((prior_report, ORIGINAL_REPORT_SHA), (prior_daily, ORIGINAL_DAILY_SHA),
                      (prior_events, ORIGINAL_EVENTS_SHA), (FROZEN / 'native_account_fix11_current.py', ORIGINAL_ENGINE_SHA),
                      (HERE / 'current_history_adapter.py', ORIGINAL_ADAPTER_SHA)):
        if sha(path) != pin:
            raise RuntimeError(f'pinned original source or result differs: {path.name}')
    prior = read_receipt(prior_report)
    if prior['status'] != 'PASS':
        raise RuntimeError('published replay lacks a passing receipt')
    network_attempts = []
    def offline(self, method, url, *args, **kwargs):
        network_attempts.append(url)
        raise RuntimeError('offline research blocked network: ' + url)
    requests.sessions.Session.request = offline
    chain = CurrentHistory(raw, args.scratch, signal.isoformat())
    for key in ('formal_source_sha256', 'policy_source_sha256', 'seller_lifecycle_sha256', 'historical_identity_binding_source_sha256'):
        if chain.audit[key] != prior[key]:
            raise RuntimeError(f'current dependency differs from published snapshot: {key}')
    stages = verify_resume_receipt(FROZEN / 'second_attempt/verified_input_stages.json', FROZEN / 'inputs',
                                  manifest_sha256=sha(manifest), formal_source_sha256=chain.audit['formal_source_sha256'])
    for name in stages['input_stage_hashes']:
        shutil.copyfile(FROZEN / 'inputs' / name, chain.inputs / name)
    chain.source()
    repair = apply_verified_terminal_filter(chain, end)
    chain.risk_engine = SimpleNamespace(OHLCV={'IC': raw / 'sina_000905_index.csv.gz',
                                               'IM': raw / 'im_ohlcv_frozen_plus_fresh.csv.gz'})
    chain.prepare_terminal(args.scratch / 'terminal_inputs', end.isoformat())
    candidates = chain.candidates({product: count + 1 for product, count in chain.counts.items()})
    chain.account = load('monthly_timing_isolated_account', HERE / 'native_account_fix11_t1_open.py')
    chain.paired.listed.grid_scan.account_stage = chain.account
    chain.lifecycle.HERE, chain.lifecycle.VERSION = chain.inputs, 'v4'
    chain.lifecycle.OHLCV = chain.risk_engine.OHLCV
    gov = pd.read_csv(raw / 'chinabond_government_10y.csv', parse_dates=['date']).set_index('date', verify_integrity=True).gov10y_yield
    spot = pd.read_csv(chain.risk_engine.OHLCV['IM'], parse_dates=['date']).set_index('date', verify_integrity=True).close
    results = {'baseline': [], 't1_open': []}
    detail = {}
    for product in ('IC', 'IM'):
        with patch.object(chain.formal.v14_policy, 'IC_SELLER_MOM120_EFFECTIVE_SIGNAL_DATE', date(1900, 1, 1)), \
             patch.object(chain.formal, '_v14_ic_short_put_candidate', new=chain.dated_ic_candidate(chain.formal._v14_ic_short_put_candidate)):
            life, life_audit = chain.lifecycle.run(product, 'repeat_roll', chain.sources, gov, spot)
        if life.action.eq('HOLD_FAIL_CLOSED').any():
            raise RuntimeError(f'unresolved seller lifecycle for {product}')
        for name, start in (('baseline', None), ('t1_open', START)):
            chain.paired.listed.grid_scan.configure_account(chain.inputs)
            chain.account.OHLCV = chain.risk_engine.OHLCV
            chain.account.MONTHLY_PUT_NEXT_OPEN_FROM = start
            daily, events, audit = chain.account.replay(product, 'repeat_roll', chain.sources, life, candidates, gov, spot)
            events['product'] = product
            results[name].append((daily, events))
            detail[f'{product}_{name}'] = audit
            print(f'{product} {name} {len(daily)} days end NAV {daily.nav.iloc[-1]:.12f}', flush=True)
    frames = {name: tuple(pd.concat(series, ignore_index=True) for series in zip(*values, strict=True))
              for name, values in results.items()}
    base, base_events = frames['baseline']
    candidate, candidate_events = frames['t1_open']
    chart_days = expected_exchange_days(chain.formal, ANCHOR, end)
    chart_parent = {'status': 'PASS', 'latest_completed_close': end.isoformat(), 'latest_mark_date': end.isoformat()}
    for daily, events in frames.values():
        verify_chart_gate(daily, chart_parent, officially_expected_days=chart_days)
        if (daily.cash_close - daily.margin_reserved).min() < -1e-7:
            raise RuntimeError('insufficient free cash in measured account')
        trades = events.loc[events.event.eq('trade')]
        if trades.symbol.str.startswith('option|call|').any():
            raise RuntimeError('unexpected Call trade')
        if not trades.loc[trades.date.eq(end.isoformat()), 'signal_date'].eq(signal.isoformat()).all():
            raise RuntimeError('terminal quote used to create a new signal')
        if events.loc[events.date.eq(end.isoformat()), 'symbol'].fillna('').str.endswith('510500P2612M06500').any():
            raise RuntimeError('quarantined midnight quote entered terminal account')
    published = compare_prefix(base, pd.read_csv(prior_daily), end.isoformat())
    published_entries = compare_event_prefix(base_events, pd.read_csv(prior_events), end.isoformat())
    same_start = compare_prefix(candidate, base, ANCHOR.isoformat())
    same_start_entries = compare_event_prefix(candidate_events, base_events, ANCHOR.isoformat())
    delayed = candidate_events.loc[candidate_events.event.eq('trade') & candidate_events.price_basis.eq('T_plus_1_monthly_maintenance_dated_open')]
    if delayed.empty or not delayed.phase.eq('open').all():
        raise RuntimeError('comparison did not execute any delayed maintenance')
    sessions = dict(zip(chart_days[:-1], chart_days[1:], strict=True))
    if not delayed.signal_date.map(sessions).eq(delayed.date).all():
        raise RuntimeError('maintenance executed outside its next trading day')
    if candidate_events.loc[candidate_events.date.ge(START.isoformat()), 'price_basis'].eq('T_monthly_maintenance_close').any():
        raise RuntimeError('comparison retained same-close monthly trades')
    if network_attempts:
        raise RuntimeError('replay attempted outside input')
    names = {}
    for name, (daily, events) in frames.items():
        for suffix, frame in (('daily', daily), ('events', events)):
            path = HERE / f'{name}_{suffix}.csv.gz'
            frame.to_csv(path, index=False, float_format='%.17g', compression={'method': 'gzip', 'mtime': 1})
            names[path.name] = sha(path)
    path = HERE / 'full_account_verification.json'
    write_json(path, {**chart_parent, 'risk_last_completed_signal': str(signal), 'anchor': str(ANCHOR), 'start': str(START),
                      'classification': 'same_start_research_counterfactual_monthly_put_open', 'source_manifest_sha256': sha(manifest),
                      'baseline_against_published': published, 'baseline_event_rows_matched': published_entries,
                      'same_initial_account': same_start, 'same_initial_event_rows': same_start_entries,
                      'terminal_quote_repair': repair, 'delayed_trade_sides': delayed.groupby('product').size().to_dict(),
                      'monthly_open_plans': {p: detail[f'{p}_t1_open']['monthly_t1_open_plans'] for p in ('IC', 'IM')},
                      'details': detail, 'outputs_sha256': names,
                      'source_sha256': {p.name: sha(p) for p in (Path(__file__), HERE / 'native_account_fix11_t1_open.py',
                                          HERE / 'current_history_adapter.py', HERE / 'repair_guards/replay_guards.py')},
                      'comparison_calendar': chart_days, 'network_attempts': network_attempts})
    print(f'PASS {path}', flush=True)


if __name__ == '__main__':
    main()
