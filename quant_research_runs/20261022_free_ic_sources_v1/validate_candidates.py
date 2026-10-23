"""Validate real response fixtures; explicitly not a full original-signal acceptance."""
from datetime import date, datetime
import json
from pathlib import Path
import sys

import candidate_sources as candidate
import survey

DAY = date(2026, 10, 22)


def inputs():
    here = survey.HERE
    master = (here / 'probe_02/raw/sse_master_https.bin').read_bytes()
    listing = (here / 'probe_01/raw/sina_december_put_ids.bin').read_bytes()
    stages = ['probe_04', 'probe_04', 'probe_03', 'probe_03', 'probe_03']
    batches = []
    selected = []
    for i, stage in enumerate(stages):
        stem = f'sina_literal_batch_{i}'
        row = json.loads((here / stage / (stem + '.json')).read_text(encoding='utf-8'))
        assert row['http_status'] == 200
        ids = [v.removeprefix('CON_OP_') for v in row['url'].split('list=')[1].split(',')]
        batches.append((ids, (here / stage / 'raw' / (stem + '.bin')).read_bytes()))
        selected.append(row)
    return master, listing, batches, selected


def main():
    clock = datetime.now(candidate.BEIJING)
    master, listing, batches, selected = inputs()
    rows = candidate.option_chain(master, listing, batches, DAY, clock)
    root = survey.ROOT
    sys.path.insert(0, str(root))
    import pandas as pd
    import poe_ic_im_mainline_v1_4_bot as bot
    etfs = [candidate.tencent_etf((survey.HERE / 'probe_02/raw/tencent_etf.bin').read_bytes(), DAY, clock),
            candidate.sina_etf((survey.HERE / 'probe_02/raw/sina_etf.bin').read_bytes(), DAY, clock)]
    assert etfs[0]['last'] == etfs[1]['last']
    assert etfs[0]['previous_close'] == etfs[1]['previous_close']
    assert etfs[0]['open'] == etfs[1]['open']
    core = next(r for r in rows if r['security_id'] == '10012149')
    assert core['contract'] == '510500P2612M06500'
    bot._require_selected_510500_quote_time(pd.Series(core), clock, DAY)
    source_freshness = []
    for quote in etfs:
        stamp = quote['source_stamp']
        bot._validate_chain_stamp_matches(quote['source'], {'date': DAY.strftime('%Y%m%d'),
            'time': stamp.strftime('%H%M%S')}, DAY, clock)
        try:
            bot._validate_iv_monitor_times(DAY, core['source_stamp'], stamp, clock, True)
        except ValueError as exc:
            source_freshness.append({'source': quote['source'], 'iv_monitor': 'REJECTED', 'reason': str(exc)})
        else:
            source_freshness.append({'source': quote['source'], 'iv_monitor': 'PASS'})
    sse = json.loads((survey.HERE / 'probe_02/raw/sse_chain_primary.bin').read_bytes())
    sse_map = {r[5]: r for r in sse['list']}
    differences = []
    for row in rows:
        other = sse_map[row['security_id']]
        if (other[0] != row['contract'] or candidate.number(other[1]) != row['last']
                or candidate.number(other[4]) != row['strike']):
            differences.append(row['security_id'])
    greek = candidate.variables((survey.HERE / 'probe_01/raw/sina_core_identity.bin').read_bytes(), 'hq_str_CON_SO_')
    assert greek['10012149'][12] == core['contract']
    # Actual saved inputs, read again without making any HTTP request.
    repeat_master, repeat_listing, repeat_batches, _ = inputs()
    repeated = candidate.option_chain(repeat_master, repeat_listing, repeat_batches, DAY, clock)
    assert rows == repeated
    before = json.loads((survey.HERE / 'probe_01/baseline.json').read_text(encoding='utf-8'))
    after = {p.relative_to(root).as_posix(): survey.sha(p) for p in (root / 'runtime').rglob('*') if p.is_file()}
    assert before['runtime'] == after
    assert all(survey.sha(root / name) == digest for name, digest in before['sources'].items())
    survey.save(survey.HERE / 'validated_candidates.json', {
        'status': 'PARTIAL_DATA_GAP_RESOLVED_RESEARCH_ONLY', 'day': str(DAY), 'validated_at': clock.isoformat(),
        'full_original_ic_signal_acceptance': False,
        'reason_not_full_acceptance': 'Component fixtures do not include all valuation, fear-grid, futures and opening evidence; no original eligible ledger result was generated here.',
        'listed_and_quoted_ids': len(rows), 'standard_M_count': sum(not r['nonstandard_contract_blocked'] for r in rows),
        'adjusted_A_count_blocked': sum(r['nonstandard_contract_blocked'] for r in rows),
        'standard_close_freshness_eligible_count': sum(r['research_selected_quote_eligible'] for r in rows),
        'pre_0915_option_timestamps': [r['security_id'] for r in rows if r['source_stamp'].hour < 9],
        'etf_crosscheck_equal': True, 'etfs': etfs, 'core': core,
        'core_full_code_crosscheck': True, 'core_production_selected_quote_guard': 'PASS',
        'etf_production_day_stamp_guard': 'PASS', 'iv_monitor_pair': source_freshness,
        'sse_current_chain_contract_price_strike_differences': differences,
        'all_saved_chain_fields_equal_on_offline_reparse': True,
        'offline_reparse_http_requests': 0, 'batch_original_requests': selected,
        'master_vintages': sorted({str(r['master_asof']) for r in rows}),
        'master_usage': 'Prior-close terms cross-check only; prices excluded; not a certified current dated master.',
        'formal_runtime_unchanged': True, 'production_sources_unchanged': True,
        'all_chain_rows': rows,
    })
    print(json.dumps({'chain': len(rows), 'sse_differences': differences,
                      'core_guard': 'PASS', 'iv_monitor': source_freshness}, ensure_ascii=False))


if __name__ == '__main__':
    # Preserve exact decimals, date objects and aware stamps as strings in the report.
    original_save = survey.save
    def report_save(path, value):
        original_save(path, json.loads(json.dumps(value, default=str)))
    survey.save = report_save
    main()
