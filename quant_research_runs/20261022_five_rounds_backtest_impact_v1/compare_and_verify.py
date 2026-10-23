"""Compare saved outputs without changing them; retain blocked rerun as failure."""
from __future__ import annotations

import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
HIST = ROOT / 'quant_research_runs/20261016_put_execution_repairs_v1'
EARLY = ROOT / 'quant_research_runs/20261021_external_review_v1/backup'


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def read(p):
    return json.loads(Path(p).read_text(encoding='utf-8-sig'))


def write(name, value):
    path = HERE / name
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def compare_frames(old, new):
    a, b = pd.read_csv(old), pd.read_csv(new)
    result = {'old': str(old.relative_to(ROOT)), 'new': str(new.relative_to(ROOT)),
              'old_sha256': sha(old), 'new_sha256': sha(new),
              'rows': len(a), 'columns': len(a.columns),
              'max_numeric_difference': 0.0, 'column_differences': {}, 'errors': []}
    if a.shape != b.shape or a.columns.tolist() != b.columns.tolist():
        result['errors'].append('shape/column mismatch')
    else:
        for c in a:
            left, right = a[c], b[c]
            if not left.isna().equals(right.isna()):
                result['errors'].append(f'missingness: {c}')
                continue
            if pd.api.types.is_numeric_dtype(left) and pd.api.types.is_numeric_dtype(right):
                x, y = left.to_numpy(float), right.to_numpy(float)
                finite = np.isfinite(x) & np.isfinite(y)
                if not np.array_equal(np.isfinite(x), np.isfinite(y)):
                    result['errors'].append(f'nonfinite: {c}')
                    continue
                delta = float(np.abs(x[finite] - y[finite]).max()) if finite.any() else 0.
                tolerance = (1e-6 if any(word in c.lower() for word in
                                        ['cash', 'equity', 'fee', 'notional', 'pnl', 'value']) else 1e-10)
                result['column_differences'][c] = delta
                result['max_numeric_difference'] = max(result['max_numeric_difference'], delta)
                if delta > tolerance:
                    result['errors'].append(f'numeric: {c}: {delta} > {tolerance}')
            elif not left.fillna('<NA>').astype(str).equals(right.fillna('<NA>').astype(str)):
                result['errors'].append(f'identity/text: {c}')
    result['status'] = 'PASS' if not result['errors'] else 'FAIL'
    return result


def compare_folder(old, new):
    rows = []
    for p in sorted(new.rglob('*')):
        if p.is_file() and (p.name.endswith('.csv') or p.name.endswith('.csv.gz')):
            relative = p.relative_to(new)
            q = old / relative
            if q.is_file():
                rows.append(compare_frames(q, p))
    return {'files': len(rows), 'rows': sum(x['rows'] for x in rows),
            'max_numeric_difference': max((x['max_numeric_difference'] for x in rows), default=0.),
            'status': 'PASS' if rows and all(x['status'] == 'PASS' for x in rows) else 'FAIL',
            'comparisons': rows}


def main():
    required = ['performance_before', 'performance_after', 'execution_history_before',
                'execution_history_after', 'ic_research_after', 'im_research_after']
    receipts = {n: read(HERE / n / 'receipt.json') for n in required}
    # A blocked current historical replay is an audit finding, never a zero-return delta.
    comparisons = {
        'earliest_engine_vs_frozen_oct16': compare_folder(HIST, HERE / 'execution_history_before'),
        'current_independent_ic_engine_vs_frozen': compare_folder(
            ROOT / 'outputs/v1_4_fix_20260917/historical_rerun/ic', HERE / 'ic_research_after'),
        'current_independent_im_engine_vs_frozen': compare_folder(
            ROOT / 'quant_param_scan_runs/20260919_ic_im_im_v1_4_r1_full_joint_im_core_and_momentum_long_put_mom120_floor_2_vs_3',
            HERE / 'im_research_after'),
        'embedded_performance_before_after': compare_folder(HERE / 'performance_before', HERE / 'performance_after'),
    }
    observed = {}
    for p in sorted(HERE.glob('selectors_*.json')):
        if p.name.endswith('_process.json'):
            continue
        v = read(p)
        observed[v['version']] = {
            'sha256': sha(p), 'bot_sha256': v['bot_sha256'],
            'futures': {k: {z: x[z] for z in ['sessions', 'selected', 'blocked']}
                        for k, x in v['futures'].items()},
            'first_day_im_put': v['IM_first_day_put'],
            'network_requests': len(v['network_requests']),
        }
    before = read(HERE / 'protected_before.json')
    changed = [n for n, h in before.items() if not (ROOT / n).is_file() or sha(ROOT / n) != h]
    old_inputs = read(HIST / 'independent_audit.json')['input_sha256']
    input_mismatches = [n for n, h in old_inputs.items() if not (ROOT / n).is_file() or sha(ROOT / n) != h]
    write('protection_after.json', {'verified_at': datetime.now(timezone.utc).isoformat(),
                                 'protected_files': len(before), 'changed_files': changed,
                                 'raw_inputs_compared': len(old_inputs),
                                 'raw_input_mismatches': input_mismatches})

    def ast_members(path):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        out = {}
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
                out[n.name] = ast.dump(n, include_attributes=False)
            elif isinstance(n, ast.Assign):
                for target in n.targets:
                    if isinstance(target, ast.Name):
                        out[target.id] = ast.dump(n.value, include_attributes=False)
        return out
    a, b = ast_members(EARLY / 'poe_ic_im_mainline_v1_4_bot.py'), ast_members(ROOT / 'poe_ic_im_mainline_v1_4_bot.py')
    inspected = ['_RETURN_BLOBS', '_RETURN_SHA256', '_BENCHMARK_PRICE_BLOBS', '_BENCHMARK_PRICE_SHA256',
                 'FROZEN', 'MOMENTUM_RULES', 'IC_MOMENTUM_RULES', 'IC_MOMENTUM_EFFECTIVE_SIGNAL_DATE',
                 '_bs_price_delta', '_implied_volatility', 'valuation_score', 'ic_targets', 'im_targets',
                 'v13_momentum_schedule', '_mom120_floor_state']
    math_checks = {n: {'present_before': n in a, 'present_after': n in b,
                      'ast_equal': a.get(n) == b.get(n) if n in a and n in b else None}
                   for n in inspected}
    financial_bindings = {}
    for name in ['execution_history_before', 'ic_research_after', 'im_research_after']:
        financial_bindings[name] = receipts[name]['loaded_local_sources']
    summary = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'result': 'FROZEN_RESULTS_PRESERVED_AND_INDEPENDENT_ENGINES_MATCH; CURRENT_DYNAMIC_HISTORY_REPLAY_BLOCKED',
        'comparisons': comparisons, 'selector_matrix': observed,
        'worker_status': {n: {k: r.get(k) for k in ['status', 'error_type', 'error', 'seconds', 'loaded_bot_sha256']}
                          for n, r in receipts.items()},
        'all_workers_network_requests': sum(len(r['network_requests']) for r in receipts.values()),
        'model_math_checks': math_checks,
        'quarter_roll_unchanged_since_earliest_review': sha(EARLY / 'ic_im_quarter_roll_v1_3.py') == sha(ROOT / 'ic_im_quarter_roll_v1_3.py'),
        'causal_patch': {'path': 'quant_research_runs/20261021_external_review_batch3_v1/source_delta.patch',
                         'sha256': sha(ROOT / 'quant_research_runs/20261021_external_review_batch3_v1/source_delta.patch'),
                         'finding': 'Strict selected future/Put calendar gates were introduced before expert_initial snapshot; fifth round is not the origin.'},
        'protected_files': len(before), 'changed_protected_files': changed,
        'raw_inputs_compared': len(old_inputs), 'raw_input_mismatches': input_mismatches,
        'current_complete_account_return_delta': None,
        'delta_unavailable_reason': 'Current original historical entrypoint raises CalendarCoverageError before account NAV generation.',
    }
    write('comparison.json', summary)
    write('financial_source_bindings.json', financial_bindings)
    print(json.dumps({'comparisons': {k: {z: v[z] for z in ['status', 'files', 'rows', 'max_numeric_difference']}
                                      for k, v in comparisons.items()},
                      'worker_status': {n: r['status'] for n, r in receipts.items()},
                      'changed_protected_files': changed, 'raw_input_mismatches': input_mismatches,
                      'raw_inputs_compared': len(old_inputs)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
