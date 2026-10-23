"""Original-session input capture/replay. This never advances the ledger.

Provider values are stored with raw HTTP responses where available. Replaying a
value still runs date/identity/numeric checks, and the normal policy and store
remain responsible for accepting a signal. No files are created on import.
"""
from __future__ import annotations

import contextvars
import json
import math
import os
import re
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime
from functools import wraps
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

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
        return capture.call(role, key, lambda: _validate_value(
            strategy, role, key, original(*args, **kwargs)))

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
           lambda expiry_ym, reference_day: {"month": str(expiry_ym)[-4:], "day": reference_day})
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


def load_original(strategy, root, product, day):
    roles = {"market.live", "cffex.quotes", "rate.resolve", "fear.score"}
    if product == "IC":
        roles |= {"ic.months", "ic.etf", "ic.chain"}
    reader = evidence.load_session(root, day, required_roles=roles, metadata_filter={
        "product": product, "input_revision": INPUT_REVISION, "original_close": True})
    clock = reader.decision_at.astimezone(strategy.BEIJING)
    closed = reader.closed_at.astimezone(strategy.BEIJING)
    # A late historical download is not an original-day snapshot. Both clocks
    # are retained, and the actual capture finish is used without backdating.
    if closed.date() != day or clock.date() != day or closed < clock:
        raise evidence.EvidenceUnavailable(f"{day} original close acquisition time is not on that session")
    if closed.time() < strategy.time(15, 0):
        raise evidence.EvidenceUnavailable(f"{day} original evidence is not a completed close")
    return reader, closed


def _record_http_conflicts(session):
    """Mark ambiguous independent evidence without interrupting signal queries.

    Named provider inputs normally coalesce repeated calls. If raw requests
    still repeat, use the reader's strict body/status/encoding/header identity;
    recording both observations must not advertise a complete recovery pack.
    """
    seen, reported = {}, set()
    def canonical(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    for row in session.raw:
        request = (row["url"], canonical(row["params"]))
        response = (row["sha256"], row.get("status_code"), row.get("encoding"),
                    canonical(row.get("headers", {})))
        if request in seen and seen[request] != response and request not in reported:
            session.failures.append({"role": "raw_http_conflict", "url": row["url"],
                "error": "同一URL/参数录得不同响应，原日存证不完整；正常查询独立运行，采集可重试",
                "resolved": False})
            reported.add(request)
        seen.setdefault(request, response)


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
                    # Per-product budget stays bounded even after a blocked
                    # ledger has accumulated many missed sessions.
                    with strategy._network_budget(strategy.SIGNAL_PRODUCT_NETWORK_BUDGET_SECONDS):
                        def attempt(fetcher):
                            failures_before = len(session.failures)
                            try:
                                return fetcher()
                            except Exception as exc:
                                # Each explicit adapter has already recorded
                                # its failed role. Keep the bounded partial
                                # evidence rather than discard other feeds.
                                if len(session.failures) == failures_before:
                                    session.failures.append({"role": "collector", "error":
                                        f"{type(exc).__name__}: {exc}", "resolved": False})
                                return None

                        # Original listing/chain snapshots cannot be obtained
                        # from tomorrow's current feeds. Prioritize these over
                        # index histories that have dated historical sources.
                        mo_frame = None
                        if product == "IC":
                            months = attempt(getattr(strategy, "fetch_510500_expiries_with_failover",
                                                     strategy.fetch_sse_510500_expiries)) or []
                            for month in months:
                                attempt(lambda month=month: strategy._fetch_510500_chain_for_day(month[-4:], day))
                            attempt(getattr(strategy, "fetch_510500_quote_with_failover",
                                            strategy.fetch_sse_510500_quote))
                        else:
                            mo_frame = attempt(lambda: strategy.fetch_cffex_quotes("MO", clock))
                        attempt(lambda: strategy.fetch_cffex_quotes(product, clock))
                        attempt(lambda: strategy.live_proxy(product, clock))
                        fear_value = attempt(lambda: getattr(strategy, "_fear_score_evidence", strategy.fear_grid.fetch_score)(day, "close"))
                        if hasattr(strategy, "_fear_reference_evidence"):
                            attempt(lambda: strategy._fear_reference_evidence(day))
                        attempt(lambda: strategy._gov10y_for_day(product, day))
                        if mo_frame is not None and mo_frame.attrs.get("source") == "东方财富":
                            attempt(lambda: strategy._prefetch_mo_replay_verification(mo_frame, clock))
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
