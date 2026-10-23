"""Freeze the last accepted delivery and configure an isolated follow-up."""
import hashlib
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PRIOR = ROOT / 'quant_research_runs/20261022_historical_calendar_repair_v1'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    target = HERE / 'baseline.json'
    assert not target.exists()
    old = json.loads((PRIOR / 'baseline.json').read_text())['protected']
    protection = {n: sha(ROOT / n) for n in old}
    for p in PRIOR.rglob('*'):
        if p.is_file() and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts:
            protection[p.relative_to(ROOT).as_posix()] = sha(p)
    sources = ['poe_ic_im_mainline_v1_4_bot.py', 'ic_im_signal_evidence_v1.py',
               'ic_im_evidence_adapter_v1.py', 'poe_ic_im_v1_4_server.py', 'notes/mainline_registry.md']
    for name in sources:
        p = HERE / 'backup' / name
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, p)
    for name in ['run_bound.py', 'run_checks.py', 'evidence_gate.py', 'pytest.ini', 'exclusions.json']:
        shutil.copyfile(PRIOR / name, HERE / name)
    runner = HERE / 'run_bound.py'
    text = runner.read_text()
    text = text.replace('HERE / "history"', '(ROOT / "quant_research_runs/20261022_historical_calendar_repair_v1/history")')
    text = text.replace('HERE / "rebuilt_02"', '(ROOT / "quant_research_runs/20261022_historical_calendar_repair_v1/rebuilt_02")')
    runner.write_text(text, encoding='utf-8')
    test = 'test_icim_expert_followup_replay_20261022.py'
    args = json.loads((PRIOR / 'final_args.json').read_text())
    (HERE / 'final_args.json').write_text(json.dumps(args + [test], indent=2), encoding='utf-8')
    (HERE / 'focused_args.json').write_text(json.dumps([test]), encoding='utf-8')
    spec = ROOT / 'docs/ic_im_expert_followup_replay_20261022_v1_spec.md'
    spec.with_suffix('.md.sha256').write_text(sha(spec) + '  ' + spec.name + '\n', encoding='utf-8')
    target.write_text(json.dumps({'protected': protection, 'backups': {n: sha(ROOT/n) for n in sources},
                                 'spec_sha256': sha(spec)}, indent=2), encoding='utf-8')
    print(json.dumps({'protected': len(protection), 'backups': len(sources)}))


if __name__ == '__main__':
    main()
