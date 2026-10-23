"""Freeze original sources, evidence and data before historical-calendar repair."""
from __future__ import annotations

import ast
import hashlib
import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PRIOR = ROOT / 'quant_research_runs/20261021_round5_recording_semantics_v1'
AUDIT = ROOT / 'quant_research_runs/20261022_five_rounds_backtest_impact_v1'
FIXTURE = ROOT / 'tests/fixtures/icim_historical_calendar_20261022'


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def read(p):
    return json.loads(Path(p).read_text(encoding='utf-8-sig'))


def main():
    if (HERE / 'baseline.json').exists():
        raise FileExistsError('baseline exists')
    previous = read(PRIOR / 'accepted_01/source_after.json')
    assert all(sha(ROOT / n) == h for n, h in previous.items()), 'prior sources changed'
    sources = ['poe_ic_im_mainline_v1_4_bot.py', 'ic_im_v1_4_policy.py', 'ic_im_quarter_roll_v1_3.py',
               'ic_im_evidence_adapter_v1.py', 'ic_im_signal_evidence_v1.py', 'notes/mainline_registry.md',
               'quant_research_runs/20261016_put_execution_repairs_v1/run_history.py']
    backup = {}
    for name in sources:
        target = HERE / 'backup' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
        backup[name] = sha(target)
    protected = read(AUDIT / 'protected_before.json')
    assert all(sha(ROOT / n) == h for n, h in protected.items()), 'protected audit baseline changed'
    for folder in [AUDIT, PRIOR]:
        for p in folder.rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts:
                protected[p.relative_to(ROOT).as_posix()] = sha(p)
    raw_inputs = read(ROOT / 'quant_research_runs/20261016_put_execution_repairs_v1/independent_audit.json')['input_sha256']
    assert all(sha(ROOT / n) == h for n, h in raw_inputs.items())
    protected.update(raw_inputs)
    for name in ['run_bound.py', 'run_checks.py', 'evidence_gate.py', 'pytest.ini', 'exclusions.json']:
        shutil.copyfile(PRIOR / name, HERE / name)
    (HERE / 'prior_args.json').write_bytes((PRIOR / 'final_args.json').read_bytes())
    (HERE / 'focused_args.json').write_text(json.dumps(['test_icim_historical_calendar_20261022.py']) + '\n', encoding='utf-8')
    spec = ROOT / 'docs/ic_im_historical_calendar_repair_20261022_v1_spec.md'
    spec.with_suffix(spec.suffix + '.sha256').write_text(sha(spec) + '  ' + spec.name + '\n', encoding='utf-8')
    # Independent oracle from dated official futures rows, not the date helper.
    import pandas as pd
    path = ROOT / 'data/ic_monthly_discount_roll_v1/cffex_ic_contracts.csv'
    raw = pd.read_csv(path, parse_dates=['date'])
    subset = raw.loc[raw.date.dt.year.between(2022, 2025)]
    sessions = {str(y): sorted(subset.loc[subset.date.dt.year.eq(y), 'date'].dt.strftime('%Y-%m-%d').unique().tolist())
                for y in range(2022, 2026)}
    expiry = {}
    for year in range(2022, 2026):
        for month in range(1, 13):
            contract = f'IC{year % 100:02d}{month:02d}'
            dates = raw.loc[raw.contract.eq(contract), 'date']
            assert not dates.empty, contract
            expiry[contract] = dates.max().date().isoformat()
    archives = {}
    for p in (path.parent / 'cffex_raw').glob('*.zip'):
        if p.name[:4] in {'2022', '2023', '2024', '2025'}:
            archives[p.relative_to(ROOT).as_posix()] = sha(p)
    protected[path.relative_to(ROOT).as_posix()] = sha(path)
    protected.update(archives)
    oracle = {'representation': 'Trading dates and last observed contract dates from frozen CFFEX historical daily rows; independent of strategy calendar functions.',
              'source': path.relative_to(ROOT).as_posix(), 'source_sha256': sha(path),
              'session_dates': sessions, 'contract_last_dates': expiry,
              'official_monthly_archive_sha256': archives}
    (FIXTURE / 'cffex_sessions_and_expiries.json').write_text(json.dumps(oracle, indent=2) + '\n', encoding='utf-8')
    # Save every original 2026 session result as a regression boundary.
    tree = ast.parse((ROOT / 'poe_ic_im_mainline_v1_4_bot.py').read_text(encoding='utf-8-sig'))
    literal = next(n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(x, ast.Name) and x.id == '_EXCHANGE_CLOSURES' for x in n.targets))
    env = {'date': date, 'frozenset': frozenset, 'range': range}
    exec(compile(ast.Module(body=[literal], type_ignores=[]), '<frozen-calendar-literal>', 'exec'), env)
    dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(365)]
    original_2026 = [str(d) for d in dates if d.weekday() < 5 and d not in env['_EXCHANGE_CLOSURES']]
    (FIXTURE / 'original_2026_sessions.json').write_text(json.dumps(original_2026, indent=2) + '\n', encoding='utf-8')
    (HERE / 'baseline.json').write_text(json.dumps({'created_at': datetime.now(timezone.utc).isoformat(),
        'prior_bindings': previous, 'backup': backup, 'protected': protected,
        'raw_inputs': raw_inputs, 'calendar_fixture_sha256': {p.name: sha(p) for p in FIXTURE.glob('*.json')},
        'spec_sha256': sha(spec)}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'backups': len(backup), 'protected': len(protected),
                      'session_counts': {y: len(x) for y, x in sessions.items()}, 'expiry_oracles': len(expiry),
                      'raw_ic_archives': len(archives)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
