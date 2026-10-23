"""Freeze all actual runtime paths as well as earlier audit objects."""
import hashlib
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PRIOR = ROOT / 'quant_research_runs/20261022_expert_followup_replay_audit_v1'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert not (HERE / 'baseline.json').exists()
    base = json.loads((PRIOR / 'baseline.json').read_text())
    paths = {ROOT / n for n in base['protected']}
    paths.update(p for p in PRIOR.rglob('*') if p.is_file() and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts)
    paths.update(p for p in (ROOT / 'runtime').rglob('*') if p.is_file())
    sources = ['ic_im_evidence_adapter_v1.py', 'ic_im_signal_evidence_v1.py',
               'poe_ic_im_mainline_v1_4_bot.py', 'poe_ic_im_v1_4_server.py', 'notes/mainline_registry.md']
    for name in sources:
        backup = HERE / 'backup' / name
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, backup)
    for name in ['run_bound.py', 'run_checks.py', 'evidence_gate.py', 'pytest.ini', 'exclusions.json']:
        shutil.copyfile(PRIOR / name, HERE / name)
    args = json.loads((PRIOR / 'final_args.json').read_text())
    test = 'test_icim_calendar_capture_review_20261022.py'
    (HERE / 'final_args.json').write_text(json.dumps(args + [test], indent=2), encoding='utf-8')
    (HERE / 'focused_args.json').write_text(json.dumps([test]), encoding='utf-8')
    spec = ROOT / 'docs/ic_im_calendar_provenance_capture_20261022_v1_spec.md'
    spec.with_suffix('.md.sha256').write_text(sha(spec) + '  ' + spec.name + '\n', encoding='utf-8')
    (HERE / 'baseline.json').write_text(json.dumps({
        'protected': {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(paths)},
        'backups': {n: sha(ROOT / n) for n in sources}, 'spec_sha256': sha(spec),
        'runtime': {p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT / 'runtime').rglob('*') if p.is_file()}
    }, indent=2), encoding='utf-8')
    print(json.dumps({'protected': len(paths), 'runtime': len(list((ROOT/'runtime').rglob('*.json')))}))


if __name__ == '__main__':
    main()
