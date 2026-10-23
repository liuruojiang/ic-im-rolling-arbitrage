"""Read-only, dated IC option terms and cash-basis migration primitives.

These objects do not enable the production A-contract gate or authorize ledger
writes. Identity terms, daily availability and an actual entry cost are three
different kinds of evidence and must not be substituted for one another.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import re
from typing import Any

import ic_im_sse_contract_identity_v1 as identity

REVISION = "ic_adjusted_put_cash_basis_preview_20261021_v1"
MASTER_SQL = "SSE_ZQPZ_YSP_GGQQZSXT_XXPL_DRHY_SEARCH_L"
CODE = re.compile(r"510500([CP])([0-9]{4})([MA])([0-9]{5})")


def day(value: Any) -> date:
    if type(value) is date:
        return value
    # SSE's quote date is an eight-digit JSON integer, unlike TIMESAVE.
    if type(value) is int and 10000000 <= value <= 99999999:
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{8}", value):
        raise RuntimeError("dated terms require an exact ASCII calendar date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise RuntimeError("invalid dated terms date") from exc


def number(value: Any, *, positive: bool = False, integer: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise RuntimeError("cash basis numeric value invalid")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise RuntimeError("cash basis numeric value invalid") from exc
    if (not result.is_finite() or (result <= 0 if positive else result < 0)
            or integer and result != result.to_integral_value()):
        raise RuntimeError("cash basis numeric domain invalid")
    return result


def payload_hash(payload: dict) -> str:
    """Canonical semantic hash; callers separately retain the raw-file hash."""
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class Terms:
    security_id: str
    contract: str
    strike: Decimal
    unit: int
    valid_from: date
    valid_through: date
    source_hash: str
    expiry: date
    availability_day: date | None = None

    def __post_init__(self):
        match = CODE.fullmatch(self.contract) if isinstance(self.contract, str) else None
        if (not match or not isinstance(self.security_id, str)
                or not re.fullmatch(r"[0-9]{8}", self.security_id)
                or not isinstance(self.source_hash, str)
                or not re.fullmatch(r"[0-9a-f]{64}", self.source_hash)):
            raise RuntimeError("dated terms identity/provenance invalid")
        if (type(self.unit) is not int or self.unit <= 0
                or type(self.valid_from) is not date or type(self.valid_through) is not date
                or type(self.expiry) is not date or self.valid_from > self.valid_through
                or self.valid_through > self.expiry
                or self.expiry.year != 2000+int(match.group(2)[:2])
                or self.expiry.month != int(match.group(2)[2:])):
            raise RuntimeError("dated terms interval/unit invalid")
        if not isinstance(self.strike, Decimal) or number(self.strike, positive=True) != self.strike:
            raise RuntimeError("dated terms strike invalid")
        if match.group(3) == "M" and (self.unit != 10000 or self.strike != Decimal(match.group(4))/1000):
            raise RuntimeError("standard terms strike/unit conflict")
        if self.availability_day is not None and (type(self.availability_day) is not date
                or not self.valid_from <= self.availability_day <= self.valid_through):
            raise RuntimeError("dated availability outside terms interval")

    def on(self, value: Any) -> "Terms":
        reference = day(value)
        if not self.valid_from <= reference <= self.valid_through:
            raise RuntimeError("identity outside proven terms interval")
        return self

    def require_available(self, value: Any) -> "Terms":
        self.on(value)
        if self.availability_day != day(value):
            raise RuntimeError("terms do not prove daily listing/availability")
        return self

    def json(self) -> dict:
        return {key: (str(value) if isinstance(value, (Decimal, date)) else value)
                for key, value in asdict(self).items()}


def adjustment_terms(payload: dict, *, effective_date: Any) -> dict[str, tuple[Terms, Terms]]:
    """Resolve one official M->A event, without inventing daily availability."""
    events = identity.parse_adjustment_events(payload, effective_date=day(effective_date))
    origin = {row["SECURITY_ID"]: row for row in payload["result"]}
    digest = payload_hash(payload)
    result = {}
    for event in events:
        raw = origin[event["security_id"]]
        common = dict(security_id=event["security_id"], source_hash=digest, expiry=event["expiry"])
        before = Terms(**common, contract=event["previous_contract"],
                       strike=number(raw["ORG_EXERCISE_PRICE"], positive=True),
                       unit=event["previous_unit"], valid_from=day(raw["START_DATE"]),
                       valid_through=event["effective_date"]-timedelta(days=1))
        after = Terms(**common, contract=event["contract"],
                      strike=number(raw["EXERCISE_PRICE"], positive=True), unit=event["contract_unit"],
                      valid_from=event["effective_date"], valid_through=event["expiry"])
        # A date on/after adjustment must never be resolved to the old M alias.
        result[event["security_id"]] = (before, after)
    return result


def master_terms(payload: dict, *, as_of: Any) -> dict[str, Terms]:
    """A current master proves only its actual TIMESAVE, not requested queryDate."""
    reference = day(as_of)
    if (not isinstance(payload, dict) or payload.get("sqlId") != MASTER_SQL
            or not (payload.get("isPagination") is False or payload.get("isPagination") == "false")
            or type(payload.get("actionErrors")) is not list
            or type(payload.get("fieldErrors")) is not dict
            or payload["actionErrors"] or payload["fieldErrors"]
            or not isinstance(payload.get("result"), list) or not payload["result"]):
        raise RuntimeError("official master scope/errors/schema unverified")
    digest, result, contracts = payload_hash(payload), {}, set()
    for row in payload["result"]:
        try:
            if not isinstance(row, dict):
                raise RuntimeError("official master row invalid")
            contract, security = row["CONTRACT_ID"], row["SECURITY_ID"]
            match = CODE.fullmatch(contract) if isinstance(contract, str) else None
            if (not match or row["CALL_OR_PUT"] != {"C": "认购", "P": "认沽"}[match.group(1)]
                    or row["OPTION_TYPE"] != "E" or row["DELISTFLAG"] != "否"
                    or day(row["TIMESAVE"]) != reference):
                raise RuntimeError("official master date/identity/listing mismatch")
            expiry = day(row["EXPIRE_DATE"])
            if day(row["EXERCISE_DATE"]) != expiry or day(row["END_DATE"]) != expiry:
                raise RuntimeError("official master expiry mismatch")
            value = Terms(security, contract, number(row["EXERCISE_PRICE"], positive=True),
                          int(number(row["CONTRACT_UNIT"], positive=True, integer=True)),
                          # For A, START_DATE is its original security listing,
                          # not the effective date of the adjusted terms.
                          reference, reference, digest, expiry, reference)
            if day(row["START_DATE"]) > reference or security in result or contract in contracts:
                raise RuntimeError("official master duplicate/listing date invalid")
            contracts.add(contract)
            result[security] = value
        except (KeyError, TypeError) as exc:
            raise RuntimeError("official master fields missing") from exc
    return result


def bind_daily_quote(terms: Terms, payload: dict, *, as_of: Any) -> Terms:
    """Bind identity terms to a saved daily quote; no close-confirmation claim."""
    reference = day(as_of)
    terms.on(reference)
    if not isinstance(payload, dict) or day(payload.get("date")) != reference:
        raise RuntimeError("daily quote date mismatch")
    rows = identity.validate_chain_rows(payload, terms.contract[7:11])
    found = [row for row in rows if row[5] == terms.security_id]
    if (len(found) != 1 or found[0][0] != terms.contract
            or number(found[0][4], positive=True) != terms.strike):
        raise RuntimeError("daily quote conflicts with dated terms")
    # Provenance binds BOTH inputs; availability must not leak to another day.
    return replace(terms, availability_day=reference,
                   source_hash=hashlib.sha256((terms.source_hash+payload_hash(payload)).encode()).hexdigest())


def cash_basis_preview(entry: Terms, current: Terms, *, premium: Any, contracts: Any,
                       entry_day: Any, as_of: Any, cost_evidence_hash: str) -> dict:
    """Preserve cash per contract, never use settlement as the entry premium.

    Callers must supply independently established entry terms and cost evidence.
    The returned object is a diagnostic; it cannot be passed to the old engine
    as authorization to remove its A-contract gate.
    """
    entry.on(entry_day)
    current.on(as_of)
    if (entry.security_id != current.security_id or day(entry_day) > day(as_of)
            or not isinstance(cost_evidence_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", cost_evidence_hash)):
        raise RuntimeError("cash basis identity/date/evidence mismatch")
    if (entry.contract[:11]+entry.contract[12:] != current.contract[:11]+current.contract[12:]
            or entry.expiry != current.expiry or entry.valid_through < day(entry_day)
            or entry.contract[11] == "A" and (current.contract[11] != "A"
                or entry.unit != current.unit or entry.strike != current.strike)):
        raise RuntimeError("cash basis series changed")
    quantity = number(contracts, positive=True, integer=True)
    price = number(premium, positive=True)
    with localcontext() as context:
        context.prec = 50
        per_contract = price * entry.unit
        economic_price = per_contract / current.unit
        total = per_contract * quantity
    return dict(revision=REVISION, engine_supported=False, ledger_write_authorized=False,
                security_id=current.security_id, original_contract=entry.contract,
                current_contract=current.contract, original_unit=entry.unit, current_unit=current.unit,
                contracts=int(quantity), original_premium=str(price),
                entry_day=day(entry_day).isoformat(), as_of=day(as_of).isoformat(),
                cost_evidence_hash=cost_evidence_hash, entry_terms_hash=entry.source_hash,
                current_terms_hash=current.source_hash, cash_per_contract=str(per_contract),
                total_entry_cash=str(total), equivalent_current_unit_premium=str(economic_price),
                cost_status="verified_inputs_preview_only")


def cash_threshold_hit(basis: dict, current: Terms, *, premium: Any,
                       multiple: Any, comparison: str) -> bool:
    """Compare cash directly; avoid rounding a rebased per-unit entry price."""
    if (not isinstance(basis, dict) or basis.get("revision") != REVISION
            or basis.get("cost_status") != "verified_inputs_preview_only"
            or basis.get("security_id") != current.security_id
            or basis.get("current_contract") != current.contract
            or type(basis.get("current_unit")) is not int or basis.get("current_unit") != current.unit
            or basis.get("current_terms_hash") != current.source_hash
            or basis.get("engine_supported") is not False or basis.get("ledger_write_authorized") is not False):
        raise RuntimeError("cash threshold basis/terms mismatch")
    # These are internal consistency checks, not authentication of the caller's
    # cost evidence. A self-consistent preview still cannot enable production.
    try:
        for field in ("cost_evidence_hash", "entry_terms_hash"):
            if not isinstance(basis[field], str) or not re.fullmatch(r"[0-9a-f]{64}", basis[field]):
                raise RuntimeError("cash threshold cost provenance invalid")
        original = basis["original_contract"]
        original_match = CODE.fullmatch(original) if isinstance(original, str) else None
        if (not original_match or original[:11]+original[12:] != current.contract[:11]+current.contract[12:]
                or original[11] == "A" and current.contract[11] != "A"
                or type(basis["original_unit"]) is not int
                or original[11] == "M" and basis["original_unit"] != 10000
                or original[11] == "A" and basis["original_unit"] != current.unit
                or day(basis["entry_day"]) > day(basis["as_of"])):
            raise RuntimeError("cash threshold original terms invalid")
        with localcontext() as context:
            context.prec = 50
            cash = number(basis["original_premium"], positive=True) * number(basis["original_unit"], positive=True, integer=True)
            total = cash * number(basis["contracts"], positive=True, integer=True)
            equivalent = cash/current.unit
            if (number(basis["cash_per_contract"], positive=True) != cash
                    or number(basis["total_entry_cash"], positive=True) != total
                    or number(basis["equivalent_current_unit_premium"], positive=True) != equivalent):
                raise RuntimeError("cash threshold preview is self-inconsistent")
    except KeyError as exc:
        raise RuntimeError("cash threshold basis fields missing") from exc
    current.on(basis["as_of"])
    factor = number(multiple, positive=True)
    with localcontext() as context:
        context.prec = 50
        mark = number(premium) * current.unit
        threshold = number(basis["cash_per_contract"], positive=True) * factor
    if comparison == "at_least":
        return mark >= threshold
    if comparison == "at_most":
        return mark <= threshold
    raise RuntimeError("unknown cash threshold comparison")


def preview_plan_identities(plan: dict, terms: dict[str, Terms], *, execution_day: Any) -> dict:
    """List required quote bindings, preserving frozen intent, without rebinding it.

    Original M/A labels stay in `original_plan`. A changed label means that the
    production planner needs an explicit versioned transition, never that an old
    opening quote can be silently relabelled or the execution date postponed.
    """
    if not isinstance(plan, dict) or day(plan.get("execution_day")) != day(execution_day):
        raise RuntimeError("plan execution date mismatch")
    required = plan.get("required_quotes")
    if not isinstance(required, list) or not required:
        raise RuntimeError("plan quote requirements missing")
    rows = []
    for row in required:
        if not isinstance(row, dict):
            raise RuntimeError("plan quote requirement invalid")
        if row.get("kind") != "option":
            rows.append(dict(original=deepcopy(row), status="outside_option_scope"))
            continue
        security, contract = row.get("security_id"), row.get("contract")
        if not isinstance(security, str) or security not in terms or not CODE.fullmatch(str(contract)):
            raise RuntimeError("plan option identity unproved")
        current = terms[security].on(execution_day)
        if current.security_id != security:
            raise RuntimeError("plan option security mapping conflicts with dated terms")
        # A stable ID may support the documented M->A alias, not arbitrary codes.
        if contract != current.contract and not (
                current.contract[11] == "A" and contract == current.contract[:11]+"M"+current.contract[12:]):
            raise RuntimeError("plan option alias unproved")
        rows.append(dict(original=deepcopy(row), current_terms=current.json(),
                         status="requires_explicit_plan_migration" if contract != current.contract
                         else "identity_unchanged"))
    # Canonical round-trip avoids sharing mutable nested data with the caller.
    original = json.loads(json.dumps(plan, ensure_ascii=False, default=str, allow_nan=False))
    return dict(revision=REVISION, original_plan=original, quote_bindings=rows,
                engine_supported=False, ledger_write_authorized=False,
                status="preview_only_pending_plan_validation")
