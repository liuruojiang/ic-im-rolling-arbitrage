"""Research-only parsers for actual anonymous public responses, not production providers."""
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
import json
import re
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo('Asia/Shanghai')


def number(raw, positive=False):
    if isinstance(raw, bool):
        raise ValueError('boolean is not a quote')
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise ValueError('invalid numeric quote') from exc
    if not value.is_finite() or value < 0 or (positive and value == 0):
        raise ValueError('non-finite or out-of-range quote')
    return value


def variables(body, prefix, separator=','):
    text = body.decode('gb18030')
    matches = list(re.finditer(r'(?:var\s+)?' + re.escape(prefix) + r'([A-Za-z0-9_]+)="([^"\r\n]*)";?', text))
    result = {}
    for match in matches:
        symbol, payload = match.groups()
        if symbol in result or not payload:
            raise ValueError('duplicate or empty vendor variable')
        result[symbol] = payload.split(separator)
    if not result:
        raise ValueError('no exact vendor variable')
    return result


def quote_stamp(raw, day, clock):
    observed = datetime.fromisoformat(raw).replace(tzinfo=BEIJING)
    if observed.date() != day or observed > clock.astimezone(BEIJING) or observed.time() < time(9, 15):
        raise ValueError('wrong-day, future, or pre-session ETF timestamp')
    return observed


def tencent_etf(body, day, clock):
    rows = variables(body, 'v_', '~')
    if set(rows) != {'sh510500'}:
        raise ValueError('wrong Tencent symbol')
    f = rows['sh510500']
    if len(f) < 31 or f[2] != '510500' or not re.fullmatch(r'[0-9]{14}', f[30]):
        raise ValueError('wrong Tencent identity or timestamp')
    stamp = datetime.strptime(f[30], '%Y%m%d%H%M%S')
    return {'last': number(f[3], True), 'previous_close': number(f[4], True),
            'open': number(f[5], True), 'source_stamp': quote_stamp(stamp.isoformat(), day, clock),
            'source': 'Tencent', 'timestamp_semantics': 'vendor update; not asserted closing execution time'}


def sina_etf(body, day, clock):
    rows = variables(body, 'hq_str_')
    if set(rows) != {'sh510500'}:
        raise ValueError('wrong Sina ETF symbol')
    f = rows['sh510500']
    if len(f) < 32 or not f[30] or not f[31]:
        raise ValueError('missing Sina ETF stamp')
    return {'last': number(f[3], True), 'previous_close': number(f[2], True),
            'open': number(f[1], True), 'source_stamp': quote_stamp(f[30] + 'T' + f[31], day, clock),
            'source': 'Sina', 'timestamp_semantics': 'vendor update; not asserted closing execution time'}


def option_chain(master_body, listed_body, batches, day, clock, ym='2612'):
    """Return verified identity records; stale/nonstandard records stay inadmissible.

    The previous-close master is a cross-check for existing contracts only. A
    missing/new contract or unequal list is rejected, never silently filled.
    This does not certify a dated current-day SSE master or historical provider.
    """
    payload = json.loads(master_body)
    if payload.get('actionErrors') or not isinstance(payload.get('result'), list):
        raise ValueError('bad master result')
    terms = {}
    for row in payload['result']:
        contract = row.get('CONTRACT_ID', '')
        if not contract.startswith('510500P' + ym):
            continue
        if not re.fullmatch(r'510500P' + re.escape(ym) + r'[MA][0-9]{5}', contract):
            raise ValueError('bad standard identity')
        sid = row['SECURITY_ID']
        if not re.fullmatch(r'[0-9]{8}', sid) or sid in terms:
            raise ValueError('duplicate/invalid master security id')
        saved = date.fromisoformat(row['TIMESAVE'])
        if saved > day or (day - saved).days > 7:
            raise ValueError('unusable master vintage')
        expiry = datetime.strptime(row['EXPIRE_DATE'], '%Y%m%d').date()
        if (row['CALL_OR_PUT'] != '认沽' or row['SECURITYNAMEBYID'] != '500ETF(510500)'
                or row['DELISTFLAG'] != '否' or expiry <= day
                or datetime.strptime(row['START_DATE'], '%Y%m%d').date() > day):
            raise ValueError('master terms do not describe an existing 510500 Put')
        unit = number(row['CONTRACT_UNIT'], True)
        if unit != unit.to_integral_value():
            raise ValueError('noninteger contract multiplier')
        terms[sid] = {'contract': contract, 'strike': number(row['EXERCISE_PRICE'], True),
                      'unit': int(unit), 'expiry': expiry, 'master_asof': saved}
    listing = variables(listed_body, 'hq_str_')
    if set(listing) != {'OP_DOWN_510500' + ym}:
        raise ValueError('wrong listed month/underlying')
    ids = [v.removeprefix('CON_OP_') for v in listing['OP_DOWN_510500' + ym] if v]
    if len(ids) != len(set(ids)) or set(ids) != set(terms):
        raise ValueError('listing/master coverage mismatch')
    quoted = {}
    for requested, body in batches:
        rows = variables(body, 'hq_str_CON_OP_')
        if set(rows) != set(requested) or len(requested) != len(set(requested)):
            raise ValueError('batch does not exactly match requested ids')
        if set(rows) & set(quoted):
            raise ValueError('overlapping batch identities')
        quoted.update(rows)
    if set(quoted) != set(ids):
        raise ValueError('incomplete quoted chain')
    result = []
    for sid, f in quoted.items():
        term = terms[sid]
        style = term['contract'][11]
        if (len(f) <= 46 or f[36] != '510500' or f[45] != 'P' or f[43] != style
                or date.fromisoformat(f[46]) != term['expiry'] or number(f[7], True) != term['strike']):
            raise ValueError('quote/master identity disagreement')
        stamp = datetime.fromisoformat(f[32]).replace(tzinfo=BEIJING)
        if stamp.date() != day or stamp > clock.astimezone(BEIJING):
            raise ValueError('wrong-day or future option quote')
        last, bid, ask = number(f[2]), number(f[1]), number(f[3])
        if bid > ask and ask > 0:
            raise ValueError('crossed option book')
        if style == 'M' and term['unit'] != 10000:
            raise ValueError('standard contract has adjusted multiplier')
        close_reference = datetime.combine(day, time(15, 15), BEIJING)
        delay = (close_reference - stamp).total_seconds()
        # Do not infer a different stamp for no-trade/old quotes; never activate A.
        admitted = style == 'M' and last > 0 and stamp.time() >= time(9, 15) and 0 <= delay <= 1200
        result.append({**term, 'security_id': sid, 'last': last, 'bid': bid, 'ask': ask,
                       'volume': number(f[41]), 'source_stamp': stamp,
                       'research_selected_quote_eligible': admitted,
                       'nonstandard_contract_blocked': style == 'A'})
    return sorted(result, key=lambda r: r['security_id'])
