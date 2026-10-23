"""Original-session input capture/replay. This never advances the ledger.

Provider values are stored with raw HTTP responses where available. Replaying a
value still runs date/identity/numeric checks, and the normal policy and store
remain responsible for accepting a signal. No files are created on import.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import math
import os
import re
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, time
from functools import wraps
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

import ic_im_signal_evidence_v1 as evidence

INPUT_REVISION = "ic_im_original_inputs_20261021_v1"
_LOCATION = contextvars.ContextVar("icim_original_evidence_location", default=None)


class Namespace:
    """Support native script runtimes which do not register sys.modules."""
    def __init__(self, values):
        object.__setattr__(self, "_values", values)

    def __getattr__(self, name):
        try:
            return self._values[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self._values[name] = value


@contextmanager
def evidence_location(root):
    token = _LOCATION.set(Path(root) if root is not None else None)
    try:
        yield
    finally:
        _LOCATION.reset(token)


def configured_root():
    return _LOCATION.get() or (Path(os.environ["ICIM_EVIDENCE_DIR"])
                               if os.environ.get("ICIM_EVIDENCE_DIR") else None)


def _number(value, label, *, positive=True):
    if isinstance(value, (bool, np.bool_)):
        raise evidence.EvidenceUnavailable(label + " cannot be boolean")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise evidence.EvidenceUnavailable(label + " is not numeric") from exc
    if not math.isfinite(result) or (result <= 0 if positive else result < 0):
        raise evidence.EvidenceUnavailable(label + " is not a valid price/count")
    return result


def validate_chain(strategy, value, month, day, clock):
    if not isinstance(value, tuple) or len(value) != 2:
        raise evidence.EvidenceUnavailable(f"{day} malformed original ic.chain/{month}")
    frame, stamp = value
    columns = ["contract", "last", "chg_rate", "pre_settle", "strike", "security_id"]
    if not isinstance(frame, pd.DataFrame) or not set(columns).issubset(frame.columns):
        raise evidence.EvidenceUnavailable(f"{day} original chain lacks identity/price fields")
    rows = frame[columns].astype(object).values.tolist()
    strategy.sse_identity.validate_chain_rows(
        {"list": rows, "total": len(rows), "begin": 0, "end": len(rows)}, str(month)[-4:])
    strategy._validate_chain_stamp_matches("原日期权链存证", stamp, day, clock)
    # Keep stale per-contract rows visible; only a consumed row must meet the
    # selected-quote freshness gate, just as on the live provider path.
    return frame.copy(deep=True), deepcopy(stamp)


def _validate_value(strategy, role, key, value):
    reader = evidence.current_reader.get()
    capture = evidence.current_session.get()
    if reader is None and capture is None:
        return value
    day, clock = (reader.day if reader is not None else capture.day), strategy._now_beijing()
    if reader is None and hasattr(strategy, "_iv_observation_clock"):
        clock = strategy._iv_observation_clock(clock)
    if role == "ic.chain":
        return validate_chain(strategy, value, key["month"], day, clock)
    if role == "ic.months":
        if (not isinstance(value, list) or not value or value != sorted(set(value))
                or any(not isinstance(m, str) or not re.fullmatch(r"20[0-9]{4}", m)
                       or not 1 <= int(m[-2:]) <= 12 for m in value)):
            raise evidence.EvidenceUnavailable(f"{day} invalid original listing months")
    elif role == "ic.etf":
        strategy._validate_chain_stamp_matches("原日ETF存证", value, day, clock)
        _number(value["last"], "ETF last")
        _number(value["prev_close"], "ETF previous close")
    elif role == "market.live":
        if value.get("history_date") != day:
            raise evidence.EvidenceUnavailable(f"{day} original index/valuation date mismatch")
        _number(value.get("price"), "index price")
        if not value.get("valuation_provenance"):
            raise evidence.EvidenceUnavailable(f"{day} original valuation provenance missing")
    elif role == "rate.resolve":
        if (not isinstance(value, dict) or value.get("revision") != strategy.chinabond.REVISION
                or value.get("source") != strategy.chinabond.SOURCE
                or type(value.get("yield_decimal")) not in (int, float)
                or not math.isfinite(value["yield_decimal"])
                or not -0.05 <= value["yield_decimal"] <= 0.30):
            raise evidence.EvidenceUnavailable("original rate value/source/revision invalid")
        if value.get("mode") == "frozen_fallback":
            if value.get("used_date") != key["anchor_day"].isoformat() or value["yield_decimal"] != key["fallback"]:
                raise evidence.EvidenceUnavailable("original frozen rate differs from its bound fallback")
        elif value.get("mode") == "official_actual":
            fetched = datetime.fromisoformat(value.get("fetched_at", ""))
            publication = datetime.combine(day, strategy.time(17, 30), strategy.BEIJING)
            if (value.get("used_date") != day.isoformat() or value.get("observed_date") != day.isoformat()
                    or fetched.tzinfo is None or not publication <= fetched <= clock
                    or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("page_sha256", "")))):
                raise evidence.EvidenceUnavailable("original official rate date/publication evidence invalid")
        else:
            raise evidence.EvidenceUnavailable("original rate mode invalid")
    elif role == "cffex.quotes":
        if not isinstance(value, pd.DataFrame) or value.empty:
            raise evidence.EvidenceUnavailable(f"{day} original CFFEX frame missing")
        if value.attrs.get("source_date") != day or any(d != day for d in value["source_date"]):
            raise evidence.EvidenceUnavailable(f"{day} original CFFEX source date mismatch")
        product, source = key["product"], str(value.attrs.get("source", ""))
        instruments = value["instrument"].tolist()
        pattern = rf"{product}[0-9]{{4}}" if product != "MO" else r"MO[0-9]{4}-[CP]-[0-9]+"
        if len(set(instruments)) != len(instruments) or any(not re.fullmatch(pattern, c) for c in instruments):
            raise evidence.EvidenceUnavailable("original CFFEX identities invalid")
        for name in ("lastprice", "volume", "position"):
            for number in value[name]:
                _number(number, name, positive=name == "lastprice")
                if name != "lastprice" and float(number) != math.floor(float(number)):
                    raise evidence.EvidenceUnavailable("original CFFEX fractional count")
        listing = value.attrs.get("listed_instruments", instruments)
        if product == "MO":
            strategy._validate_complete_mo_chain(value, day, source, pd.DataFrame({"instrument": listing}))
        else:
            strategy._validate_complete_future_structure(value, product, day, source, set(listing))
        if source in {"新浪财经", "东方财富"}:
            strategy.quote_snapshot_guard.vendor_snapshot_evidence(
                value.to_dict("records"), clock, day, "原日" + source + " " + product)
    return value


def _keyed(strategy, owner, name, role, keyfn):
    original = getattr(owner, name)

    @wraps(original)
    def adapted(*args, **kwargs):
        reader, capture = evidence.current_reader.get(), evidence.current_session.get()
        if reader is None and capture is None:
            return original(*args, **kwargs)
        key = keyfn(*args, **kwargs)
        if reader is not None:
            if role == "fear.reference" and not any(
                    row.get("role") == role for row in reader.manifest["entries"].values()):
                return {"jiu_reference_only": True, "jiu_status": "unavailable",
                        "jiu_fear_greed_index": None, "jiu_data_date": None,
                        "jiu_warning": "原日未归档参考源，回放不重新联网"}
            value = _validate_value(strategy, role, key, reader.call(role, key))
            if role == "fear.score" and key.get("mode") == "close" and value.get("fear_greed_index") is None:
                return _historical_fear_supplement(strategy, reader, key, value, original)
            return value
        value = capture.call(role, key, lambda: _validate_value(
            strategy, role, key, original(*args, **kwargs)))
        # capture.call coalesces a role/key before reaching the query cache.
        # Every chain consumption must still recheck its original timestamps.
        return _validate_value(strategy, role, key, value) if role == "ic.chain" else value

    setattr(owner, name, adapted)


def _checked_historical_fear(strategy, value, day):
    if not isinstance(value, dict) or value.get("fear_greed_index") is None:
        raise evidence.EvidenceUnavailable(f"{day} 历史恐慌数据仍未取得；该日网格继续未确认，需historical_replay补录")
    _number(value["fear_greed_index"], "historical Fear value", positive=False)
    if not strategy.fear_grid.flexible_source(value):
        raise evidence.EvidenceUnavailable("historical Fear supplement requires the approved source revision")
    actual = datetime.now(strategy.BEIJING)
    strategy.fear_grid.validate_source_evidence({**value, "market_date": day, "close_confirmed": True,
        "signal_generated_at": actual.isoformat(), "signal_generation_timing": "retrospective_replay"},
        validation_clock=actual)
    return value


def _historical_fear_supplement(strategy, reader, key, original_value, original):
    """Recover a missing grid input through the approved historical provider.

    Never replace an originally usable T or T-1 observation. The supplementary
    evidence stays separate and uses the real later source/retrieval clock.
    """
    day = key["day"]
    metadata = {"capture_kind": "historical_fear_supplement", "input_revision": INPUT_REVISION,
                "original_close": False}
    try:
        try:
            supplement = evidence.load_session(reader.root, day, required_roles={"fear.score"}, metadata_filter=metadata)
            value = _checked_historical_fear(strategy, supplement.call("fear.score", key), day)
        except evidence.EvidenceUnavailable:
            token = evidence.current_reader.set(None)
            try:
                with evidence.capture_session(reader.root, day, reader.decision_at, metadata) as supplement:
                    value = supplement.call("fear.score", key, lambda: _checked_historical_fear(
                        strategy, original(day, "close"), day))
            finally:
                evidence.current_reader.reset(token)
        supplements = getattr(reader, "supplemental_evidence", [])
        supplements.append({"role": "fear.score", "price_day": day.isoformat(),
                            "manifest_sha256": supplement.manifest_sha256, "original_close": False,
                            "retrieved_at": value["fear_retrieved_at"]})
        reader.supplemental_evidence = supplements
        return {**value, "fear_replay_recovery": "historical_supplement"}
    except Exception as exc:
        return {**original_value, "fear_replay_recovery": "unavailable",
                "fear_unavailable_reason": f"{day}历史恐慌补证失败，需对该日执行historical_replay；{type(exc).__name__}: {exc}"}


def _chain_close(strategy, security_id, day):
    """A same-day chain binds security identity independently of the request."""
    reader = evidence.current_reader.get()
    matches = []
    for entry in reader.manifest["entries"].values():
        if entry.get("role") != "ic.chain":
            continue
        key = evidence.decode(entry["key"])
        frame, stamp = validate_chain(strategy, reader.call("ic.chain", key), key["month"], day,
                                       strategy._now_beijing())
        rows = frame[frame["security_id"].eq(str(security_id))]
        matches.extend((row, stamp) for _, row in rows.iterrows())
    if len(matches) != 1:
        raise evidence.EvidenceUnavailable(f"{day} original chain identity unavailable for {security_id}")
    row, stamp = matches[0]
    strategy._require_selected_510500_quote_time(row, strategy._now_beijing(), day)
    _number(row["last"], "original option close", positive=False)
    return row, stamp


def _checked_open(value, day):
    if not isinstance(value, tuple) or len(value) != 2 or value[1] not in {"Sina:dated_open", "Eastmoney:dated_open"}:
        raise evidence.EvidenceUnavailable(f"{day} opening supplement has no verified dated provider")
    _number(value[0], "dated option opening")
    return value


def _dated_open_source(strategy, original):
    @wraps(original)
    def opening(security_id, day):
        key = {"security_id": str(security_id), "day": day}
        reader = evidence.current_reader.get()
        if reader is None:
            session = evidence.current_session.get()
            return session.call("option.open", key, lambda: _checked_open(original(security_id, day), day)) if session else original(security_id, day)
        present = any(entry.get("role") == "option.open" and evidence.decode(entry["key"]) == key
                      for entry in reader.manifest["entries"].values())
        if present:
            value = reader.call("option.open", key)
        else:
            # A public dated daily bar can supplement the opening evidence.
            # This is explicitly retrospective, unlike the original listing
            # and close chain, which must never be fetched from today's feed.
            metadata = {"capture_kind": "dated_open_supplement", "security_id": str(security_id),
                        "input_revision": INPUT_REVISION, "original_close": False}
            try:
                supplement = evidence.load_session(reader.root, day, required_roles={"option.open"},
                                                   metadata_filter=metadata)
                value = supplement.call("option.open", key)
            except evidence.EvidenceUnavailable:
                token = evidence.current_reader.set(None)
                try:
                    with evidence.capture_session(reader.root, day, strategy._now_beijing(), metadata) as supplement:
                        value = supplement.call("option.open", key, lambda: _checked_open(original(security_id, day), day))
                finally:
                    evidence.current_reader.reset(token)
            supplements = getattr(reader, "supplemental_evidence", [])
            supplements.append({"role": "option.open", "price_day": day.isoformat(),
                                "security_id": str(security_id), "manifest_sha256": supplement.manifest_sha256,
                                "original_close": False, "retrieved_at":
                                (supplement.closed_at.isoformat() if hasattr(supplement, "closed_at")
                                 else supplement.context["closed_at"])})
            reader.supplemental_evidence = supplements
        return _checked_open(value, day)
    return opening


def canonical_ic_month(value):
    month = str(value)
    if not re.fullmatch(r"(?:20)?[0-9]{4}", month) or not 1 <= int(month[-2:]) <= 12:
        raise ValueError(f"非法ETF期权月份: {value}")
    return month[-4:]


def install(strategy):
    """Wrap only explicit public adapters; never patch requests globally."""
    if getattr(strategy, "_original_evidence_installed", False):
        return
    _keyed(strategy, strategy, "live_proxy", "market.live", lambda product, clock=None: {"product": product})
    _keyed(strategy, strategy, "fetch_cffex_quotes", "cffex.quotes", lambda product, clock=None: {"product": product})
    months_name = ("fetch_510500_expiries_with_failover" if hasattr(strategy, "fetch_510500_expiries_with_failover")
                   else "fetch_sse_510500_expiries")
    quote_name = ("fetch_510500_quote_with_failover" if hasattr(strategy, "fetch_510500_quote_with_failover")
                  else "fetch_sse_510500_quote")
    _keyed(strategy, strategy, months_name, "ic.months", lambda: {})
    _keyed(strategy, strategy, quote_name, "ic.etf", lambda reference_day=None: {})
    _keyed(strategy, strategy, "_fetch_510500_chain_for_day", "ic.chain",
           lambda expiry_ym, reference_day: {"month": canonical_ic_month(expiry_ym), "day": reference_day})
    if hasattr(strategy, "build_iv_warning"):
        _keyed(strategy, strategy, "build_iv_warning", "iv.monitor",
               lambda product, signal, live, quotes, clock, **kwargs:
                   {"product": product, "day": signal["market_date"]})
    _keyed(strategy, strategy, "_fear_score_evidence", "fear.score",
           lambda signal_day, mode: {"day": signal_day, "mode": mode})
    if hasattr(strategy, "_fear_reference_evidence"):
        _keyed(strategy, strategy, "_fear_reference_evidence", "fear.reference", lambda signal_day: {"day": signal_day})
    if hasattr(strategy, "_prefetch_mo_replay_verification"):
        _keyed(strategy, strategy, "_prefetch_mo_replay_verification", "mo.replay_verification",
               lambda quotes, clock: {"day": clock.date(), "product": "MO"})
    _keyed(strategy, strategy, "_resolve_gov10y_evidence", "rate.resolve",
           lambda market_day, clock, frozen_rate, anchor_day: {
               "day": market_day, "fallback": frozen_rate, "anchor_day": anchor_day})
    strategy.fetch_option_dated_open = _dated_open_source(strategy, strategy.fetch_option_dated_open)
    closes = strategy.fetch_option_closes

    @wraps(closes)
    def option_closes(security_id):
        reader = evidence.current_reader.get()
        if reader is not None:
            row, stamp = _chain_close(strategy, security_id, reader.day)
            result = pd.Series([float(row["last"])], index=pd.DatetimeIndex([reader.day]), dtype=float)
            result.attrs.update(source="原日期权链存证：" + str(stamp.get("source", "")), source_failures=[])
            return result
        return closes(security_id)
    strategy.fetch_option_closes = option_closes
    historical = strategy.fetch_sse_existing_put_historical_quote

    @wraps(historical)
    def held_quote(contract, security_id, day):
        if evidence.current_reader.get() is None:
            return historical(contract, security_id, day)
        if not re.fullmatch(r"510500P[0-9]{4}M[0-9]{5}", str(contract)):
            raise evidence.EvidenceUnavailable("adjusted/invalid held identity requires its full migration engine")
        row, stamp = _chain_close(strategy, security_id, day)
        if row["contract"] != contract:
            raise evidence.EvidenceUnavailable(f"{day} original held contract/security identity mismatch")
        expiry = strategy._fourth_wednesday(2000 + int(contract[7:9]), int(contract[9:11]))
        strategy._require_official_exchange_calendar(expiry, "原日持仓Put到期")
        return pd.DataFrame([row]), stamp
    strategy.fetch_sse_existing_put_historical_quote = held_quote
    strategy._original_evidence_installed = True


def has_original(root, product, day):
    return evidence.has_session(root, day, {"product": product, "original_close": True})


def _original_required_roles(product):
    roles = {"market.live", "cffex.quotes", "rate.resolve", "fear.score"}
    if product == "IC":
        roles |= {"ic.months", "ic.etf", "ic.chain"}
    return roles


def _require_original_reader(reader, product, day, zone):
    """Use the same replay-route scope for readiness and actual loading."""
    if (reader.metadata.get("product") != product
            or reader.metadata.get("input_revision") != INPUT_REVISION
            or reader.metadata.get("original_close") is not True):
        raise evidence.EvidenceUnavailable(f"{day} original evidence metadata/revision mismatch")
    roles = {entry["role"] for entry in reader.context["entries"].values()}
    missing = _original_required_roles(product) - roles
    if missing:
        raise evidence.EvidenceUnavailable("missing original evidence roles=" + ",".join(sorted(missing)))
    clock = reader.decision_at.astimezone(zone)
    closed = reader.closed_at.astimezone(zone)
    if closed.date() != day or clock.date() != day or closed < clock:
        raise evidence.EvidenceUnavailable(f"{day} original close acquisition time is not on that session")
    if closed.time() < time(15, 0):
        raise evidence.EvidenceUnavailable(f"{day} original evidence is not a completed close")
    return closed


def _original_session_paths(root, day):
    root = evidence._canonical(Path(root))
    directory = evidence._inside(root, root / "sessions" / day.isoformat())
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError as exc:
        raise evidence.EvidenceUnavailable(f"{day} cannot enumerate original evidence") from exc
    if len(paths) > evidence.MAX_SESSION_FILES:
        raise evidence.EvidenceUnavailable(f"{day} evidence session count exceeds limit")
    return root, paths


def load_original(strategy, root, product, day):
    day = evidence._day(day)
    root, paths = _original_session_paths(root, day)
    choices, failures = [], []
    for path in paths:
        try:
            reader = evidence._load_one(root, path, day)
            # A later packet with an invalid original-session clock must not
            # hide an earlier valid one. Qualify before choosing the newest.
            closed = _require_original_reader(reader, product, day, strategy.BEIJING)
            choices.append((reader, closed))
        except (evidence.EvidenceUnavailable, ValueError, TypeError, KeyError, AttributeError) as exc:
            failures.append(f"{path.name}: {exc}")
    if not choices:
        detail = "; ".join(failures[:4]) or "no complete original session matching requested product"
        raise evidence.EvidenceUnavailable(f"{day} original evidence unavailable: {detail}")
    # Both clocks are retained; use the actual capture finish without backdating.
    return max(choices, key=lambda pair: (pair[0].decision_at, pair[0].closed_at,
                                          pair[0].manifest_path.name))


def _record_http_conflicts(session):
    """The session checks immediately; retain this end-of-collection guard."""
    return session.record_http_conflicts()


def capture_daily(strategy, root, clock):
    """Bounded prospective collector; it deliberately ignores ledger freshness."""
    day = clock.date()
    strategy._require_official_exchange_calendar(day, "原日存证采集")
    if not strategy._is_exchange_trading_day(day) or strategy._market_phase(clock) != "收盘后":
        return {"day": str(day), "status": "waiting_for_close", "products": {}}
    actual = datetime.now(strategy.BEIJING)
    if actual.date() != day:
        raise evidence.EvidenceUnavailable("prospective evidence capture requires the actual current session")
    outcomes = {}
    with strategy.STRATEGY_QUERY_LOCK, strategy.runtime_clock(clock):
        for product in ("IC", "IM"):
            metadata = {"product": product, "input_revision": INPUT_REVISION, "original_close": True,
                        "capture_kind": "independent_daily_inputs", "build": strategy.BUILD_ID,
                        "anchor_day_at_capture": str(strategy.LIVE_CONTINUATION_ANCHOR[product]["last_verified_day"])}
            session = None
            try:
                with evidence.capture_session(root, day, clock, metadata) as session:
                    # Every job inherits this one absolute product deadline;
                    # queued work cannot obtain a fresh budget when it starts.
                    # CaptureSession serializes its own mutations and coalesces
                    # same-role fetches, while independent providers may run.
                    with strategy._network_budget(strategy.SIGNAL_PRODUCT_NETWORK_BUDGET_SECONDS):
                        job_errors, pending, scheduled_months = [], {}, set()
                        fear_value = None
                        guard = getattr(strategy, "_bounded_timeout", None)
                        def run_job(fetcher):
                            for attempt in range(2):
                                if callable(guard):
                                    guard(8)
                                try:
                                    value = fetcher()
                                    if callable(guard):
                                        guard(8)
                                    return value
                                except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError):
                                    if attempt:
                                        raise

                        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="icim-capture") as pool:
                            def submit(label, fetcher):
                                context = contextvars.copy_context()
                                pending[pool.submit(context.run, run_job, fetcher)] = label

                            def submit_month(month):
                                value = str(month)[-4:]
                                if value not in scheduled_months:
                                    scheduled_months.add(value)
                                    submit("ic.chain/" + value,
                                           lambda value=value: strategy._fetch_510500_chain_for_day(value, day))

                            if product == "IC":
                                # The old ledger can be frozen across several
                                # expiries. Use held identities only to order
                                # CURRENT observed months, never to demand a
                                # now-delisted chain from a current quote feed.
                                anchor = strategy.LIVE_CONTINUATION_ANCHOR[product]
                                held_months = set()
                                for field in ("v14_core_put_contract", "post_put_contract", "v14_short_put_contract"):
                                    match = re.fullmatch(r"510500P([0-9]{4})[MA][0-9]{5}", str(anchor.get(field) or ""))
                                    if match:
                                        held_months.add(match.group(1))
                                submit("ic.months", getattr(strategy, "fetch_510500_expiries_with_failover",
                                                            strategy.fetch_sse_510500_expiries))
                                submit("ic.etf", getattr(strategy, "fetch_510500_quote_with_failover",
                                                         strategy.fetch_sse_510500_quote))
                            else:
                                def collect_mo():
                                    frame = strategy.fetch_cffex_quotes("MO", clock)
                                    if frame is not None and getattr(frame, "attrs", {}).get("source") == "东方财富":
                                        if callable(guard):
                                            guard(8)
                                        strategy._prefetch_mo_replay_verification(frame, clock)
                                    return frame
                                submit("mo.quotes_and_verification", collect_mo)
                            submit("cffex.quotes", lambda: strategy.fetch_cffex_quotes(product, clock))
                            submit("market.live", lambda: strategy.live_proxy(product, clock))
                            submit("fear.score", lambda: getattr(strategy, "_fear_score_evidence",
                                                                  strategy.fear_grid.fetch_score)(day, "close"))
                            submit("rate.resolve", lambda: strategy._gov10y_for_day(product, day))
                            if hasattr(strategy, "_fear_reference_evidence"):
                                submit("fear.reference", lambda: strategy._fear_reference_evidence(day))
                            while pending:
                                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                                for future in done:
                                    label = pending.pop(future)
                                    try:
                                        value = future.result()
                                    except Exception as exc:
                                        job_errors.append((label, exc))
                                        continue
                                    if label == "ic.months":
                                        for month in sorted(value or [], key=lambda item: (
                                                str(item)[-4:] not in held_months, str(item))):
                                            submit_month(month)
                                    elif label == "fear.score":
                                        fear_value = value
                        if callable(guard):
                            guard(8)
                        # All writers have finished before checking errors or
                        # sealing the session. Compare exception identity, not
                        # a racy before/after count of unrelated job failures.
                        for label, exc in job_errors:
                            if not any(recorded is exc for recorded in session._recorded_exceptions):
                                session.failures.append({"role": "collector", "job": label, "resolved": False,
                                                         "error": f"{type(exc).__name__}: {exc}"})
                    _record_http_conflicts(session)
                    present_roles = {entry["role"] for entry in session.entries.values()}
                    required_roles = {"market.live", "cffex.quotes", "fear.score", "rate.resolve"}
                    if product == "IC":
                        required_roles |= {"ic.months", "ic.etf", "ic.chain"}
                    if required_roles - present_roles:
                        session.failures.append({"role": "collector", "resolved": False,
                            "error": "missing roles: " + ",".join(sorted(required_roles - present_roles))})
                    session.annotate("replay_completeness", {
                        "collection_path": "independent_of_signal_and_calendar_decision",
                        "parallel_workers": 4,
                        "required_roles": sorted(required_roles), "present_roles": sorted(present_roles),
                        "missing_roles": sorted(required_roles - present_roles),
                        "input_capture_complete": not any(row.get("resolved") is not True for row in session.failures),
                        "historical_supplements_needed": ["fear.score"] if isinstance(fear_value, dict)
                            and fear_value.get("fear_greed_index") is None else [],
                        "state_transition_validated": False,
                        "note": "原日输入快照完整性不等于正式信号或账本资格；缺值可能仍需后日补证"})
                complete = session.context.get("complete") is True
                outcomes[product] = {"status": "captured" if complete else "incomplete",
                                     "manifest": str(session.manifest_path), "sha256": session.manifest_sha256}
                if not complete:
                    outcomes[product]["reason"] = "; ".join(
                        str(row.get("role")) + ": " + str(row.get("error")) for row in session.context["failures"]
                        if row.get("resolved") is not True)
                    outcomes[product]["recovery_warning"] = f"{day}原日输入不完整；补齐日历后仍可能需人工锚定，不能承诺自动恢复"
            except Exception as exc:
                outcomes[product] = {"status": "incomplete", "reason": f"{type(exc).__name__}: {exc}",
                                     "manifest": str(session.manifest_path) if session and session.manifest_path else None}
    return {"day": str(day), "status": "captured" if all(
        v["status"] == "captured" for v in outcomes.values()) else "incomplete", "products": outcomes}


def capture_day_status(root, day):
    """Read sealed attempts, not successful query counts or polling receipts."""
    root, paths = _original_session_paths(root, day)
    products = {p: {"attempts": 0, "complete_attempts": 0, "incomplete_attempts": 0,
                    "conflict_attempts": 0, "conflict_requests": 0} for p in ("IC", "IM")}
    errors, unavailable = [], []
    zone = ZoneInfo("Asia/Shanghai")
    for path in paths:
        try:
            envelope = evidence._loads(evidence._read(evidence._inside(root, path), evidence.MAX_MANIFEST_BYTES))
            body = envelope["body"]
            if (hashlib.sha256(evidence._dumps(body)).hexdigest() != envelope["sha256"]
                    or body.get("schema") != evidence.SCHEMA or body.get("day") != str(day)):
                raise evidence.EvidenceUnavailable("evidence manifest scope/hash invalid")
            metadata = evidence.decode(body["metadata"])
            product = metadata.get("product")
            if metadata.get("original_close") is not True or product not in products:
                continue
            counts = products[product]
            counts["attempts"] += 1
            conflicts = evidence.raw_http_conflicts(body.get("raw", []))
            conflicted = bool(conflicts) or any(x.get("role") == "raw_http_conflict" for x in body.get("failures", []))
            counts["conflict_attempts"] += int(conflicted)
            counts["conflict_requests"] += len(conflicts)
            try:
                reader = evidence._load_one(root, path, day)
                _require_original_reader(reader, product, day, zone)
                counts["complete_attempts"] += 1
            except evidence.EvidenceUnavailable as exc:
                counts["incomplete_attempts"] += 1
                unavailable.append({"manifest": path.name, "error": str(exc)[:500]})
        except (OSError, ValueError, TypeError, KeyError, AttributeError, evidence.EvidenceUnavailable) as exc:
            errors.append({"manifest": path.name, "error": str(exc)[:500]})
    for value in products.values():
        value["conflict_frequency"] = value["conflict_attempts"] / value["attempts"] if value["attempts"] else None
    # A cycle can fail before any session is published. These receipts are
    # advisory failure evidence, never a substitute for a valid original pack
    # and never part of the sealed-session statistics denominator.
    failed_receipts, receipt_errors = [], []
    receipt_paths = sorted((root / "capture_receipts" / day.isoformat()).glob("*.json"))
    if len(receipt_paths) > evidence.MAX_SESSION_FILES:
        raise evidence.EvidenceUnavailable("evidence capture receipt count exceeds limit")
    for path in receipt_paths:
        try:
            value = evidence._loads(evidence._read(evidence._inside(root, path), evidence.MAX_MANIFEST_BYTES))
            if (not isinstance(value, dict) or value.get("schema") != "icim_capture_runner_receipt_v1"
                    or value.get("status") not in {"incomplete", "source_pin_mismatch"}):
                continue
            started = datetime.fromisoformat(value["started_at"])
            if started.tzinfo is None or started.astimezone(zone).date() != day:
                raise evidence.EvidenceUnavailable("capture receipt day/time mismatch")
            capture = value.get("capture", {})
            if not isinstance(capture, dict) or capture.get("day", str(day)) != str(day):
                raise evidence.EvidenceUnavailable("capture receipt scope mismatch")
            failed_receipts.append({"receipt": path.name, "status": value["status"],
                                    "started_at": value["started_at"]})
        except (OSError, ValueError, TypeError, KeyError, AttributeError, evidence.EvidenceUnavailable) as exc:
            receipt_errors.append({"receipt": path.name, "error": str(exc)[:500]})
    missing = [p for p, c in products.items() if not c["complete_attempts"]]
    attempted = bool(paths or failed_receipts or receipt_errors)
    return {"day": str(day), "status": "captured" if not missing else "incomplete" if attempted else "not_recorded",
            "products": products, "missing_products": missing, "unreadable_manifests": errors,
            "unavailable_originals": unavailable, "failed_cycle_receipts": failed_receipts,
            "unreadable_receipts": receipt_errors,
            "denominator": "sealed original-close sessions per product; polling receipts excluded",
            "recovery_warning": (f"{day} 当日原日存证缺失（{'/'.join(missing)}）；本轮重试后仍未取得完整包，需另行补证或人工锚定，不能承诺自动恢复。"
                                 if missing and attempted else None)}
