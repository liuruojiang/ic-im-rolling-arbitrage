"""Input-bound adapter for the accepted 10/22 current-rule historical chain.

Only data locations, source coverage dates and coverage assertions are adapted.
The strategy, identity gates, exchange calendar and account equations are imported
from the actual current source. No production state is opened or persisted.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import types
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
NATIVE = ROOT / 'outputs/re_certification/icim_v14_fix4_recert_20260924/l5_native_signal_reconstruction_20260925'
STUDY = ROOT / 'quant_param_scan_runs/20260926_icim_fix8_momentum_transfer_robustness'
PAIRED = ROOT / 'quant_param_scan_runs/20260928_fear_entry_paired_exit_fix9_certified_v2'
REPAIR = ROOT / 'quant_research_runs/20261016_put_execution_repairs_v1'
CALENDAR = ROOT / 'quant_research_runs/20261022_historical_calendar_repair_v1'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(name, path, replacements=()):
    """Load current source; record every input/date/count-only replacement."""
    source = Path(path).read_text(encoding='utf-8')
    edits = []
    for before, after in replacements:
        if source.count(before) != 1:
            raise RuntimeError(f'expected exactly one adapter anchor: {path}: {before!r}')
        source = source.replace(before, after, 1)
        edits.append({'before': before, 'after': after})
    module = types.ModuleType(name)
    module.__file__ = str(path)
    module.__adapter_input_patches__ = edits
    sys.modules[name] = module
    exec(compile(source, str(path), 'exec'), module.__dict__)
    return module


class CurrentHistory:
    def __init__(self, raw: Path, output: Path, completed_close: str):
        self.raw, self.output = Path(raw), Path(output)
        self.close = str(completed_close)
        self.inputs = self.output / 'inputs'
        self.output.mkdir(parents=True, exist_ok=True)
        self.inputs.mkdir(exist_ok=True)
        os.environ['ICIM_STATE_DIR'] = str(self.output / 'isolated_state')
        sys.dont_write_bytecode = True
        sys.path[:0] = [str(ROOT), str(STUDY), str(NATIVE)]
        import native_fix4_historical_sources_v1 as base
        import native_fix4_historical_sources_v2 as source_v2
        self.base, self.formal, self.source_v2 = base, base.formal, source_v2
        self.anchor_before = deepcopy(self.formal.LIVE_CONTINUATION_ANCHOR)
        self.paired = load('latest_current_paired', PAIRED / 'run_scan.py')
        self.history = load('latest_calendar_rebuild_helpers', CALENDAR / 'history/rebuild_history.py')
        self.lifecycle = load('latest_execution_cost_lifecycle', REPAIR / 'seller_lifecycle_execution_cost.py')
        self.account = self.paired.listed.grid_scan.account_stage
        self.audit = {'classification': 'current_fix11_research_listed_history_account',
                      'build_identity': self.formal.BUILD_ID,
                      'formal_source_sha256': sha(self.formal.__file__),
                      'policy_source_sha256': sha(self.formal.v14_policy.__file__),
                      'account_engine': str(Path(self.account.__file__).relative_to(ROOT)),
                      'account_engine_sha256': sha(self.account.__file__),
                      'seller_lifecycle_engine': str((REPAIR / 'seller_lifecycle_execution_cost.py').relative_to(ROOT)),
                      'seller_lifecycle_sha256': sha(self.lifecycle.__file__),
                      'historical_identity_binding_source_sha256': sha(self.history.__file__),
                      'calendar_years': sorted(self.formal._OFFICIAL_EXCHANGE_CALENDAR_YEARS),
                      'input_adapter_patches': {}, 'phases': {}}

    def align_current_account(self):
        """Reuse the already-reviewed three formal-alignment repairs unchanged."""
        previous = ROOT / 'outputs/fix11_nav_1y_20261015_close'
        manifest = json.loads((previous / 'engine_minimal_patch_manifest.json').read_text(encoding='utf-8'))
        if sha(self.account.__file__) != manifest['source_engine_sha256']:
            raise RuntimeError('native account changed since the reviewed minimal alignment; review required')
        source = previous / 'native_account_fix11_current.py'
        if sha(source) != manifest['isolated_engine_sha256']:
            raise RuntimeError('reviewed isolated current-account bytes differ from recorded alignment')
        destination = HERE / 'native_account_fix11_current.py'
        if destination.exists() and destination.read_bytes() != source.read_bytes():
            raise RuntimeError('new isolated account destination contains conflicting evidence')
        if not destination.exists():
            shutil.copy2(source, destination)
        self.account = load('latest_isolated_current_account', destination)
        self.paired.listed.grid_scan.account_stage = self.account
        self.audit['account_alignment'] = {'classification': 'inherited_current_formal_alignment_not_new_parameters',
            'reviewed_manifest': str((previous / 'engine_minimal_patch_manifest.json').relative_to(ROOT)),
            'source_engine_sha256': manifest['source_engine_sha256'],
            'isolated_engine_sha256': sha(destination), 'patches': manifest['patches'],
            'current_formal_references_inspected': ['_v14_ic_core_overlay', '_v14_prepare_profit_open_plan', '_v14_schedule_ordinary_put',
                                                  'ic_im_v1_4_policy.project_ordinary_open']}
        self.audit['account_engine'] = str(destination.relative_to(ROOT))
        self.audit['account_engine_sha256'] = sha(destination)

    def source(self):
        base, raw, formal = self.base, self.raw, self.formal
        completed_stamp = pd.Timestamp(self.close)
        base.IC_DATA = raw
        base.IC_FUTURES, base.IM_FUTURES = raw / 'cffex_ic_contracts.csv', raw / 'cffex_im_contracts.csv'
        base.IM_ARCHIVES = raw / 'cffex_raw'
        parent = self.source_v2.DatedSources

        class Sources(parent):
            def __init__(self):
                super().__init__()
                self.ic_master['contract_month'] = pd.to_datetime(self.ic_master.contract_month, format='mixed')
                for frame in (self.ic_master, self.ic_hist):
                    frame['security_id'] = frame.security_id.astype(str).str.replace(r'\.0$', '', regex=True)
                    if not frame.security_id.str.fullmatch(r'[0-9]{8}').all():
                        raise RuntimeError('invalid raw IC security identity')
                self.mo_extension = pd.read_csv(raw / 'sina_mo_put_histories.csv.gz', parse_dates=['date'])
                ic_file = raw / 'sina_510500_puts_close.csv.gz'
                mo_file = raw / 'eastmoney_mo_puts_close.csv'
                self.ic_noon = (pd.read_csv(ic_file, parse_dates=['date'], dtype={'security_id': str}) if ic_file.is_file()
                                else pd.DataFrame(columns=['date','security_id']))
                self.mo_noon = (pd.read_csv(mo_file, parse_dates=['date']) if mo_file.is_file()
                                else pd.DataFrame(columns=['date','instrument']))
                self.ic_noon = self.ic_noon.loc[self.ic_noon.date.gt(completed_stamp)].copy()
                self.mo_noon = self.mo_noon.loc[self.mo_noon.date.gt(completed_stamp)].copy()
                self.ic_noon['security_id'] = self.ic_noon.security_id.astype(str).str.replace(r'\.0$', '', regex=True)
                if self.ic_noon.duplicated(['date', 'security_id']).any() or self.mo_noon.duplicated(['date', 'instrument']).any():
                    raise RuntimeError('duplicate frozen noon contract identity')
                if self.mo_extension.duplicated(['date', 'instrument']).any():
                    raise RuntimeError('duplicate dated extension MO identity')
                self._ic_cache, self._mo_cache, self._future_cache = {}, {}, {}

            def futures_on(self, product, day):
                key = product, day
                if key not in self._future_cache:
                    q = self.futures[product].loc[self.futures[product].date.eq(pd.Timestamp(day))].copy()
                    prices = q[['open', 'close']].to_numpy(float)
                    if q.empty or not np.isfinite(prices).all() or (prices <= 0).any():
                        raise RuntimeError(f'invalid dated futures: {product} {day}')
                    if day <= date(2026, 9, 28) and q.settle.isna().any():
                        raise RuntimeError(f'missing official historical settlement: {product} {day}')
                    self._future_cache[key] = q
                return self._future_cache[key].copy(deep=True)

            def ic_on(self, day):
                if day not in self._ic_cache:
                    stamp = pd.Timestamp(day)
                    noon_rows = self.ic_noon.loc[self.ic_noon.date.eq(stamp)].copy()
                    if len(noon_rows):
                        master = self.ic_master.loc[self.ic_master.date.eq(stamp)].copy()
                        if master.empty or stamp not in self.ic_etf.index:
                            raise RuntimeError('noon IC lacks same-day master or ETF')
                        q = master.merge(noon_rows, on=['date', 'security_id'], how='left', validate='one_to_one')
                        q = q.rename(columns={'contract_id': 'contract', 'close': 'last'})
                        if q.contract.duplicated().any():
                            raise RuntimeError('duplicate frozen noon IC contract')
                        spot = float(self.ic_etf.loc[stamp, 'close'])
                    else:
                        spot, q = super().ic_on(day)
                    if not np.isfinite(spot) or spot <= 0:
                        raise RuntimeError(f'invalid dated IC ETF: {day}')
                    if not pd.to_datetime(q.date).eq(stamp).all() or q.security_id.duplicated().any():
                        raise RuntimeError(f'wrong date or duplicate security in frozen IC daily chain: {day}')
                    # The joined daily-bar table also stores original
                    # provider timestamps for a few observations. Its rows
                    # are used as dated bars and never as live tick evidence.
                    q = q.drop(columns=['source_stamp'], errors='ignore')
                    self._ic_cache[day] = spot, q
                spot, q = self._ic_cache[day]
                return spot, q.copy(deep=True)

            def im_mo_on(self, day):
                if day not in self._mo_cache:
                    noon_rows = self.mo_noon.loc[self.mo_noon.date.eq(pd.Timestamp(day))].copy()
                    if len(noon_rows):
                        q = noon_rows
                    elif day <= date(2026, 9, 28):
                        q = super().im_mo_on(day)
                    else:
                        q = self.mo_extension.loc[self.mo_extension.date.eq(pd.Timestamp(day))].copy()
                        if q.empty or q.instrument.duplicated().any():
                            raise RuntimeError(f'missing or duplicate dated MO: {day}')
                    self._mo_cache[day] = q
                return self._mo_cache[day].copy(deep=True)

        source = Sources()
        self.sources = source
        self.counts = {}
        for product, first in [('IC', '2022-09-19'), ('IM', '2022-07-22')]:
            days = source.futures[product].loc[source.futures[product].date.between(first, self.close), 'date'].drop_duplicates()
            if days.empty or str(days.max().date()) != self.close:
                raise RuntimeError(f'{product} futures do not cover requested close {self.close}')
            if not all(formal._is_exchange_trading_day(x.date()) for x in days):
                raise RuntimeError(f'{product} futures contain non-exchange dates')
            self.counts[product] = len(days)
        return source

    @contextmanager
    def dated_ic_selection(self, observed_day):
        """Bind production economics to a frozen historical input boundary."""
        formal = self.formal
        if not formal._is_exchange_trading_day(observed_day):
            raise RuntimeError(f'IC listing observation is not on a trading day: {observed_day}')
        _, chain = self.sources.ic_on(observed_day)
        if chain.empty or not pd.to_datetime(chain.date).eq(pd.Timestamp(observed_day)).all():
            raise RuntimeError(f'IC chain does not prove the historical observation date: {observed_day}')
        months = sorted(chain.contract_month.dt.strftime('%Y%m').unique())

        def fetch_month(yy_mm, reference_day):
            if reference_day != observed_day:
                next_session = formal._roll_forward_exchange_day(observed_day + timedelta(days=1), require_official=True)
                if reference_day != next_session or reference_day != formal._third_friday(reference_day.year, reference_day.month):
                    raise RuntimeError(f'unexpected valuation vs monthly expiry reference: {observed_day} -> {reference_day}')
            rows = chain.loc[chain.contract_month.dt.strftime('%y%m').eq(yy_mm)].copy()
            if rows.empty or rows.contract.duplicated().any() or rows.security_id.duplicated().any():
                raise RuntimeError(f'IC dated monthly chain is missing or ambiguous: {observed_day} {yy_mm}')
            if not rows.security_id.str.fullmatch('[0-9]{8}').all():
                raise RuntimeError(f'IC dated contract identity is missing: {observed_day} {yy_mm}')
            return rows, {'date': observed_day.isoformat(), 'time': None, 'source': 'dated_sse_master_plus_vendor_close'}

        clock = datetime.combine(observed_day, datetime.min.time(), tzinfo=formal.BEIJING).replace(hour=16)
        with formal.runtime_clock(clock), \
             patch.object(formal, 'fetch_510500_expiries_with_failover', return_value=months), \
             patch.object(formal, '_fetch_510500_chain_for_day', side_effect=fetch_month):
            yield

    def dated_ic_candidate(self, selector):
        def candidate(signal, **kwargs):
            with ExitStack() as stack:
                stack.enter_context(self.dated_ic_selection(signal['market_date']))
                if kwargs.get('roll_from_expiry') is not None:
                    # If the exact next month is genuinely absent from the
                    # dated master, use the formal typed nonlisting branch.
                    stack.enter_context(patch.object(self.formal, '_IC_CANDIDATE_LISTING_EFFECTIVE_SIGNAL_DATE', date(1900, 1, 1)))
                return selector(signal, **kwargs)
        return candidate

    def risk(self):
        risk = load('native_fix4_risk_signals_v1', NATIVE / 'native_fix4_risk_signals_v1.py', [
            ('last = pd.Timestamp("2026-08-13" if product == "IC" else "2026-08-14")',
             f'last = pd.Timestamp("{self.close}")'),
            ('if len(out) != (945 if product == "IC" else 986):', 'if len(out) != EXPECTED_COUNTS[product]:')])
        risk.EXPECTED_COUNTS = self.counts
        risk.HERE, risk.ROOT, risk.VALUATION = self.inputs, ROOT, self.raw
        risk.OHLCV = {'IC': self.raw / 'sina_000905_index.csv.gz',
                      'IM': self.raw / 'im_ohlcv_frozen_plus_fresh.csv.gz'}
        self.risk_engine = risk
        self.account.OHLCV = risk.OHLCV
        fear = pd.read_csv(self.raw / 'fear_greed_full.csv', dtype={'date': str}).set_index('date', verify_integrity=True).fear_greed_index
        fear = pd.to_numeric(fear, errors='raise')
        self.audit['input_adapter_patches']['risk'] = risk.__adapter_input_patches__
        self.risks = {}
        for product in ['IC', 'IM']:
            with patch.object(self.formal, 'IC_MOMENTUM_EFFECTIVE_SIGNAL_DATE', date(1900, 1, 1)):
                frame, detail = risk.produce(product, self.sources)
            frame, signals, grid = self.paired.build_targets(frame, fear, product, 50.)
            self.risks[product] = frame
            frame.to_csv(self.inputs / f'native_fix4_{product.lower()}_risk_signals_v1.csv.gz', index=False, compression='gzip')
            signals.to_csv(self.output / f'{product.lower()}_grid_signals.csv', index=False)
            self.audit['phases'][f'{product}_risk'] = {'raw': detail, 'grid': grid,
                'latest_fear_actual_date': max((d for d in fear.index if d <= self.close), default=None),
                'missing_fear_dates': [d for d in frame.signal_date if d not in fear.index]}
            print(json.dumps({'stage': 'risk', 'product': product, 'rows': len(frame), 'last': str(frame.signal_date.iloc[-1])}), flush=True)

    def targets(self):
        binding = self.history.HistoricalICBinding(self.formal)
        self.audit['historical_IC_source_compatibility'] = {
            'classification': 'research_dated_close_bars_without_live_timestamp_claim',
            'scope': 'IC historical target and seller selector data access only',
            'observation': 'exact SSE date/contract/security master, exact dated vendor open/close',
            'selection_date': 'same trading day or next-session monthly expiry preview using preceding day observations',
            'quote_timestamp': None,
            'selector': 'current formal tenor, strike, pricing positivity, identity and sizing retained',
        }
        for product, version in [('IC', 'v2'), ('IM', 'v3')]:
            old_count = 'if len(frame) != 945 or' if product == 'IC' else 'if len(frame) != 986:'
            new_count = f'if len(frame) != {self.counts[product]} or' if product == 'IC' else f'if len(frame) != {self.counts[product]}:'
            replacements = [
                ('gov = pd.read_csv(ROOT / "data/ic_im_valuation_risk_premium_forecast_v4/chinabond_government_10y.csv", parse_dates=["date"])',
                 'gov = pd.read_csv(RAW / "chinabond_government_10y.csv", parse_dates=["date"])'),
                (old_count, new_count)]
            if product == 'IC':
                anchor = 'with patch.object(formal, "fetch_sse_510500_expiries", return_value=months),'
                replacements.append((anchor, anchor.replace('with ', 'with DATED_IC_SELECTION(day), ', 1)))
            engine = load(f'latest_{product.lower()}_target_engine', NATIVE / f'native_fix4_{product.lower()}_noseller_targets_v1.py', replacements)
            engine.HERE, engine.RAW, engine.ROOT = self.inputs, self.raw, ROOT
            if product == 'IC':
                engine.DATED_IC_SELECTION = self.dated_ic_selection
            self.audit['input_adapter_patches'][f'{product}_targets'] = engine.__adapter_input_patches__
            with ExitStack() as stack:
                stack.enter_context(patch.object(engine, 'DatedSources', return_value=self.sources))
                if product == 'IC':
                    stack.enter_context(patch.object(self.formal, '_ic_put_selected_security_id', side_effect=binding.selected_security))
                    stack.enter_context(patch.object(self.formal, '_size_existing_ic_put', side_effect=binding.resize))
                else:
                    stack.enter_context(patch.object(self.formal, 'select_im_call_d10', return_value=None))
                    stack.enter_context(patch.object(self.formal, 'select_im_call_rescue', return_value=None))
                engine.main(version=version)
            detail = json.loads((self.inputs / f'native_fix4_{product.lower()}_noseller_targets_{version}.json').read_text(encoding='utf-8'))
            self.audit['phases'][f'{product}_targets'] = detail
            if detail['fail_closed_events']:
                raise RuntimeError(f'{product} unresolved target evidence: {detail["fail_closed_events"]}')
        self.audit['phases']['IC_identity_checks'] = binding.identity_checks

    def candidates(self, counts=None):
        counts = counts or self.counts
        stage = load('latest_seller_candidate_engine', NATIVE / 'native_fix4_seller_candidates_v1.py', [
            ('gov = pd.read_csv(ROOT / "data/ic_im_valuation_risk_premium_forecast_v4/chinabond_government_10y.csv", parse_dates=["date"])',
             'gov = pd.read_csv(RAW / "chinabond_government_10y.csv", parse_dates=["date"])'),
            ('if len(frame) != 945 + 986 - 2:', f'if len(frame) != {sum(counts.values()) - 2}:')])
        stage.HERE, stage.RAW, stage.ROOT = self.inputs, self.raw, ROOT
        stage.OHLCV = self.risk_engine.OHLCV
        self.audit['input_adapter_patches']['candidates'] = stage.__adapter_input_patches__
        with patch.object(stage, 'DatedSources', return_value=self.sources), \
             patch.object(self.formal, '_v14_ic_short_put_candidate', new=self.dated_ic_candidate(self.formal._v14_ic_short_put_candidate)), \
             patch.object(self.formal.v14_policy, 'IC_SELLER_MOM120_EFFECTIVE_SIGNAL_DATE', date(1900, 1, 1)):
            stage.main()
        return pd.read_csv(self.inputs / 'native_fix4_seller_candidates_v1.csv.gz')

    def replay(self, candidates, noon_date=None, terminal_date=None):
        mark_date = terminal_date or noon_date
        gov = pd.read_csv(self.raw / 'chinabond_government_10y.csv', parse_dates=['date']).set_index('date', verify_integrity=True).gov10y_yield
        spot = pd.read_csv(self.risk_engine.OHLCV['IM'], parse_dates=['date']).set_index('date', verify_integrity=True).close
        self.lifecycle.HERE, self.lifecycle.VERSION = self.inputs, 'v4'
        self.lifecycle.OHLCV = self.risk_engine.OHLCV
        daily_parts, event_parts = [], []
        for product in ['IC', 'IM']:
            with patch.object(self.formal.v14_policy, 'IC_SELLER_MOM120_EFFECTIVE_SIGNAL_DATE', date(1900, 1, 1)), \
                 patch.object(self.formal, '_v14_ic_short_put_candidate', new=self.dated_ic_candidate(self.formal._v14_ic_short_put_candidate)):
                life, life_audit = self.lifecycle.run(product, 'repeat_roll', self.sources, gov, spot)
            if life.action.eq('HOLD_FAIL_CLOSED').any():
                raise RuntimeError(f'{product} unresolved lifecycle target evidence')
            entries = life.loc[life.action.isin(['ENTER_SHORT_PUT', 'ROLL_SHORT_PUT'])]
            if not np.array_equal(entries.entry_premium_state_after.to_numpy(float), entries.fill_new_t1.to_numpy(float)):
                raise RuntimeError('seller decay premium differs from actual dated entry open')
            life_audit['entry_basis'] = 'Every new/rolled short Put uses its recorded T+1 positive dated open for premium state and decay comparisons.'
            life_audit['entry_open_basis_checks'] = len(entries)
            life.to_csv(self.output / f'{product.lower()}_seller_lifecycle.csv.gz', index=False, compression='gzip')
            self.paired.listed.grid_scan.configure_account(self.inputs)
            self.account.OHLCV = self.risk_engine.OHLCV
            original_cash = self.account.Account.accrue_cash
            def accrue(account, day, free_cash, rate):
                return original_cash(account, day, free_cash, rate * (.5 if str(day) == noon_date else 1.))
            with patch.object(self.account.Account, 'accrue_cash', new=accrue):
                daily, events, account_audit = self.account.replay(product, 'repeat_roll', self.sources, life, candidates, gov, spot)
            account_audit['model_execution'] = ('T close freezes targets and 3x contract; T+1 dated open futures/seller and ordinary core/momentum Put; '
                'documented sticky-IV buyer open proxy when a dated positive open is absent; monthly maintenance once at settlement-day close; '
                'confirmed quarterly rolls at signal-day close; no volume cap')
            account_audit['configuration'] = {name: sorted(getattr(self.account, name)) if isinstance(getattr(self.account, name), frozenset)
                else getattr(self.account, name) for name in ['VERSION', 'MARGIN_MODEL', 'EXECUTION_MODEL', 'LIFECYCLE_VERSION',
                'QUARTER_ROLL_AT_SIGNAL_CLOSE', 'PROFIT_RESTRIKE_MULTIPLE', 'PROFIT_EXECUTION_PHASE', 'BUYER_OPEN_SLEEVES',
                'CORE_UNITS', 'CORE_BUYER_SCALE', 'MOMENTUM_SCALE', 'SELLER_SCALE', 'SELLER_NAV_BUFFER_RATE', 'DISABLED_MODULES']}
            if mark_date is not None:
                last_life = life.iloc[-1]
                if str(last_life.execution_date) != mark_date:
                    raise RuntimeError('noon lifecycle does not execute the latest completed plan')
                daily.loc[daily.date.astype(str).eq(mark_date), 'seller_route'] = last_life.route_after_execution
                noon_trades = events.loc[events.event.eq('trade') & events.date.astype(str).eq(mark_date)]
                if len(noon_trades) and (noon_trades.signal_date.astype(str) != self.close).any():
                    raise RuntimeError('noon trade was not derived exclusively from the last completed signal')
                terminal_events = events.loc[events.date.astype(str).eq(mark_date)]
                if terminal_date is not None and product == 'IC' and terminal_events.event.eq('assign_short_put').any():
                    daily.loc[daily.date.astype(str).eq(mark_date), 'seller_route'] = 'assigned_etf'
                if terminal_date is not None:
                    self.audit.setdefault('terminal_expiry_accounting', {})[product] = {
                        'event_date': mark_date, 'assign_events': int(terminal_events.event.eq('assign_short_put').sum()),
                        'financial_rule': 'Native mixed close expiry marks to intrinsic, then physical assignment reference at strike.',
                        'settlement_timing': 'Inherited theoretical close-date accounting of ETF receivable and exercise payable; actual exchange securities/cash settlement is next trading day.'}

            expected = self.counts[product] + int(mark_date is not None)
            if len(daily) != expected or daily.date.astype(str).iloc[-1] != (mark_date or self.close):
                raise RuntimeError(f'{product} account coverage mismatch')
            if not np.isfinite(daily.return_net).all() or not np.isfinite(daily.nav).all() or not daily.nav.gt(0).all():
                raise RuntimeError(f'{product} invalid account arithmetic')
            nav_error = float(np.max(np.abs(np.cumprod(1 + daily.return_net.to_numpy(float)) - daily.nav.to_numpy(float))))
            if nav_error > 1e-10 or (daily.cash_close-daily.margin_reserved).min() < -1e-7:
                raise RuntimeError(f'{product} account funding/NAV identity failed')
            if events.loc[events.event.eq('trade'), 'symbol'].astype(str).str.startswith('option|call|').any():
                raise RuntimeError(f'{product} unexpected Call trade')
            self.audit['phases'][f'{product}_account'] = {'lifecycle': life_audit, 'account': account_audit,
                'nav_return_identity_max_abs_error': nav_error, 'rows': len(daily), 'last': str(daily.date.iloc[-1]),
                'final_nav': float(daily.nav.iloc[-1])}
            daily['product'], events['product'] = product, product
            daily_parts.append(daily); event_parts.append(events)
            print(json.dumps({'stage': 'account', 'product': product, 'rows': len(daily), 'last': str(daily.date.iloc[-1]), 'final_nav': float(daily.nav.iloc[-1])}), flush=True)
        self.audit['anchors_unchanged'] = self.formal.LIVE_CONTINUATION_ANCHOR == self.anchor_before
        if not self.audit['anchors_unchanged']:
            raise RuntimeError('production anchor changed during research replay')
        return pd.concat(daily_parts, ignore_index=True), pd.concat(event_parts, ignore_index=True)

    def prepare_terminal(self, destination, mark_date):
        """Append a terminal mark row: no new risk calculation or close decision."""
        destination = Path(destination)
        destination.mkdir(exist_ok=False)
        for path in self.inputs.iterdir():
            if path.is_file():
                shutil.copy2(path, destination / path.name)
        for product, version in [('IC', 'v2'), ('IM', 'v3')]:
            for name in [f'native_fix4_{product.lower()}_risk_signals_v1.csv.gz',
                         f'native_fix4_{product.lower()}_noseller_targets_{version}.csv.gz']:
                frame = pd.read_csv(destination / name)
                if str(frame.signal_date.iloc[-1]) != self.close or pd.notna(frame.execution_date.iloc[-1]):
                    raise RuntimeError('unexpected completed-close terminal row')
                final = frame.iloc[-1].copy()
                final['signal_date'], final['execution_date'] = mark_date, None
                # These T-close triggers must never be manufactured at noon.
                for field in ['monthly_reset_due', 'quarter_roll_confirmed', 'new_put_selection']:
                    if field in final:
                        final[field] = False
                final['terminal_mark_only'] = True
                frame.loc[frame.index[-1], 'execution_date'] = mark_date
                frame = pd.concat([frame, final.to_frame().T], ignore_index=True)
                frame.to_csv(destination / name, index=False, compression='gzip')
        for name in ['native_fix4_seller_candidates_v1.csv.gz', 'native_fix4_seller_candidates_v1.json']:
            (destination / name).unlink()
        self.inputs = destination
        self.output = destination.parent
        self.audit = deepcopy(self.audit)
        self.audit['terminal_mark'] = {'date': mark_date, 'classification': 'completed_session_terminal_mark_no_new_signal',
            'latest_actual_signal': self.close, 'open_execution': f'{self.close} frozen targets execute at {mark_date} open',
            'terminal_input_rule': 'last completed risk/target copied only for terminal shape; no new signal, selection, maintenance or close roll; native expiry settlement still runs',
            'cash_accrual_fraction': 1.0}


def check_original_account_baseline(output):
    """Reproduce the unchanged 945/986-day account using original saved inputs."""
    output = Path(output)
    output.mkdir(exist_ok=False)
    sys.path[:0] = [str(ROOT), str(STUDY), str(NATIVE)]
    paired = load('latest_baseline_paired', PAIRED / 'run_scan.py')
    import native_fix4_historical_sources_v2 as sources
    import native_fix4_risk_signals_v1 as risk
    reference_dir = CALENDAR / 'rebuilt_02'
    source = sources.DatedSources()
    candidates = pd.read_csv(reference_dir / 'inputs/native_fix4_seller_candidates_v1.csv.gz')
    gov = pd.read_csv(ROOT / 'data/ic_im_valuation_risk_premium_forecast_v4/chinabond_government_10y.csv', parse_dates=['date']).set_index('date').gov10y_yield
    spot = pd.read_csv(risk.OHLCV['IM'], parse_dates=['date']).set_index('date').close
    ref = pd.read_csv(reference_dir / 'daily_paths.csv.gz')
    out = {}
    for product in ['IC', 'IM']:
        life = pd.read_csv(reference_dir / f'{product.lower()}_seller_lifecycle.csv.gz')
        paired.listed.grid_scan.configure_account(reference_dir / 'inputs')
        acc = paired.listed.grid_scan.account_stage
        daily, events, detail = acc.replay(product, 'repeat_roll', source, life, candidates, gov, spot)
        expected = ref.loc[ref['product'].eq(product)].reset_index(drop=True)
        if daily.date.astype(str).tolist() != expected.date.astype(str).tolist():
            raise RuntimeError('unchanged baseline account dates differ')
        fields = ['nav', 'return_net', 'equity_close', 'cash_close', 'margin_reserved', 'option_value', 'etf_value', 'fees_cumulative']
        errors = {k: float(np.nanmax(np.abs(daily[k].to_numpy(float)-expected[k].to_numpy(float)))) for k in fields}
        if errors['nav'] > 1e-10 or errors['return_net'] > 1e-10 or max(errors.values()) > 1e-6:
            raise RuntimeError(f'unchanged baseline mismatch: {product} {errors}')
        out[product] = {'rows': len(daily), 'status': 'PASS', 'max_abs_errors': errors, 'engine_sha256': sha(acc.__file__)}
        print(json.dumps({'stage': 'unchanged_baseline', 'product': product, **out[product]}), flush=True)
    (output / 'parity.json').write_text(json.dumps({'status': 'PASS', 'reference': str(reference_dir.relative_to(ROOT)), 'products': out}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return out


def validate_noon_event_marks(raw, events, close='2026-10-22', mark='2026-10-23'):
    """Tie actual marked legs to frozen same-session rows and stable identities."""
    raw = Path(raw)
    result = {'classification': 'same_session_frozen_quote_evidence', 'products': {}}
    master = pd.read_csv(raw / 'sse_510500_standard_put_snapshots.csv.gz', dtype={'security_id': str})
    ic_noon = pd.read_csv(raw / 'sina_510500_puts_noon.csv.gz', dtype={'security_id': str})
    ic_history = pd.read_csv(raw / 'sina_510500_put_histories.csv.gz', dtype={'security_id': str})
    mo_noon = pd.read_csv(raw / 'eastmoney_mo_puts_noon.csv')
    mo_history = pd.read_csv(raw / 'sina_mo_put_histories.csv.gz')
    for product in ['IC', 'IM']:
        current = events.loc[events['product'].eq(product) & events.date.astype(str).eq(mark)]
        trades = current.loc[current.event.eq('trade')]
        if len(trades) and (trades.signal_date.astype(str) != close).any():
            raise RuntimeError('current mark includes a newly invented intraday trading decision')
        marked = current.loc[current.event.eq('mark')]
        futures = pd.read_csv(raw / f'cffex_{product.lower()}_contracts.csv')
        rows = []
        for name in sorted(marked.symbol.dropna().unique()):
            kind, sleeve, contract = str(name).split('|', 2)
            identity = None
            if kind == 'future':
                q = futures.loc[futures.contract.eq(contract) & futures.date.eq(mark)]
                old = futures.loc[futures.contract.eq(contract) & futures.date.eq(close)]
                if len(q) != 1 or len(old) != 1:
                    raise RuntimeError('noon marked future lacks exact dated identity')
                opening, last, previous = float(q.open.iloc[0]), float(q.close.iloc[0]), float(old.close.iloc[0])
                stamp = mark + ' 11:30:00'
                multiplier = 200.
            elif kind == 'option' and product == 'IC':
                identities = master.loc[master.contract_id.eq(contract) & master.date.isin([close, mark]), ['date', 'security_id']]
                if len(identities) != 2 or identities.security_id.nunique() != 1:
                    raise RuntimeError('held IC security identity changed across last close and frozen noon')
                identity = str(identities.security_id.iloc[0])
                q = ic_noon.loc[ic_noon.security_id.eq(identity) & ic_noon.date.eq(mark)]
                old = ic_history.loc[ic_history.security_id.eq(identity) & ic_history.date.eq(close)]
                if len(q) != 1 or len(old) != 1 or float(q.volume.iloc[0]) <= 0:
                    raise RuntimeError('held IC option lacks unique active current-session quote')
                opening, last, previous = float(q.open.iloc[0]), float(q.close.iloc[0]), float(old.close.iloc[0])
                stamp = str(q.source_stamp.iloc[0])
                multiplier = 10000.
            elif kind == 'option' and product == 'IM':
                q = mo_noon.loc[mo_noon.instrument.eq(contract) & mo_noon.date.eq(mark)]
                old = mo_history.loc[mo_history.instrument.eq(contract) & mo_history.date.eq(close)]
                if len(q) != 1 or len(old) != 1 or float(q.volume.iloc[0]) <= 0:
                    raise RuntimeError('held MO option lacks unique active current-session quote')
                opening, last, previous = float(q.openprice.iloc[0]), float(q.lastprice.iloc[0]), float(old.lastprice.iloc[0])
                stamp = str(q.source_date.iloc[0]) + ' ' + str(q.source_time.iloc[0])
                multiplier = 100.
            else:
                raise RuntimeError(f'unhandled noon marked asset: {name}')
            observed = pd.Timestamp(stamp)
            if observed.date().isoformat() != mark or observed > pd.Timestamp(mark + ' 11:30:00'):
                raise RuntimeError('marked contract has stale or afternoon quote stamp')
            if not np.isfinite([opening, last, previous]).all() or min(opening, last, previous) <= 0:
                raise RuntimeError('marked contract has nonpositive or nonfinite open/last/prior price')
            entry = {'symbol': name, 'security_id': identity, 'open': opening, 'last': last,
                     'previous_close': previous, 'source_stamp': str(observed)}
            # With no current-day trades, independently reconcile the complete
            # close-to-noon position PnL rather than merely checking availability.
            if trades.empty:
                quantity = float(events.loc[events['product'].eq(product) & events.date.astype(str).le(close) & events.symbol.eq(name), 'quantity_change'].sum())
                expected = quantity * multiplier * (last-previous)
                actual = float(marked.loc[marked.symbol.eq(name), 'equity_change'].sum())
                error = abs(actual-expected)
                if error > 5e-6:
                    raise RuntimeError(f'frozen quote/account PnL differs: {name} {error}')
                entry.update(held_quantity=quantity, expected_mark_pnl=expected, actual_mark_pnl=actual, max_abs_error=error)
            rows.append(entry)
        result['products'][product] = {'mark_legs': rows, 'noon_trades': len(trades),
            'cash_interest': float(current.loc[current.event.eq('cash_interest'), 'equity_change'].sum())}
    return result
