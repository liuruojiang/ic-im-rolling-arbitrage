"""Verify immutable evidence, exact source/test binding, backups and official fixture bytes."""
from pathlib import Path
from datetime import datetime, timezone
import difflib
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
def read(path):
    return json.loads(path.read_text(encoding='utf-8'))
def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()
def bad(entries, base=ROOT):
    return [p for p, expected in entries.items() if not (base / p).is_file() or sha(base / p) != expected]

baseline = read(HERE / 'baseline.json')
old = read(ROOT / 'quant_research_runs/20261021_external_review_v1/baseline.json')
outputs = read(ROOT / 'quant_research_runs/20261021_external_review_redteam_v1/outputs_baseline.json')
previous_sources = read(ROOT / 'quant_research_runs/20261021_external_review_redteam_v1/final/source_after.json')
previous_backed = {p: h for p, h in previous_sources.items() if p in baseline['backed_up']}
bound = read(HERE / 'final/source_after.json')
receipt = read(HERE / 'final/receipt.json')
changed = bad(baseline['backed_up'])
allowed = {'poe_ic_im_mainline_v1_4_bot.py', 'test_ic_im_external_review_display_cache_20261021.py',
           'notes/mainline_registry.md'}
checks = dict(backup_mismatches=bad(baseline['backed_up'], HERE / 'backup'),
              prior_evidence_mismatches=bad(baseline['prior_evidence']),
              selected_immutable_mismatches=bad(old['immutable']),
              previous_source_receipt_backup_mismatches=bad(previous_backed, HERE / 'backup'),
              cumulative_bound_source_mismatches=bad(bound),
              cumulative_artifact_mismatches=bad(receipt['artifacts'], HERE / 'final'),
              prospective_outputs_mismatches=bad({p: v['sha256'] for p, v in outputs['files'].items()}),
              unexpected_changes=sorted(set(changed) - allowed))
actual = {p.relative_to(ROOT).as_posix() for p in (ROOT / 'outputs').rglob('*')
          if p.is_file() and '__pycache__' not in p.parts}
checks['prospective_output_path_delta'] = sorted(actual.symmetric_difference(outputs['files']))
fixture = ROOT / 'tests/fixtures/icim_external_review_round2_20261021'
fixture_hashes = read(fixture / 'manifest.json')
checks['fixture_mismatches'] = bad(fixture_hashes, fixture)
checks['fixture_raw_mismatches'] = bad(fixture_hashes, HERE / 'raw')
spec = ROOT / 'docs/ic_im_external_review_round2_20261021_v1_spec.md'
checks['spec_mismatches'] = [] if sha(spec) == spec.with_suffix('.md.sha256').read_text().split()[0] else [spec.name]
compiled = ['poe_ic_im_mainline_v1_4_bot.py', 'ic_im_sse_contract_identity_v1.py',
            'test_ic_im_external_review_round2_20261021.py', 'test_ic_im_external_review_display_cache_20261021.py']
for relative in compiled:
    compile((ROOT / relative).read_bytes(), relative, 'exec')
check = subprocess.run(['git', '-c', 'safe.directory=' + ROOT.as_posix(), 'diff', '--check'],
                       cwd=ROOT, text=True, capture_output=True)
diff = []
for relative in sorted(changed):
    diff += difflib.unified_diff((HERE / 'backup' / relative).read_text(encoding='utf-8').splitlines(True),
                                (ROOT / relative).read_text(encoding='utf-8').splitlines(True),
                                fromfile='before/' + relative, tofile='after/' + relative)
added = ['ic_im_sse_contract_identity_v1.py', 'test_ic_im_external_review_round2_20261021.py']
for relative in added:
    diff += difflib.unified_diff([], (ROOT / relative).read_text(encoding='utf-8').splitlines(True),
                                fromfile='/dev/null', tofile='after/' + relative)
(HERE / 'source_delta.patch').write_bytes(''.join(diff).encode('utf-8'))
result = dict(verified_at=datetime.now(timezone.utc).isoformat(), passed=not any(checks.values()) and check.returncode == 0 and receipt['exit_code'] == 0,
              checks=checks, changed_baseline_files=changed, new_runtime_and_test_files=added,
              backups=len(baseline['backed_up']), protected_prior_evidence=len(baseline['prior_evidence']),
              original_selected_immutable=len(old['immutable']), prospective_outputs=len(outputs['files']),
              prospective_outputs_bytes=sum(v['size'] for v in outputs['files'].values()),
              prospective_scope=outputs['scope'],
              previous_source_receipt_backed_up_entries=len(previous_backed),
              cumulative=receipt['counts'], source_before_equals_after=receipt['source_before_equals_after'],
              compiled_files=compiled, git_diff_check_exit=check.returncode,
              git_diff_check_output=check.stdout + check.stderr,
              source_delta_sha256=sha(HERE / 'source_delta.patch'),
              final_sha256={p: sha(ROOT / p) for p in added + sorted(changed)})
target = HERE / 'final_verification.json'
if target.exists():
    raise SystemExit('Refusing to overwrite final verification')
target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if result['passed'] else 1)
