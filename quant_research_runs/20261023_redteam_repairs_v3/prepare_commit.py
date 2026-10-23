"""Prepare an exact, hash-checked local commit; never push or change the index."""
from pathlib import Path
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE = '58986fda2c17df93eb3925bf09a1cebd55a7869e'


def main():
    script = r'''$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$head = (& git -C $repo rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $head -ne '58986fda2c17df93eb3925bf09a1cebd55a7869e') { throw 'HEAD changed; review before committing.' }
& git -C $repo diff --cached --quiet
if ($LASTEXITCODE -ne 0) { throw 'Index already contains staged changes; review them first.' }
$expected = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'commit_hashes.json') -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($entry in $expected.PSObject.Properties) {
    $file = Join-Path $repo $entry.Name
    $actual = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $entry.Value) { throw ('Audited file changed: ' + $entry.Name) }
}
$pathspec = Join-Path $PSScriptRoot 'commit_paths.nul'
& git -C $repo add "--pathspec-from-file=$pathspec" --pathspec-file-nul
if ($LASTEXITCODE -ne 0) { throw 'git add failed; nothing committed.' }
& 'C:\Python314\python.exe' -B -X utf8 (Join-Path $PSScriptRoot 'verify_staged.py')
if ($LASTEXITCODE -ne 0) { throw 'Staged bytes do not match audited files; nothing committed.' }
& git -C $repo commit -m 'fix: close IC IM v1.4 red-team gaps with source-bound adversarial evidence'
if ($LASTEXITCODE -ne 0) { throw 'git commit failed.' }
& git -C $repo log -1 --format=fuller
'''
    (HERE / 'commit_repairs.ps1').write_text(script, encoding='utf-8-sig')
    verifier = '''from pathlib import Path
import hashlib,json,subprocess
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
expected=json.loads((HERE/'commit_hashes.json').read_text('utf-8'))
paths=(HERE/'commit_paths.nul').read_bytes().decode('utf-8').strip('\\0').split('\\0')
staged=subprocess.check_output(['git','diff','--cached','--name-only','-z'],cwd=ROOT).decode('utf-8').strip('\\0').split('\\0')
assert set(staged)==set(paths), 'unexpected staged files'
for name,digest in expected.items():
    content=subprocess.check_output(['git','cat-file','blob',':'+name],cwd=ROOT)
    assert hashlib.sha256(content).hexdigest()==digest, 'staged bytes changed: '+name
for name in paths:
    if name not in expected:
        content=subprocess.check_output(['git','cat-file','blob',':'+name],cwd=ROOT)
        assert content==(ROOT/name).read_bytes(), 'commit metadata differs: '+name
print('Staged paths and bytes verified:',len(paths))
'''
    (HERE / 'verify_staged.py').write_text(verifier, encoding='utf-8')
    names = set(subprocess.check_output(['git', 'diff', '--name-only', '-z'], cwd=ROOT).decode('utf-8').strip('\0').split('\0'))
    names.discard('')
    for pattern in ['test_icim_redteam_*_repairs_20261023.py', 'docs/ic_im_redteam_repairs_20261023_v3*', 'docs/ic_im_daily_release_20261023_repairs_v3*']:
        names.update(p.relative_to(ROOT).as_posix() for p in ROOT.glob(pattern) if p.is_file())
    for folder in [HERE, ROOT / 'quant_research_runs/20261023_latest_redteam_v2', ROOT / 'tests/fixtures/icim_verified_ledger_20261021']:
        names.update(p.relative_to(ROOT).as_posix() for p in folder.rglob('*') if p.is_file()
                     and p.name not in {'collector.stdout.log', 'collector.stderr.log'}
                     and not {'isolated_temp', '__pycache__', 'backup', '.pytest_cache'}.intersection(p.relative_to(folder).parts))
    metadata = { (HERE / n).relative_to(ROOT).as_posix() for n in ['commit_hashes.json', 'commit_paths.nul', 'commit_preparation.json'] }
    names -= metadata
    values = {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in sorted(names)}
    (HERE / 'commit_hashes.json').write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding='utf-8')
    names |= metadata
    (HERE / 'commit_paths.nul').write_bytes(b'\0'.join(n.encode('utf-8') for n in sorted(names)) + b'\0')
    (HERE / 'commit_preparation.json').write_text(json.dumps({'baseline_commit': BASE, 'paths': len(names), 'hashed_files': len(values),
               'index_changed': False, 'commit_created': False, 'push_requested': False}, indent=2), encoding='utf-8')
    print(json.dumps({'paths': len(names), 'hashed_files': len(values), 'script': str(HERE / 'commit_repairs.ps1')}))


if __name__ == '__main__':
    main()
