from __future__ import annotations

import hashlib, json, math, subprocess
from pathlib import Path
import numpy as np
import pandas as pd

import im_mainline_v1_1 as policy
import im_mo_csi1000_put_protection_battery_v6 as mkt
import research_im_short_put_recovery_atm_full_model_v1 as model_source
import research_im_short_put_recovery_atm_real_v1 as original
from im_put_maturity_valuation_tiers_v3 import actual_expiry_map, metrics, prepare_options

ROOT = Path(__file__).resolve().parent
RUN = ROOT / 'quant_param_scan_runs/20260916_ic_im_im_short95_recovery_v1_short_put_premium_decay_early_roll_premium_decay_threshold'
SPEC = ROOT / 'docs/im_short95_premium_decay_early_roll_scan_v1_spec.md'
THRESHOLDS = (None, .50, .60, .70, .80)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(['git', 'status', '--short'], cwd=ROOT, text=True, capture_output=True).stdout.strip()


def allowed_series() -> pd.Series:
    state, _ = policy.load_authoritative_local_state()
    state = state.set_index('date')
    return (state.valuation_score.notna() & state.valuation_tier.le(1) & state.momentum_120.ge(0)).shift(1, fill_value=False)


def choose_put(chain: pd.DataFrame, spot: float, after_expiry: pd.Timestamp | None = None,
               contract_month: pd.Timestamp | None = None):
    chain = chain[(chain.strike < spot) & (chain.open > 0) & (chain.volume > 0) & (chain.open_interest > 0)].copy()
    if contract_month is not None:
        chain = chain[chain.contract_month == contract_month]
    if after_expiry is not None:
        chain = chain[chain.actual_expiry > after_expiry]
        if not chain.empty:
            earliest = chain.actual_expiry.min()
            chain = chain[chain.actual_expiry == earliest]
    if chain.empty:
        return None
    chain['dist'] = abs(chain.strike - spot * .95)
    return chain.sort_values(['dist', 'strike']).iloc[0]


def run(base: pd.DataFrame, options: pd.DataFrame, futures: pd.DataFrame, threshold: float | None):
    allowed = allowed_series()
    by_day = {d: g for d, g in options.groupby('date', sort=False)}
    ol, fl = options.set_index(['contract', 'date']), futures.set_index(['contract', 'date'])
    equity, state, pos, pending, cycle = 1.0, 'idle', None, '', None
    rows, events, cycles = [], [], []
    label = 'hold_to_expiry' if threshold is None else f'decay_{int(threshold * 100)}'
    for i, b in enumerate(base.itertuples(index=False)):
        day, prev_equity = b.date, equity
        pnl = cost = 0.0
        action = ''
        if state == 'put':
            q = ol.loc[(pos['contract'], day)]
            pnl += pos['units'] * 200 * (pos['mark'] - q.settle)
            pos['mark'] = float(q.settle)
            if pending == 'early_roll':
                spot = float(base.iloc[i - 1].csi1000_price_close)
                target_month = pos['contract_month'] + pd.offsets.MonthBegin(1)
                newq = choose_put(by_day.get(day, options.iloc[:0]), spot, contract_month=target_month) if bool(allowed.get(day, False)) else None
                if newq is not None:
                    # The old leg was marked to today's settlement above; buy it at today's open.
                    pnl += pos['units'] * 200 * (q.settle - q.open)
                    pnl += pos['units'] * 200 * (newq.open - newq.settle)
                    cost += pos['units'] * 200 * spot * .0002
                    pos = {'contract': newq.contract, 'mark': float(newq.settle), 'units': pos['units'],
                           'expiry': newq.actual_expiry, 'entry_premium': float(newq.open),
                           'contract_month': newq.contract_month, 'rolled': True}
                    cycle['early_rolls'] += 1
                    cycle['last_roll_date'] = str(day.date())
                    action, pending = 'early_roll_buyback_and_sell_next_open', ''
                else:
                    # New month must pass the same valuation/MOM admission.  Do not
                    # force a roll; keep the old Put and evaluate again next day.
                    action, pending = 'early_roll_blocked_reentry', ''
        elif state == 'future':
            q = fl.loc[(pos['contract'], day)]
            if pending == 'exit':
                pnl += pos['units'] * 200 * (q.open - pos['mark'])
                cost += pos['units'] * 200 * q.open * .0001
                cycle['exit_date'] = str(day.date())
                cycle['recovery_days'] = (day - pd.Timestamp(cycle['assignment_date'])).days
                cycle['realized_pnl'] += pnl - cost
                cycle['exit_cycle_pnl'], cycle['closed'] = cycle['realized_pnl'], True
                cycles.append(cycle.copy())
                cycle, state, pos, pending, action = None, 'idle', None, '', 'recovery_exit_next_open'
            elif str(b.roll_to) not in ('nan', '') and str(b.roll_to) != pos['contract']:
                pnl += pos['units'] * 200 * (q.close - pos['mark'])
                nq = fl.loc[(b.roll_to, day)]
                cost += pos['units'] * 200 * (q.close + nq.close) * .0001
                pnl += pos['units'] * 200 * (nq.settle - nq.close)
                pos['contract'], pos['mark'], action = b.roll_to, float(nq.settle), 'monthly_roll_close'
            else:
                pnl += pos['units'] * 200 * (q.settle - pos['mark'])
                pos['mark'] = float(q.settle)
        if pending == 'assign':
            q = fl.loc[(b.contract, day)]
            pos = {'contract': b.contract, 'units': cycle['units'], 'mark': float(q.settle)}
            pnl += pos['units'] * 200 * (q.settle - q.open)
            cost += pos['units'] * 200 * q.open * .0001
            state, pending, action = 'future', '', 'assignment_buy_next_open'
            cycle['assignment_date'] = str(day.date())
        if state == 'idle' and i > 0 and not action and bool(allowed.get(day, False)):
            spot = float(base.iloc[i - 1].csi1000_price_close)
            target_month = day.to_period('M').to_timestamp() + pd.offsets.MonthBegin(1)
            q = choose_put(by_day.get(day, options.iloc[:0]), spot, contract_month=target_month)
            if q is not None:
                units = equity / (spot * 200)
                pos = {'contract': q.contract, 'mark': float(q.settle), 'units': units, 'expiry': q.actual_expiry,
                       'entry_premium': float(q.open), 'contract_month': q.contract_month, 'rolled': False}
                pnl += units * 200 * (q.open - q.settle)
                cost += units * 200 * spot * .0001
                cycle = {'candidate': label, 'entry_date': str(day.date()), 'put_contract': q.contract,
                         'strike': float(q.strike), 'spot_reference': spot, 'actual_moneyness': float(q.strike / spot),
                         'premium_points': float(q.open), 'units': units, 'realized_pnl': 0.0, 'closed': False,
                         'assignment_date': '', 'early_rolls': 0, 'last_roll_date': ''}
                state, action = 'put', 'sell_next_month_put_open'
        if cycle is not None:
            cycle['realized_pnl'] += pnl - cost
        if state == 'put' and day == pos['expiry']:
            cycle['settlement_points'], cycle['expiry_date'] = pos['mark'], str(day.date())
            if pos['mark'] > 0:
                pending, state, pos, action = 'assign', 'idle', None, 'itm_cash_settlement'
            else:
                cycle['exit_date'], cycle['exit_cycle_pnl'], cycle['closed'] = str(day.date()), cycle['realized_pnl'], True
                cycles.append(cycle.copy())
                cycle, state, pos, action = None, 'idle', None, 'worthless_expiry'
        elif state == 'put' and threshold is not None and not pos['rolled'] and pending == '' and pos['mark'] <= pos['entry_premium'] * (1 - threshold):
            pending, action = 'early_roll', f'premium_decay_{int(threshold * 100)}_signal_close'
        if state == 'future' and cycle['realized_pnl'] >= pos['units'] * 200 * pos['mark'] * .0001:
            pending = 'exit'
        cash = prev_equity * (.7 if state != 'idle' or pending == 'assign' else 1.0) * original.CASH
        equity += pnl - cost + cash
        assert np.isfinite(equity) and equity > 0
        row = {'date': day, 'candidate': label, 'return_net': equity / prev_equity - 1, 'nav': equity,
               'pnl': pnl, 'cost': cost, 'cash': cash, 'state': state, 'action': action,
               'pending': pending, 'premium_fraction': np.nan if state != 'put' else pos['mark'] / pos['entry_premium']}
        rows.append(row)
        if action:
            events.append({**row, 'contract': '' if pos is None else pos['contract']})
    if cycle is not None:
        cycle['mark_date'], cycle['open_cycle_pnl'] = str(day.date()), cycle['realized_pnl']
        cycles.append(cycle.copy())
    return pd.DataFrame(rows), pd.DataFrame(events), pd.DataFrame(cycles)


def extended_model_inputs():
    market, base, _, futures, checks, basis = model_source.build_inputs()
    dates = pd.DatetimeIndex(market.date)
    expiries = {x: mkt.third_friday(x, dates) for x in pd.date_range(market.date.min().to_period('M').to_timestamp(), market.date.max() + pd.DateOffset(months=16), freq='MS')}
    keys = set()
    for i, row in enumerate(market.itertuples(index=False)):
        if i == 0: continue
        spot = market.iloc[i - 1].spot_close
        step = 25 if spot <= 2500 else 50 if spot <= 5000 else 100 if spot <= 10000 else 200
        for ahead in range(1, 16):
            keys.add((row.date.to_period('M').to_timestamp() + pd.offsets.MonthBegin(ahead), math.floor(spot * .95 / step + .5) * step))
    rows = []
    for month, strike in keys:
        expiry = expiries[month]
        for row in market[(market.date >= month - pd.DateOffset(months=3)) & (market.date <= expiry)].itertuples(index=False):
            t = max((expiry - row.date).days / 365, 0)
            op = mkt.proxy.bs_put(row.spot_open, strike, row.rate_open, row.dividend_open, row.sigma_open, t)
            cl = mkt.proxy.bs_put(row.spot_close, strike, row.rate_close, row.dividend_close, row.sigma_close, t)
            rows.append({'date': row.date, 'contract': 'MO' + month.strftime('%y%m') + '-P-' + str(strike),
                         'contract_month': month, 'actual_expiry': expiry, 'strike': strike, 'open': op,
                         'settle': cl, 'close': cl, 'volume': 1, 'open_interest': 1})
    return base, pd.DataFrame(rows), futures, checks, basis


def summaries(daily: pd.DataFrame):
    rows, wide = [], []
    for candidate, g in daily.groupby('candidate', sort=False):
        g, w = g.sort_values('date'), {'candidate': candidate}
        for segment, years in [('full', None), ('last_10y', 10), ('last_5y', 5), ('last_3y', 3), ('last_1y', 1)]:
            start = g.date.min() if years is None else g.date.max() - pd.DateOffset(years=years)
            ok = years is None or g.date.min() <= start
            m = metrics(g[g.date >= start].return_net) if ok else {k: 'N/A' for k in ['ann_return', 'ann_vol', 'sharpe_repo', 'max_dd']}
            rows.append({'candidate': candidate, 'segment': segment, 'start': str(start.date()), 'end': str(g.date.max().date()),
                         'rows': len(g[g.date >= start]) if ok else 0, 'available': ok,
                         'unavailable_reason': '' if ok else 'history shorter than requested window', **m})
            for k, v in m.items(): w[f'{k}_{segment}'] = v
        wide.append(w)
    return pd.DataFrame(rows), pd.DataFrame(wide)


def main():
    meta = json.loads((RUN / 'scan_meta.json').read_text(encoding='utf-8'))
    if meta.get('phase') != 'init': raise RuntimeError('run already started')
    rb = pd.read_csv(original.BASE, parse_dates=['date'])
    raw = pd.read_csv(original.OP, parse_dates=['date']); raw['contract_month'] = pd.to_datetime('20' + raw.contract.str[2:6], format='%Y%m')
    ro = prepare_options(raw, actual_expiry_map(raw, rb)); rf = pd.read_csv(original.FU, parse_dates=['date'])
    mb, mo, mf, checks, basis = extended_model_inputs()
    ds, es, cs = [], [], []
    for layer, base, options, futures in [('real', rb, ro, rf), ('model', mb, mo, mf)]:
        for threshold in THRESHOLDS:
            d, e, c = run(base, options, futures, threshold)
            candidate = f'{layer}_hold_to_expiry' if threshold is None else f'{layer}_decay_{int(threshold*100)}'
            d['candidate'] = candidate; e['candidate'] = candidate; c['candidate'] = candidate
            assert abs((d.pnl - d.cost).sum() - c.realized_pnl.sum()) < 1e-11
            ds.append(d); es.append(e); cs.append(c)
    daily, events, cycles = pd.concat(ds, ignore_index=True), pd.concat(es, ignore_index=True), pd.concat(cs, ignore_index=True)
    summary, wide = summaries(daily)
    out = RUN / 'daily_outputs'; out.mkdir(exist_ok=False)
    daily.to_csv(out / 'daily.csv.gz', index=False, compression='gzip'); events.to_csv(out / 'events.csv', index=False); cycles.to_csv(out / 'cycles.csv', index=False)
    summary.to_csv(RUN / 'scan_summary.csv', index=False, encoding='utf-8-sig'); wide.to_csv(RUN / 'window_metrics.csv', index=False, encoding='utf-8-sig')
    early = cycles.groupby('candidate').agg(cycles=('closed', 'size'), completed=('closed', 'sum'), early_rolls=('early_rolls', 'sum')).reset_index()
    early.to_csv(RUN / 'roll_diagnostics.csv', index=False, encoding='utf-8-sig')
    meta.update(scan_type='premium_decay_early_roll', baseline={'candidate':'*_hold_to_expiry','definition':'same state machine, no early roll'},
                candidate_grid=[{'premium_decay': x} for x in THRESHOLDS], data_snapshot={'real_start':str(rb.date.min().date()),'real_end':str(rb.date.max().date()),'model_start':str(mb.date.min().date()),'model_end':str(mb.date.max().date()),'model_checks':checks,'basis_calibration':basis},
                cost_model={'one_way_notional':.0001,'early_roll_two_sides':.0002,'reserve':.30,'cash_annual':.03},
                outputs={**meta['outputs'],'daily':str(out/'daily.csv.gz'),'events':str(out/'events.csv'),'cycles':str(out/'cycles.csv'),'roll_diagnostics':str(RUN/'roll_diagnostics.csv')},
                source_hashes={str(p):sha(p) for p in (original.BASE,original.OP,original.FU,SPEC,Path(__file__))},
                warnings=['The model uses theoretical Black-Scholes contracts and ex-post basis calibration; it is directional only.','No bid/ask, capacity, dynamic margin, forced liquidation, integer contracts or slippage.','An early roll resets the premium reference to the new leg open price.'],
                unavailable_segments={f'real_{x}':{'last_10y':'Real IM/MO begins 2022-07-22.','last_5y':'Real IM/MO begins 2022-07-22.'} for x in ['hold_to_expiry','decay_50','decay_60','decay_70','decay_80']}, git_status_after=git_status())
    (RUN / 'scan_meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    record = '# IM 卖95%Put权利金衰减提前换月扫描 v1\n\n研究专用，真实与模型严格分层；规则详见预注册规格。提前换月是T收盘触发、T+1开盘双边执行，且以新腿卖出开盘价重新计量衰减。\n\n## Full-Sample Results\n\n' + summary[summary.segment.eq('full')].to_markdown(index=False) + '\n\n## Roll Diagnostics\n\n' + early.to_markdown(index=False) + '\n\n## Decision\n\nresearch_only_pending_interpretation\n'
    (RUN / 'record.md').write_text(record, encoding='utf-8')
    with (RUN / 'command_log.txt').open('a', encoding='utf-8') as f: f.write(f'cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n')
    print(summary[summary.segment.eq('full')].to_string(index=False)); print(early.to_string(index=False))


if __name__ == '__main__':
    main()
