"""Independent saved-output arithmetic, date attribution and frozen-file guards."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
RUN = HERE.parent
ROOT = RUN.parents[1]
HIST = ROOT / 'quant_research_runs/20261016_put_execution_repairs_v1'
NEW = RUN / 'rebuilt_02'
PREVIOUS_AUDIT = ROOT / 'quant_research_runs/20261022_five_rounds_backtest_impact_v1'


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def read(p):
    return json.loads(Path(p).read_text(encoding='utf-8-sig'))


def metrics(frame):
    returns = frame.return_net.to_numpy(float)
    nav = np.r_[1., np.cumprod(1 + returns)]
    return {'rows': len(frame), 'start': str(frame.date.iloc[0]), 'end': str(frame.date.iloc[-1]),
            'cagr': float(nav[-1] ** (252 / len(frame)) - 1),
            'max_dd': float((nav / np.maximum.accumulate(nav) - 1).min()),
            'final_nav': float(nav[-1]), 'sharpe': float(returns.mean() / returns.std(ddof=1) * np.sqrt(252)),
            'fees': float(frame.fees_cumulative.iloc[-1])}


def compare_columns(a, b, key):
    if a[key].astype(str).tolist() != b[key].astype(str).tolist():
        raise RuntimeError(f'date alignment changed: {key}')
    result, differences = {}, []
    for col in a.columns.intersection(b.columns):
        x, y = a[col], b[col]
        if pd.api.types.is_numeric_dtype(x) and pd.api.types.is_numeric_dtype(y):
            u, v = x.to_numpy(float), y.to_numpy(float)
            finite = np.isfinite(u) & np.isfinite(v)
            changed = (np.isnan(u) != np.isnan(v)) | (finite & (np.abs(u - v) > 1e-12))
            maximum = float(np.abs(u[finite] - v[finite]).max()) if finite.any() else 0.
        else:
            changed = x.fillna('<NA>').astype(str).ne(y.fillna('<NA>').astype(str)).to_numpy()
            maximum = None
        result[col] = {'different_rows': int(changed.sum()), 'max_abs_numeric_difference': maximum}
        for index in np.flatnonzero(changed):
            differences.append({'day': str(a[key].iloc[index]), 'field': col,
                                'before': str(x.iloc[index]), 'after': str(y.iloc[index])})
    return result, differences


def write(name, obj):
    p = HERE / name
    if p.exists():
        raise FileExistsError(p)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    HERE.mkdir(exist_ok=True)
    assert read(NEW / 'receipt.json')['status'] == 'PASS'
    assert read(RUN / 'original_entry_runs/execution_history_after/receipt.json')['status'] == 'PASS'
    assert read(RUN / 'rebuilt_01/interpretation.json')['comparison_eligible'] is False
    spec = importlib.util.spec_from_file_location('calendar_output_compare', PREVIOUS_AUDIT / 'compare_and_verify.py')
    compare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compare)
    control = compare.compare_folder(HIST, RUN / 'original_entry_runs/execution_history_after')
    assert control['status'] == 'PASS' and control['max_numeric_difference'] == 0
    before = pd.read_csv(HIST / 'daily_paths.csv.gz')
    before = before.loc[before.arm.eq('both')].reset_index(drop=True)
    after = pd.read_csv(NEW / 'daily_paths.csv.gz')
    old_events = pd.read_csv(HIST / 'events.csv.gz', low_memory=False)
    old_events = old_events.loc[old_events.arm.eq('both')].copy()
    new_events = pd.read_csv(NEW / 'events.csv.gz', low_memory=False)
    windows, target_diffs, life_diffs, detail = [], [], [], {}
    for product, version in [('IC', 'v2'), ('IM', 'v3')]:
        p = product.lower()
        a = before.loc[before['product'].eq(product)].reset_index(drop=True)
        b = after.loc[after['product'].eq(product)].reset_index(drop=True)
        assert a.date.tolist() == b.date.tolist()
        assert np.isfinite(b.return_net).all() and b.nav.gt(0).all()
        assert np.max(np.abs(np.cumprod(1 + b.return_net) - b.nav)) < 1e-10
        assert (b.cash_close - b.margin_reserved).min() >= -1e-7
        assert abs(new_events.loc[new_events['product'].eq(product), 'fee'].fillna(0).sum() - b.fees_cumulative.iloc[-1]) < 1e-6
        old_risk = pd.read_csv(HIST / 'inputs' / p / f'native_fix4_{p}_risk_signals_v1.csv.gz')
        new_risk = pd.read_csv(NEW / 'inputs' / f'native_fix4_{p}_risk_signals_v1.csv.gz')
        risk_stats, risk_differences = compare_columns(old_risk, new_risk, 'signal_date')
        assert not risk_differences, f'{product}: mathematical risk input changed'
        old_targets = pd.read_csv(HIST / 'inputs' / p / f'native_fix4_{p}_noseller_targets_{version}.csv.gz')
        new_targets = pd.read_csv(NEW / 'inputs' / f'native_fix4_{p}_noseller_targets_{version}.csv.gz')
        target_stats, changes = compare_columns(old_targets, new_targets, 'signal_date')
        for row in changes:
            row['product'] = product
        target_diffs.extend(changes)
        old_life = pd.read_csv(HIST / f'{p}_seller_lifecycle_fixed.csv.gz')
        new_life = pd.read_csv(NEW / f'{p}_seller_lifecycle.csv.gz')
        life_stats, changes = compare_columns(old_life, new_life, 'signal_date')
        for row in changes:
            row['product'] = product
        life_diffs.extend(changes)
        # Current policy must not introduce a seller route/position/cost change.
        money_fields = ['action', 'route_before', 'route_after_execution', 'active_contract_after',
                        'entry_premium_state_after', 'early_roll_used_after', 'fill_old_t1', 'fill_new_t1',
                        'settlement_value', 'cycle_pnl_reference', 'recovery_pnl_reference']
        assert all(life_stats[c]['different_rows'] == 0 for c in money_fields)
        ret_delta = b.return_net.to_numpy(float) - a.return_net.to_numpy(float)
        nav_delta = b.nav.to_numpy(float) - a.nav.to_numpy(float)
        for window, years in [('full', None), ('3Y', 3), ('1Y', 1)]:
            start = None if years is None else pd.Timestamp(a.date.iloc[-1]) - pd.DateOffset(years=years)
            mask = np.ones(len(a), dtype=bool) if start is None else pd.to_datetime(a.date).ge(start).to_numpy()
            old_metrics, new_metrics = metrics(a.loc[mask]), metrics(b.loc[mask])
            windows.append({'product': product, 'window': window,
                            'before_cagr': old_metrics['cagr'], 'after_cagr': new_metrics['cagr'],
                            'cagr_change_pp': 100 * (new_metrics['cagr'] - old_metrics['cagr']),
                            'before_max_dd': old_metrics['max_dd'], 'after_max_dd': new_metrics['max_dd'],
                            'max_dd_change_pp': 100 * (new_metrics['max_dd'] - old_metrics['max_dd'])})
        if product == 'IM':
            assert np.array_equal(a.return_net.to_numpy(float), b.return_net.to_numpy(float))
        actual_trade_rows = new_events.loc[new_events['product'].eq(product) & new_events.event.eq('trade')]
        assert not actual_trade_rows.symbol.astype(str).str.startswith('option|call|').any()
        detail[product] = {'before': metrics(a), 'after': metrics(b),
                           'return_changed_days': int((np.abs(ret_delta) > 1e-12).sum()),
                           'first_return_change': next((str(a.date.iloc[i]) for i in range(len(a)) if abs(ret_delta[i]) > 1e-12), None),
                           'max_daily_return_difference': float(np.abs(ret_delta).max()),
                           'max_nav_difference': float(np.abs(nav_delta).max()),
                           'risk_comparison': risk_stats, 'target_comparison': target_stats,
                           'seller_lifecycle_comparison': life_stats,
                           'actual_trade_rows': len(actual_trade_rows),
                           'target_change_days_excluding_call': sorted({r['day'] for r in target_diffs if r['product'] == product and not r['field'].startswith('call_')})}
    pd.DataFrame(windows).to_csv(HERE / 'window_metrics.csv', index=False)
    pd.DataFrame(target_diffs).to_csv(HERE / 'target_differences.csv', index=False)
    pd.DataFrame(life_diffs).to_csv(HERE / 'lifecycle_differences.csv', index=False)
    write('analysis.json', {'created_at': datetime.now(timezone.utc).isoformat(),
                           'original_unchanged_entry_control': control,
                           'accepted_rebuilt_stage': 'rebuilt_02', 'rejected_comparison_stage': 'rebuilt_01',
                           'source_and_data': {'bot_sha256': sha(ROOT / 'poe_ic_im_mainline_v1_4_bot.py'),
                                               'after_receipt_sha256': sha(NEW / 'receipt.json'),
                                               'before_daily_sha256': sha(HIST / 'daily_paths.csv.gz'),
                                               'after_daily_sha256': sha(NEW / 'daily_paths.csv.gz')},
                           'results': detail, 'windows': windows,
                           'interpretation': 'Risk inputs and seller financial paths unchanged; IC buyer targets change at the 2024-02 holiday-shifted maintenance. IM Call target metadata removed because the inherited account already disables Call.'})
    print(json.dumps({'windows': windows, 'products': {p: {k: v[k] for k in ['return_changed_days', 'first_return_change', 'actual_trade_rows', 'target_change_days_excluding_call']} for p, v in detail.items()}}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
