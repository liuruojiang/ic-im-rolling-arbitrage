"""Machine-bind A–H history and summarize sealed attempts without hiding failures."""
import ast
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
RUN = Path(__file__).resolve().parent.parent
D = RUN / 'delivery'
Q = ROOT / 'quant_research_runs'
sys.path.insert(0, str(ROOT))


def read(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def functions(path):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    result = {n.name: ast.dump(n, include_attributes=False) for n in tree.body if isinstance(n, ast.FunctionDef)}
    for cls in tree.body:
        if isinstance(cls, ast.ClassDef):
            result.update({cls.name + '.' + n.name: ast.dump(n, include_attributes=False)
                           for n in cls.body if isinstance(n, ast.FunctionDef)})
    return result


bot_name = 'poe_ic_im_mainline_v1_4_bot.py'
pre = Q / '20261022_free_source_integration_v1/backup' / bot_name
free = Q / '20261022_evidence_conflict_review_v1/backup' / bot_name
received = RUN / 'backup' / bot_name
free_bind = read(Q / '20261022_free_source_integration_v1/accepted_02/source_after.json')
h_bind = read(Q / '20261022_evidence_conflict_review_v1/accepted_03/source_after.json')
date_bind = read(Q / '20261022_evidence_http_date_v1/accepted_02/source_after.json')
assert sha(free) == free_bind[bot_name]
assert sha(received) == h_bind[bot_name] == date_bind[bot_name]
before, after, delivered, now = map(functions, [pre, free, received, ROOT / bot_name])
groups = {
 'A': ['fetch_510500_quote_with_failover', 'fetch_tencent_510500_quote', 'fetch_sina_510500_quote'],
 'B': ['fetch_sina_510500_expiries', 'fetch_510500_expiries_with_failover', 'select_ic_put_for_reset'],
 'C': ['_sina_put_detail_batches'], 'D': ['_prefetch_mo_replay_verification'],
 'E': ['_fetch_510500_chain_for_day'],
 'F': ['_validate_chain_stamp_matches', '_require_selected_510500_quote_time'],
 'G': ['_evidence_http_get'], 'H': ['build_live_trade_signal']}
rows = []
for item, names in groups.items():
    owner = '20261022_free_source_integration_v1' if item != 'H' else '20261022_evidence_conflict_review_v1'
    row = {'item': item, 'origin_round': owner, 'source_at_origin': str(free if item != 'H' else received),
           'source_sha256': sha(free if item != 'H' else received), 'functions': []}
    for name in names:
        row['functions'].append({'name': name, 'present_before_free_source': name in before,
            'changed_in_free_source': before.get(name) != after.get(name),
            'free_source_equals_received': after.get(name) == delivered.get(name),
            'received_equals_this_round': delivered.get(name) == now.get(name)})
        if item != 'H':
            assert after.get(name) == delivered.get(name)
    rows.append(row)
assert before['collection_clock'] == after['collection_clock'] == delivered['collection_clock']
assert before['_iv_observation_clock'] == after['_iv_observation_clock'] == delivered['_iv_observation_clock']
assert before['build_live_trade_signal'] != after['build_live_trade_signal'] != delivered['build_live_trade_signal']
prov = {'status': 'PASS', 'snapshots': [{'path': str(p), 'sha256': sha(p)} for p in [pre, free, received]],
        'changes': rows, 'F_note': 'clock helpers pre-existed; public builder wrapping and chain/row clock consumers changed in free-source round',
        'previous_acceptance_counts': {'free_source': 2918, 'conflict': 2941, 'HTTP_Date': 2959},
        'current_production_changes': ['query/capture ic.chain cache revalidation',
            'exhausted replay retains EvidenceUnavailable', 'two in-flight batches and shutdown deadline check'],
        'new_round_does_not_claim_old_features_as_new': True}
(D / 'A_H_provenance.json').write_text(json.dumps(prov, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

# Summary uses only receipts present at the cut. No prediction of future days.
days = defaultdict(lambda: {'cycles': 0, 'statuses': Counter(), 'products': defaultdict(lambda: {
    'attempts': 0, 'complete': 0, 'incomplete': 0, 'timeout_attempts': 0,
    'proxy_attempts': 0, 'incomplete_hours': Counter(), 'timeout_hours': Counter()})})
errors = []
for path in sorted((ROOT / 'data/icim_v14_original_inputs/capture_receipts').rglob('*.json')):
    try:
        receipt = read(path)
        day = receipt.get('capture', {}).get('day') or receipt['started_at'][:10]
        row = days[day]
        row['cycles'] += 1
        row['statuses'][receipt['status']] += 1
        if receipt['status'] not in {'captured', 'incomplete'}:
            continue  # polling/waiting is not a new sealed product attempt.
        for product, result in receipt.get('capture', {}).get('products', {}).items():
            if not result.get('manifest'):
                continue
            body = read(Path(result['manifest']))['body']
            assert hashlib.sha256(__import__('ic_im_signal_evidence_v1')._dumps(body)).hexdigest() == read(Path(result['manifest']))['sha256']
            stats = row['products'][product]
            stats['attempts'] += 1
            complete = body.get('complete') is True
            stats['complete' if complete else 'incomplete'] += 1
            # Receipt reason can be abbreviated. Inspect all recorded failures too.
            text = json.dumps(body.get('failures', []), ensure_ascii=False) + result.get('reason', '')
            timeout = any(word in text.lower() for word in ['timeout', 'timed out', '超过总时间预算'])
            proxy = 'proxyerror' in text.lower() or 'cannot connect to proxy' in text.lower()
            stats['timeout_attempts'] += int(timeout)
            stats['proxy_attempts'] += int(proxy)
            hour = receipt['started_at'][11:13]
            if not complete:
                stats['incomplete_hours'][hour] += 1
            if timeout:
                stats['timeout_hours'][hour] += 1
    except Exception as exc:
        errors.append({'receipt': path.name, 'error_type': type(exc).__name__, 'error': str(exc)[:250]})
for day in days.values():
    for p in day['products'].values():
        p['timeout_frequency'] = p['timeout_attempts'] / p['attempts'] if p['attempts'] else None
stats = {'observed_at': datetime.now().astimezone().isoformat(), 'days': days, 'unreadable_receipts': errors,
    'denominator': 'each sealed product capture attempt linked by a completed cycle receipt; polling/waiting excluded',
    'limitations': ['one closed market day so far', 'status/failure text classifies timeout attempts, not independent HTTP request rates',
                   'observed sample mixes earlier deployment versions; no causal estimate of batch-size effect',
                   'receiptless crash/unsealed attempts are outside this denominator']}
(D / 'timeout_frequency.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'provenance': 'PASS', 'days': list(days), 'read_errors': len(errors),
                  'products': {d: dict(s['products']) for d, s in days.items()}}, ensure_ascii=False))
