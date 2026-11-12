"""Independent expiry checks. Rule checks are never monthly notice evidence."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import re
from zoneinfo import ZoneInfo

import ic_im_adjusted_put_migration_v1 as terms_parser

REVISION = "expiry_source_completion_20261112_v3"
EFFECTIVE_SIGNAL_DATE = date(2026, 11, 12)
CFFEX_RULE_SOURCES = (
    "https://www.cffex.com.cn/u/cms/www/oldsys/W020150327602765131821.pdf",
    "https://www.cffex.com.cn/u/cms/www/202207/182020307zry.pdf",
    "https://www.cffex.com.cn/u/cms/www/202207/1820311129r5.pdf",
)


class ExpiryEvidenceConflict(RuntimeError):
    """Positive contradictory evidence must not disappear through failover."""


class ExpiryMasterUnavailable(RuntimeError):
    """No applicable dated master; another independently dated source may work."""


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ExpiryEvidenceConflict("SSE主表JSON含重复字段")
        result[key] = value
    return result


def verify_sse_chain(frame, payload, *, as_of, month, model_expiry, observation_clock=None):
    """Bind every quote identity to a same-day real SSE master row."""
    if type(as_of) is not date or type(model_expiry) is not date:
        raise ExpiryEvidenceConflict("SSE到期核验缺少明确市场日或模型日期")
    if not isinstance(month, str) or re.fullmatch(r"20[0-9]{2}(?:0[1-9]|1[0-2])", month) is None:
        raise ExpiryEvidenceConflict("SSE到期核验月份无效")
    if model_expiry.strftime("%Y%m") != month:
        raise ExpiryEvidenceConflict("SSE模型到期日不属于请求月份")
    observed_day = as_of
    if observation_clock is not None:
        if (not isinstance(observation_clock, datetime) or observation_clock.tzinfo is None
                or observation_clock.utcoffset() is None):
            raise ExpiryEvidenceConflict("SSE主表缺少明确的实际观察时刻")
        observed_day = observation_clock.astimezone(ZoneInfo("Asia/Shanghai")).date()
        if observed_day < as_of:
            raise ExpiryEvidenceConflict("SSE参考市场日晚于实际观察日")
    try:
        if not isinstance(payload, dict) or not isinstance(payload.get("result"), list) or not payload["result"]:
            raise ExpiryMasterUnavailable("SSE当日主表为空，不能确认实际到期日")
        rows = payload["result"]
        snapshot_dates = {terms_parser.day(row["TIMESAVE"]) for row in rows}
        if len(snapshot_dates) != 1:
            raise ExpiryEvidenceConflict("SSE主表实际TIMESAVE混合")
        snapshot_day = next(iter(snapshot_dates))
        if snapshot_day > observed_day:
            raise ExpiryEvidenceConflict("SSE主表实际TIMESAVE晚于实际观察日")
        # Validate this current table's own identities and actual expiry first.
        # A different reference date must not hide contradictory expiry fields.
        terms = terms_parser.master_terms(payload, as_of=snapshot_day)
    except ExpiryMasterUnavailable:
        raise
    except (RuntimeError, KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ExpiryEvidenceConflict(f"SSE主表身份或真实到期字段无效：{exc}") from exc
    for term in terms.values():
        if term.expiry.strftime("%Y%m") != month or term.contract[7:11] != month[2:]:
            raise ExpiryEvidenceConflict("SSE主表返回了请求月份以外的合约")
        if term.expiry != model_expiry:
            raise ExpiryEvidenceConflict("SSE主表真实到期日与模型日期冲突")
    if snapshot_day != as_of:
        raise ExpiryMasterUnavailable("SSE当前主表TIMESAVE不能证明所需历史参考时点，禁止重标来源日期")
    needed = {"contract", "security_id", "strike"}
    if not needed.issubset(frame.columns) or frame.empty:
        raise ExpiryEvidenceConflict("SSE行情链缺少可核验证券身份")
    seen = set()
    for row in frame.to_dict("records"):
        security = row["security_id"]
        term = terms.get(security)
        try:
            strike = terms_parser.number(row["strike"], positive=True)
        except RuntimeError as exc:
            raise ExpiryEvidenceConflict("SSE行情链行权价无效") from exc
        if (not isinstance(security, str) or security in seen or term is None
                or row["contract"] != term.contract or strike != term.strike):
            raise ExpiryEvidenceConflict("SSE行情链与主表证券ID/合约/行权价冲突")
        if term.contract[11] == "M" and term.unit != 10000:
            raise ExpiryEvidenceConflict("SSE标准合约单位与真实主表冲突")
        seen.add(security)
    if seen != set(terms):
        raise ExpiryEvidenceConflict("SSE行情链与完整当日主表证券集合不一致")
    result = frame.copy()
    result["expiry"] = model_expiry
    result.attrs["expiry_evidence"] = {
        "revision": REVISION, "source": "SSE_dated_contract_master",
        "as_of": as_of.isoformat(), "month": month,
        "actual_expiry": model_expiry.isoformat(),
        "date_fields": ["EXPIRE_DATE", "EXERCISE_DATE", "END_DATE"],
        "master_sha256": terms_parser.payload_hash(payload),
        "delivery_date_used_as_expiry": False,
    }
    return result


def verify_recorded_chain(frame, stamp, proof, *, as_of, month, observation_clock,
                          model_expiry=None):
    """Recheck raw provider evidence on every post-adoption archive consumption."""
    if (not isinstance(proof, dict) or proof.get("revision") != REVISION
            or proof.get("day") != as_of.isoformat() or proof.get("month") != month
            or not isinstance(stamp, dict)):
        raise ExpiryEvidenceConflict("原日到期来源身份/版本/月日不一致")
    provider = proof.get("provider")
    if provider == "SSE":
        if stamp.get("source") != "上交所行情接口":
            raise ExpiryEvidenceConflict("原日期权链与SSE到期来源不一致")
        try:
            actual = terms_parser.day(proof["payload"]["result"][0]["EXPIRE_DATE"])
        except (KeyError, IndexError, TypeError, RuntimeError) as exc:
            raise ExpiryEvidenceConflict("原日SSE实际到期字段缺失") from exc
        expected = model_expiry or actual
        result = verify_sse_chain(frame, proof["payload"], as_of=as_of,
                                  month=month, model_expiry=expected,
                                  observation_clock=observation_clock)
    elif provider == "Sina":
        if not str(stamp.get("source", "")).startswith("新浪财经期权详报价"):
            raise ExpiryEvidenceConflict("原日期权链与Sina到期来源不一致")
        ids, packets = proof.get("listed_ids"), proof.get("packets")
        if (not isinstance(ids, list) or not ids or len(ids) != len(set(ids))
                or any(not isinstance(s, str) or re.fullmatch(r"[0-9]{8}", s) is None for s in ids)
                or not isinstance(packets, list) or len(packets) != len(ids)):
            raise ExpiryEvidenceConflict("原日Sina挂牌/详情集合无效")
        parsed, seen, actuals = {}, set(), set()
        for packet in packets:
            if not isinstance(packet, (list, tuple)) or len(packet) != 2:
                raise ExpiryEvidenceConflict("原日Sina详情报文结构无效")
            security, text = packet
            if not isinstance(security, str) or security in seen or security not in ids or not isinstance(text, str):
                raise ExpiryEvidenceConflict("原日Sina详情证券集合缺失、重复或越界")
            seen.add(security)
            fields = text.split(",")
            if (len(fields) <= 46 or fields[36].strip() != "510500"
                    or fields[43] not in {"M", "A"} or fields[45] != "P"):
                raise ExpiryEvidenceConflict("原日Sina详情标的/类型无效")
            try:
                actual = terms_parser.day(fields[46])
                strike = terms_parser.number(fields[7], positive=True)
                price = terms_parser.number(fields[2])
                observed = datetime.strptime(fields[32], "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=observation_clock.tzinfo)
            except (RuntimeError, ValueError, TypeError, AttributeError) as exc:
                raise ExpiryEvidenceConflict("原日Sina完整到期日/行情字段无效") from exc
            if (actual.strftime("%Y%m") != month or observed > observation_clock):
                raise ExpiryEvidenceConflict("原日Sina实际月份、行情日或观察时间冲突")
            actuals.add(actual)
            if fields[43] == "M":
                contract = f"510500P{month[2:]}M{int(round(float(strike)*1000)):05d}"
                if strike != terms_parser.number(int(round(float(strike)*1000))/1000):
                    raise ExpiryEvidenceConflict("原日Sina标准行权价精度冲突")
                parsed[security] = (contract, strike, price, observed)
        if seen != set(ids) or len(actuals) != 1:
            raise ExpiryEvidenceConflict("原日Sina挂牌详情或实际到期日不一致")
        actual = next(iter(actuals))
        expected = model_expiry or actual
        if (expected != actual or frame.empty or len(frame) != len(parsed)
                or set(frame["security_id"]) != set(parsed)):
            raise ExpiryEvidenceConflict("原日Sina链证券集合或模型到期日冲突")
        latest = max(item[3] for item in parsed.values())
        if (str(stamp.get("date", "")).replace("-", "") != latest.strftime("%Y%m%d")
                or str(stamp.get("time", "")).replace(":", "") != latest.strftime("%H%M%S")):
            raise ExpiryEvidenceConflict("原日Sina总体时刻与原始详情最大M合约时刻冲突")
        # Unconsumed stale rows remain visible. Selection still enforces each
        # consumed row's exact timestamp, independently of this aggregate stamp.
        for row in frame.to_dict("records"):
            item = parsed[row["security_id"]]
            if (row["contract"] != item[0]
                    or terms_parser.number(row["strike"], positive=True) != item[1]
                    or terms_parser.number(row["last"]) != item[2]
                    or row.get("source_stamp") != item[3]):
                raise ExpiryEvidenceConflict("原日Sina链与原始详情身份/报价/时刻冲突")
        result = frame.copy(deep=True)
    else:
        raise ExpiryEvidenceConflict("原日到期来源缺失或未授权")
    if "expiry" not in frame or any(type(value) is not date or value != expected for value in frame["expiry"]):
        raise ExpiryEvidenceConflict("原日期权链缺少实际到期列或与来源冲突")
    return result


def verify_cffex_rule_date(year, month, model_expiry, is_trading_day):
    """Independent arithmetic, sharing only the approved session input."""
    if type(year) is not int or type(month) is not int or type(model_expiry) is not date:
        raise ExpiryEvidenceConflict("CFFEX规则日期输入无效")
    fifteenth = date(year, month, 15)
    expected = fifteenth + timedelta(days=(4-fifteenth.weekday()) % 7)
    for _ in range(32):
        if is_trading_day(expected):
            break
        expected += timedelta(days=1)
    else:
        raise ExpiryEvidenceConflict("CFFEX到期规则无法在有界交易日范围内核验")
    if expected != model_expiry:
        raise ExpiryEvidenceConflict("CFFEX第三个周五顺延规则与模型日期冲突")
    return {"source": "CFFEX_contract_rule_with_accepted_session_calendar",
            "expiry": expected.isoformat(), "rule_sources": list(CFFEX_RULE_SOURCES),
            "independent_monthly_notice_verified": False}
