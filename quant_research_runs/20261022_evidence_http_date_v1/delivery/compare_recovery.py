"""Compare every recovered fact against the prior accepted real-package run."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[3]
RUN = Path(__file__).resolve().parent.parent
OLD = ROOT / 'quant_research_runs/20261022_evidence_conflict_review_v1/real_recovery_02'
NEW = RUN / 'real_recovery_01'
EXCLUDED = {'fetch_time', 'signal_calculation_started_at', 'signal_generated_at'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def strict(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


old, new = read(OLD / 'receipt.json'), read(NEW / 'receipt.json')
assert all(old['selected_packages'][p]['sha256'] == new['selected_packages'][p]['sha256'] for p in ['IC', 'IM'])
report = {'status': 'PASS', 'excluded_calculation_clocks': sorted(EXCLUDED), 'strict_json_types': True,
          'original_manifests_identical': True, 'cases': [], 'differences': []}
reference = read(OLD / 'public_explicit_replay/recovered_record.json')
for name in ['public_explicit_replay', 'automatic_recovery']:
    path = NEW / name / 'recovered_record.json'
    observed = read(path)
    case = {'name': name, 'compared_signal_fields': {}, 'complete_product_states_equal':
            strict(reference['products']) == strict(observed['products']),
            'recovered_record_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    assert case['complete_product_states_equal']
    for product in ['IC', 'IM']:
        a, b = reference['signals'][product], observed['signals'][product]
        assert set(a) == set(b)
        case['compared_signal_fields'][product] = len(a)
        for key in a:
            if key not in EXCLUDED and strict(a[key]) != strict(b[key]):
                report['differences'].append({'case': name, 'product': product, 'field': key})
    report['cases'].append(case)
assert not report['differences'], report['differences']
(RUN / 'delivery/recovery_facts_comparison.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))
