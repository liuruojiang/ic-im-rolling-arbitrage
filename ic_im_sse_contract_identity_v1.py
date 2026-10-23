"""Official SSE identity parsing and adjustment diagnosis; no trading-state migration."""
from datetime import date
from decimal import Decimal, InvalidOperation
import math
import re

REVISION = 'sse_option_identity_and_adjustment_diagnosis_20261021_v1'
EFFECTIVE_SIGNAL_DATE = date(2026, 10, 21)
ADJUSTMENT_SQL = 'SSE_ZQPZ_YSP_OPTZSXT_ADJUST_INFO_HYTZ_SEARCH_L'

def _day(value):
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError) as exc:
        raise RuntimeError('SSE identity date invalid') from exc

def _number(value, *, positive=False):
    if isinstance(value, bool):
        raise RuntimeError('SSE identity numeric value invalid')
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError('SSE identity numeric value invalid') from exc
    if not math.isfinite(number) or (number <= 0 if positive else number < 0):
        raise RuntimeError('SSE identity numeric value invalid')
    return number

def _unit(value):
    try:
        unit = Decimal(str(value))
        if not unit.is_finite() or unit <= 0 or unit != unit.to_integral_value():
            raise ValueError('invalid unit')
        return int(unit)
    except (ValueError, InvalidOperation) as exc:
        raise RuntimeError('SSE identity contract unit invalid') from exc

def validate_chain_rows(payload, yymm):
    """Validate the exact six-field SSE select projection, without dropping rows."""
    if not isinstance(payload, dict) or not re.fullmatch(r'[0-9]{4}', str(yymm)):
        raise RuntimeError('SSE identity payload/month invalid')
    rows = payload.get('list')
    if not isinstance(rows, list) or not rows:
        raise RuntimeError('SSE identity chain empty')
    for name, expected in [('total', len(rows)), ('begin', 0), ('end', len(rows))]:
        value = payload.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value != expected:
            raise RuntimeError('SSE identity chain incomplete or pagination unverified')
    contracts, securities = set(), set()
    checked = []
    for row in rows:
        if not isinstance(row, list) or len(row) != 6:
            raise RuntimeError('SSE identity fields missing')
        contract, last, change, previous, strike, security = row
        match = re.fullmatch(r'510500([CP])([0-9]{4})([MA])([0-9]{5})', str(contract))
        if (not match or match.group(2) != yymm or not isinstance(security, str)
                or not re.fullmatch(r'[0-9]{8}', security)):
            raise RuntimeError('SSE identity underlying/month/security mismatch')
        if contract in contracts or security in securities:
            raise RuntimeError('SSE identity duplicate contract or security')
        contracts.add(contract)
        securities.add(security)
        strike, last = _number(strike, positive=True), _number(last)
        if match.group(3) == 'M' and not math.isclose(strike, int(match.group(4))/1000., rel_tol=0, abs_tol=1e-9):
            raise RuntimeError('SSE identity standard strike mismatch')
        checked.append([contract, last, change, previous, strike, security])
    return checked

def parse_adjustment_events(payload, *, effective_date):
    """Parse the official dated adjustment table; prices are NOT a unit source."""
    effective_date = _day(effective_date)
    if (not isinstance(payload, dict) or payload.get('sqlId') != ADJUSTMENT_SQL
            or str(payload.get('securityCode')) != '510500'
            or not (payload.get('isPagination') is False
                    or isinstance(payload.get('isPagination'), str) and payload['isPagination'] == 'false')):
        raise RuntimeError('SSE adjustment response scope unverified')
    if (not isinstance(payload.get('actionErrors'), list)
            or not isinstance(payload.get('fieldErrors'), dict)
            or payload['actionErrors'] or payload['fieldErrors']):
        raise RuntimeError('SSE adjustment response reports an error or invalid error schema')
    rows = payload.get('result')
    if not isinstance(rows, list) or not rows:
        raise RuntimeError('SSE adjustment table empty')
    seen, codes, result = set(), set(), []
    for row in rows:
        try:
            if not isinstance(row, dict) or row['UNDERLYING_SECURITY_ID'] != '510500':
                raise RuntimeError('SSE adjustment underlying mismatch')
            security = row['SECURITY_ID']
            old, new = row['ORG_CONTRACT_ID'], row['CONTRACT_ID']
            match = re.fullmatch(r'510500([CP])([0-9]{4})M([0-9]{5})', str(old))
            if (not isinstance(security, str) or not re.fullmatch(r'[0-9]{8}', security)
                    or not match or new != old[:11] + 'A' + old[12:]
                    or row['CALL_OR_PUT'] != {'C': '认购', 'P': '认沽'}[match.group(1)]
                    or row['EXPIRE_MONTH'] != '20' + match.group(2)):
                raise RuntimeError('SSE adjustment identity mismatch')
            if security in seen or new in codes:
                raise RuntimeError('SSE adjustment duplicate identity')
            seen.add(security)
            codes.add(new)
            effective, expiry = _day(row['ADJUST_DATE']), _day(row['EXPIRE_DATE'])
            if (effective != effective_date or expiry < effective
                    or _day(row['START_DATE']) > effective
                    or expiry.strftime('%y%m') != match.group(2)
                    or _day(row['END_DATE']) != expiry or _day(row['EXERCISE_DATE']) != expiry):
                raise RuntimeError('SSE adjustment effective date mismatch')
            original_strike = _number(row['ORG_EXERCISE_PRICE'], positive=True)
            if not math.isclose(original_strike, int(match.group(3))/1000., rel_tol=0, abs_tol=1e-9):
                raise RuntimeError('SSE adjustment original strike mismatch')
            original_unit, unit = _unit(row['ORG_CONTRACT_UNIT']), _unit(row['CONTRACT_UNIT'])
            if original_unit != 10000:
                raise RuntimeError('SSE adjustment prior nonstandard contract not supported')
            result.append(dict(security_id=security, contract=new, previous_contract=old,
                               previous_strike=original_strike,
                               strike=_number(row['EXERCISE_PRICE'], positive=True),
                               previous_unit=original_unit, contract_unit=unit,
                               effective_date=effective, expiry=expiry))
        except (KeyError, TypeError) as exc:
            raise RuntimeError('SSE adjustment fields missing') from exc
    return result

def resolve_adjusted_holding(events, quote_payload, security_id, held_contract, as_of):
    """Diagnose an old M/new A alias by stable ID, without mutating any anchor.

    The caller must retain the official raw response and capture provenance.
    This result deliberately cannot authorize engine use or ledger migration.
    """
    as_of = _day(as_of)
    matches = [event for event in events if event['security_id'] == security_id]
    if len(matches) != 1:
        raise RuntimeError('SSE adjusted identity missing or ambiguous')
    event = matches[0]
    if not event['effective_date'] <= as_of <= event['expiry']:
        raise RuntimeError('SSE adjusted identity outside effective interval')
    if held_contract not in {event['previous_contract'], event['contract']}:
        raise RuntimeError('SSE adjusted holding alias not proved')
    if _day(quote_payload.get('date')) != as_of:
        raise RuntimeError('SSE adjusted quote date mismatch')
    rows = validate_chain_rows(quote_payload, event['contract'][7:11])
    found = [row for row in rows if row[5] == security_id]
    if (len(found) != 1 or found[0][0] != event['contract']
            or not math.isclose(found[0][4], event['strike'], rel_tol=0, abs_tol=1e-9)):
        raise RuntimeError('SSE adjusted quote identity/strike mismatch')
    return dict(event, observed_on=as_of, migration_required=held_contract != event['contract'],
                engine_supported=False, status='requires_unit_and_cost_basis_migration')
