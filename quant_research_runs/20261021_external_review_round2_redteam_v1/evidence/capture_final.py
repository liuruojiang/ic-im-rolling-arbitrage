"""Final independent read-only audit, binding the unchanged 27 tests to current code."""
from pathlib import Path
from datetime import datetime, timezone
import ast
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
RUN = ROOT / 'quant_research_runs/20261021_external_review_round2_redteam_v1'
FINAL = RUN / 'evidence/final'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write(name, value):
    (FINAL / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def main():
    FINAL.mkdir(exist_ok=False)
    names = ['poe_ic_im_mainline_v1_4_bot.py', 'ic_im_sse_contract_identity_v1.py',
             'ic_im_put_execution_v1.py', 'ic_im_v1_4_policy.py', 'poe_ic_im_v1_4_state.py',
             'test_icim_round2_audit_evidence_20261021.py', 'test_ic_im_v1_4_integration_guards.py',
             (RUN / 'evidence_gate.py').relative_to(ROOT).as_posix(),
             (RUN / 'run_checks.py').relative_to(ROOT).as_posix(),
             Path(__file__).relative_to(ROOT).as_posix()]
    def sources():
        return {name: sha(ROOT / name) for name in names}
    started = datetime.now(timezone.utc).isoformat()
    before = sources()
    write('source_before.json', before)
    relative = 'test_ic_im_v1_4_integration_guards.py'
    previous = ast.parse((RUN / 'backup' / relative).read_text(encoding='utf-8'))
    current = ast.parse((ROOT / relative).read_text(encoding='utf-8'))
    changes = []
    for node in ast.walk(current):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            value = node.value
            for small, large in [('1001', '10001001'), ('1002', '10001002'), ('1003', '10001003')]:
                old, new = 'CON_OP_' + small, 'CON_OP_' + large
                if isinstance(value, bytes):
                    old, new = old.encode(), new.encode()
                if new in value:
                    changes.append(dict(line=node.lineno, replacement_from=new.decode() if isinstance(new,bytes) else new,
                                        replacement_to=old.decode() if isinstance(old,bytes) else old))
                    value = value.replace(new, old)
            node.value = value
    ast_equal = ast.dump(previous) == ast.dump(current)
    assert ast_equal and len(changes) == 6, (ast_equal, changes)
    write('integration_mock_ast.json', dict(only_security_id_input_changed=ast_equal,
          source=relative, old_backup_sha256=sha(RUN / 'backup' / relative),
          current_sha256=sha(ROOT / relative), replacements=changes,
          method='Normalize only three CON_OP security-ID constants in current AST, then require exact full AST equality to audit-start backup. Prices, times, statements and assertions remain equal.'))
    environment = dict(os.environ, ICIM_AUDIT_STAGE='evidence/final', PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
    result = subprocess.run([sys.executable, str(RUN / 'run_checks.py'), 'test_icim_round2_audit_evidence_20261021.py'],
                            cwd=ROOT, env=environment, timeout=60)
    after = sources()
    write('source_after.json', after)
    suites = ET.parse(FINAL / 'regression.xml').getroot().findall('testsuite')
    counts = {key:sum(int(s.get(key, 0)) for s in suites) for key in ('tests','failures','errors','skipped')}
    runtime = ROOT / 'runtime/ic_im_v1_4_r1/latest.json'
    latest = json.loads(runtime.read_text(encoding='utf-8'))
    receipt = dict(started_at=started, finished_at=datetime.now(timezone.utc).isoformat(),
                   process_exit_code=result.returncode, source_before_equals_after=before == after,
                   bound_files=len(before), counts=counts, extra_ast_check_passed=ast_equal,
                   runtime_observation=dict(sha256=sha(runtime), sequence=latest.get('sequence'), verified_day=latest.get('verified_day'),
                        exception='Concurrent seq18 differs from audit-start seq17; this auditor performed no runtime write or restore.'),
                   passed=result.returncode == 0 and before == after and counts == dict(tests=27,failures=0,errors=0,skipped=0) and ast_equal,
                   artifacts={p.name:sha(p) for p in sorted(FINAL.iterdir()) if p.is_file()})
    write('independent_receipt.json', receipt)
    print(json.dumps(receipt,ensure_ascii=False,indent=2))
    raise SystemExit(0 if receipt['passed'] else 1)

if __name__ == '__main__':
    main()
