"""Differential selector checks on saved historical quotes; no calendar bypass."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
EARLY = ROOT / 'quant_research_runs/20261021_external_review_v1/backup'
SNAPSHOTS = {
    'before': EARLY,
    'expert_initial': ROOT / 'quant_research_runs/20261021_expert_repairs_v1/backup',
    'expert_round3': ROOT / 'quant_research_runs/20261021_expert_round3_repairs_v1/backup',
    'source_validation': ROOT / 'quant_research_runs/20261021_round3_source_validation_v1/backup',
    'round4': ROOT / 'quant_research_runs/20261021_round4_evidence_calendar_v1/backup',
    'round5': ROOT / 'quant_research_runs/20261021_round5_recording_semantics_v1/backup',
}
SOURCE = {
    'IC': ROOT / 'data/ic_monthly_discount_roll_v1/cffex_ic_contracts.csv',
    'IM': ROOT / 'data/im_roll_momentum_blend_v1/upstream_im_roll_data/cffex_im_contracts.csv',
}
NATIVE = ROOT / 'outputs/re_certification/icim_v14_fix4_recert_20260924/l5_native_signal_reconstruction_20260925'


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('version', choices=[*SNAPSHOTS, 'after'])
    args = parser.parse_args()
    out = HERE / f'selectors_{args.version}.json'
    if out.exists():
        raise FileExistsError(out)
    sys.dont_write_bytecode = True
    sys.path[:0] = ([str(SNAPSHOTS[args.version])] if args.version in SNAPSHOTS else []) + [str(ROOT), str(NATIVE)]
    import pandas as pd
    import requests
    blocked = []
    def block(self, method, url, *a, **kw):
        blocked.append(str(url))
        raise RuntimeError('AUDIT_OFFLINE_NETWORK_BLOCKED: ' + str(url))
    requests.sessions.Session.request = block
    import poe_ic_im_mainline_v1_4_bot as bot
    from native_fix4_historical_sources_v2 import DatedSources
    results = {'version': args.version, 'bot_sha256': sha(bot.__file__), 'bot_path': bot.__file__,
               'source_hashes': {p: sha(v) for p, v in SOURCE.items()},
               'official_calendar_years': sorted(bot._OFFICIAL_EXCHANGE_CALENDAR_YEARS),
               'futures': {}, 'IM_first_day_put': {}, 'network_requests': blocked}
    for product in ('IC', 'IM'):
        risk = ROOT / 'quant_research_runs/20261016_put_execution_repairs_v1/inputs' / product.lower() / f'native_fix4_{product.lower()}_risk_signals_v1.csv.gz'
        days = pd.read_csv(risk).signal_date.astype(str).tolist()
        quotes = pd.read_csv(SOURCE[product], parse_dates=['date'])
        by_date = dict(tuple(quotes.groupby('date', sort=False)))
        observations = []
        for text in days:
            day = date.fromisoformat(text)
            frame = by_date[pd.Timestamp(day)].rename(columns={'contract': 'instrument', 'close': 'lastprice'})
            item = {'day': text}
            try:
                selected = bot.select_active_future(product, frame, day)
                item.update(status='selected', contract=str(selected.instrument), expiry=str(selected.expiry))
            except Exception as exc:
                item.update(status='blocked', error_type=type(exc).__name__, reason=str(exc))
            observations.append(item)
        results['futures'][product] = {'sessions': len(days),
                                      'selected': sum(x['status'] == 'selected' for x in observations),
                                      'blocked': sum(x['status'] == 'blocked' for x in observations),
                                      'risk_sha256': sha(risk), 'observations': observations}
    day = date(2022, 7, 22)
    try:
        quotes = DatedSources().im_mo_on(day)
        spot_path = ROOT / 'data/im_mo_csi1000_put_protection_battery_v6/sina_sh000852_index.csv'
        spot = pd.read_csv(spot_path).set_index('date').loc[str(day), 'close']
        original = bot.im_put_policy.EFFECTIVE_DATE
        try:
            bot.im_put_policy.EFFECTIVE_DATE = date(1900, 1, 1)
            selected = bot.select_independent_im_put_for_reset(quotes, day, float(spot))
        finally:
            bot.im_put_policy.EFFECTIVE_DATE = original
        results['IM_first_day_put'] = {'day': str(day), 'status': 'selected', 'contract': str(selected.instrument)}
    except Exception as exc:
        results['IM_first_day_put'] = {'day': str(day), 'status': 'blocked', 'error_type': type(exc).__name__, 'reason': str(exc)}
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'version': args.version,
                      'futures': {p: {k: v for k, v in d.items() if k in ['sessions', 'selected', 'blocked']}
                                  for p, d in results['futures'].items()},
                      'first_day_put': results['IM_first_day_put'],
                      'network_requests': len(blocked)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
