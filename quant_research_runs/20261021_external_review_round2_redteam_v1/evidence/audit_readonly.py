"""Independent one-time byte verification, not a permanent equality regression."""
from pathlib import Path
from datetime import datetime, timezone
import ast
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OLD = ROOT / 'quant_research_runs/20261021_external_review_round2_v1'

def read(p):
    return json.loads(p.read_text(encoding='utf-8'))

def sha(p):
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def bad(items, base=ROOT):
    return [p for p, h in items.items() if not (base / p).is_file() or sha(base / p) != h]

def main():
    destination = HERE / sys.argv[1]
    if destination.exists():
        raise SystemExit('Refusing to replace independent verification')
    receipt = read(OLD / 'final/receipt.json')
    before, after = [read(OLD / 'final' / ('source_' + stage + '.json')) for stage in ('before', 'after')]
    baseline = read(OLD / 'baseline.json')
    original = read(ROOT / 'quant_research_runs/20261021_external_review_v1/baseline.json')
    previous_sources = read(ROOT / 'quant_research_runs/20261021_external_review_redteam_v1/final/source_after.json')
    previous_backed = {p:h for p,h in previous_sources.items() if p in baseline['backed_up']}
    outputs = read(ROOT / 'quant_research_runs/20261021_external_review_redteam_v1/outputs_baseline.json')
    paths = {p.relative_to(ROOT).as_posix() for p in (ROOT / 'outputs').rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    checks = dict(historical_source_before_equals_after=before == after,
                  historical_source_files=len(after), current_old_bound_source_mismatches=bad(after),
                  historical_artifact_mismatches=bad(receipt['artifacts'], OLD / 'final'),
                  backed_up_file_mismatches=bad(baseline['backed_up'], OLD / 'backup'),
                  prior_evidence_mismatches=bad(baseline['prior_evidence']),
                  selected_original_immutable_mismatches=bad(original['immutable']),
                  selected_original_immutable_count=len(original['immutable']),
                  previous_source_receipt_backup_mismatches=bad(previous_backed, OLD / 'backup'),
                  previous_source_receipt_backed_up_entries=len(previous_backed),
                  runtime_mismatches=bad({p:h for p,h in baseline['backed_up'].items() if p.startswith('runtime/')}),
                  outputs_mismatches=bad({p:v['sha256'] for p,v in outputs['files'].items()}),
                  outputs_path_delta=sorted(paths.symmetric_difference(outputs['files'])),
                  outputs_count=len(outputs['files']),outputs_scope=outputs['scope'])
    bound = [ROOT / 'test_icim_round2_audit_evidence_20261021.py', Path(__file__),
             ROOT / 'poe_ic_im_mainline_v1_4_bot.py', ROOT / 'ic_im_sse_contract_identity_v1.py',
             HERE.parent / 'run_checks.py']
    for name in ('evidence_gate.py', 'final_acceptance.py', 'run_all.py'):
        if (HERE.parent / name).is_file():
            bound.append(HERE.parent / name)
    result = dict(verified_at=datetime.now(timezone.utc).isoformat(), checks=checks,
                  audit_binding={p.relative_to(ROOT).as_posix():sha(p) for p in bound},
                  boundaries=['Current local verification only; no remote deployment was attempted',
                              'Outputs baseline is prospective and cannot reconstruct unrecorded history',
                              'Current old-bound source mismatches may be legitimate subsequent repairs; listed without automatic PASS/FAIL'])
    destination.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__ == '__main__':
    main()
