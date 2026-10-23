"""Strict saved-signal comparison; it does not certify a real evidence bundle.

Only the root signal_generated_at timestamp is excepted. Provenance, reasons,
action/quantity/price, data dates and any additional fields remain compared.
"""
import argparse
import json
from pathlib import Path


def compare_signal_values(original, replayed):
    differences = []
    def visit(a, b, path):
        if path == '$.signal_generated_at':
            return
        if type(a) is not type(b):
            differences.append({'path': path, 'original': a, 'replayed': b, 'reason': 'type_or_missing'})
        elif isinstance(a, dict):
            for key in sorted(a.keys() | b.keys()):
                if key not in a or key not in b:
                    differences.append({'path': path + '.' + key, 'reason': 'field_missing',
                                        'original_present': key in a, 'replayed_present': key in b})
                else:
                    visit(a[key], b[key], path + '.' + key)
        elif isinstance(a, list):
            if len(a) != len(b):
                differences.append({'path': path, 'reason': 'list_length', 'original': len(a), 'replayed': len(b)})
            else:
                for i, (x, y) in enumerate(zip(a, b)):
                    visit(x, y, f'{path}[{i}]')
        elif a != b:
            differences.append({'path': path, 'original': a, 'replayed': b, 'reason': 'value'})
    visit(original, replayed, '$')
    return differences


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--replayed', type=Path, required=True)
    args = parser.parse_args()
    def load(path):
        def reject(value):
            raise ValueError('nonstandard JSON number: ' + value)
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('duplicate JSON key: ' + key)
                result[key] = value
            return result
        value = json.loads(path.read_text(encoding='utf-8-sig'), parse_constant=reject, object_pairs_hook=unique)
        return value['signals']['IC'] if isinstance(value, dict) and 'signals' in value else value
    diffs = compare_signal_values(load(args.original), load(args.replayed))
    print(json.dumps({'equal_except_generated_at': not diffs, 'differences': diffs,
                      'real_bundle_and_http_requirements': 'Must be independently verified; not certified by this value comparator.'}, ensure_ascii=False, indent=2))
    raise SystemExit(bool(diffs))


if __name__ == '__main__':
    main()
