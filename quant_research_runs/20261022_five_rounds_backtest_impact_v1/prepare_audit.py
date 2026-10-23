"""Read-only inventory and source bindings for the backtest-impact audit."""
from __future__ import annotations

import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
EARLY = ROOT / 'quant_research_runs/20261021_external_review_v1'
RUNS = ['20261021_external_review_v1', '20261021_external_review_round2_v1',
        '20261021_expert_repairs_v1', '20261021_expert_round3_repairs_v1',
        '20261021_round3_source_validation_v1', '20261021_round4_evidence_calendar_v1',
        '20261021_round5_recording_semantics_v1']
ENTRIES = [
    'research_ic_v13_to_v14_final_joint_gate_v1.py',
    'research_im_v14_mom_negative_put_qty_2_vs_3_v1.py',
    'build_ic_im_mainline_v1_3_r6_fixed_performance.py',
    'build_v14_full_nav_to_20260918.py',
    'render_v14_verified_curve_20260814.py',
    'quant_research_runs/20261002_fix10_vs_fix11_full_history/run_account.py',
    'quant_research_runs/20261016_put_execution_repairs_v1/run_history.py',
]


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def read(p):
    return json.loads(Path(p).read_text(encoding='utf-8-sig'))


def write(name, value):
    path = HERE / name
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    baseline = read(EARLY / 'baseline.json')
    old = baseline['backed_up']
    source = {p.name: sha(p) for p in ROOT.glob('*.py')}
    difference = {n: {'before': old.get(n), 'after': h} for n, h in source.items()
                  if h != old.get(n) and not n.startswith('test')}
    modules = {p.stem: p for p in ROOT.glob('*.py') if not p.name.startswith('test')}
    graph = {}
    for name, p in modules.items():
        tree = ast.parse(p.read_text(encoding='utf-8-sig'))
        deps = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                deps.update(n.name.split('.')[0] for n in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                deps.add(node.module.split('.')[0])
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.endswith('.py'):
                deps.add(Path(node.value).stem)
        graph[name] = sorted(deps & modules.keys())

    def closure(name):
        found, pending = set(), [name]
        while pending:
            item = pending.pop()
            if item in found:
                continue
            found.add(item)
            pending.extend(set(graph.get(item, [])) - found)
        return sorted(found)

    closures = {}
    for name in ENTRIES:
        p = ROOT / name
        if p.parent == ROOT:
            values = closure(p.stem)
            closures[name] = {'root_modules': values,
                              'changed_root_dependencies': [n for n in values if n + '.py' in difference]}
        else:
            closures[name] = {'note': 'Dynamic local imports; inspected and exercised by isolated workers; not covered by root-module graph.'}

    layers = {}
    for name in RUNS:
        folder = ROOT / 'quant_research_runs' / name / 'backup'
        layer = {}
        for p in folder.glob('*.py'):
            if p.name.startswith('test') or p.name not in source:
                continue
            before = sha(p)
            if before != source[p.name]:
                layer[p.name] = {'before_sha256': before, 'now_sha256': source[p.name]}
        layers[name] = layer

    protected_old = read(ROOT / 'quant_research_runs/20261021_expert_round3_repairs_v1/protected_before.json')
    selected = set(protected_old) | set(baseline['immutable'])
    for name in ['quant_research_runs/20261016_put_execution_repairs_v1',
                 'quant_research_runs/20261002_fix10_vs_fix11_full_history',
                 'runtime', 'docs']:
        d = ROOT / name
        selected.update(p.relative_to(ROOT).as_posix() for p in d.rglob('*') if p.is_file()
                        and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts)
    selected.update(n for n in source)
    # Include old root backup bindings and every old-data file in the input roots.
    selected.update(old)
    protected = {n: sha(ROOT / n) for n in sorted(selected) if (ROOT / n).is_file()}
    prior_mismatch = [n for n, h in protected_old.items()
                      if n not in protected or protected[n] != h]
    old_source_changes = [n for n, h in old.items() if n.endswith('.py') and n in source and source[n] != h]
    write('inventory.json', {'created_at': datetime.now(timezone.utc).isoformat(),
                           'baseline': str(EARLY / 'baseline.json'),
                           'baseline_sha256': sha(EARLY / 'baseline.json'),
                           'production_source_difference': difference,
                           'old_root_source_changes': old_source_changes,
                           'root_dependency_graph': graph, 'entry_closures': closures,
                           'per_layer_differences_to_now': layers,
                           'older_protected_objects': len(protected_old),
                           'older_protected_mismatches': prior_mismatch,
                           'source_bindings': source,
                           'entry_bindings': {n: sha(ROOT / n) for n in ENTRIES}})
    write('protected_before.json', protected)
    print(json.dumps({'protected': len(protected), 'prior_mismatches': len(prior_mismatch),
                      'changed_production_root': sorted(difference),
                      'entry_impacts': {n: x.get('changed_root_dependencies', x.get('note'))
                                        for n, x in closures.items()}}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
