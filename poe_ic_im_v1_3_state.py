"""Durable, hash-chained research ledger for the IC/IM v1.3 Poe server.

The immutable constants in ``poe_ic_im_mainline_v1_3_bot.py`` are only a
bootstrap checkpoint.  A server deployment loads ``latest.json`` before each
query and appends one journal record after a fully confirmed close.  This keeps
Poe restarts and new conversations independent from hard-coded calendar dates.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import poe_ic_im_mainline_v1_3_bot as strategy


SCHEMA_VERSION = 3
STRATEGY_VERSION = "1.3"
STRATEGY_REVISION = "r6"
STATE_ENV = "ICIM_STATE_DIR"
DEFAULT_STATE_DIR = Path(__file__).resolve().parent / "runtime" / "ic_im_v1_3_r6"
PRODUCTS = ("IC", "IM")


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _canonical_payload(record: dict[str, Any]) -> bytes:
    payload = {key: value for key, value in record.items() if key != "digest"}
    return json.dumps(
        _jsonable(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_payload(record)).hexdigest()


def _decode_anchor(anchor: dict[str, Any]) -> dict[str, Any]:
    decoded = dict(anchor)
    for key, value in list(decoded.items()):
        if key.endswith("_day") or key.endswith("_expiry"):
            if isinstance(value, str) and value:
                decoded[key] = date.fromisoformat(value[:10])
    return decoded


def _validate_record(record: dict[str, Any]) -> None:
    if int(record.get("schema_version", -1)) != SCHEMA_VERSION:
        raise RuntimeError("Poe账本schema_version不受支持")
    if str(record.get("strategy_version", "")) != STRATEGY_VERSION:
        raise RuntimeError("Poe账本strategy_version不是独立v1.3，禁止续写旧账本")
    if str(record.get("strategy_revision", "")) != STRATEGY_REVISION:
        raise RuntimeError("Poe账本strategy_revision不是r6，禁止续写旧版v1.3账本")
    sequence = record.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise RuntimeError("Poe账本sequence必须为非负整数")
    if set(record.get("products", {})) != set(PRODUCTS):
        raise RuntimeError("Poe账本必须同时包含IC和IM")
    expected = str(record.get("digest", ""))
    actual = _digest(record)
    if not expected or expected != actual:
        raise RuntimeError("Poe账本SHA-256校验失败")
    days: list[date] = []
    for product in PRODUCTS:
        anchor = _decode_anchor(record["products"][product])
        day = anchor.get("last_verified_day")
        if not isinstance(day, date):
            raise RuntimeError(f"{product}账本缺少last_verified_day")
        days.append(day)
        if float(anchor.get("verified_grid_units", -1)) not in {0.0, 1.0}:
            raise RuntimeError(f"{product}账本网格状态非法")
        if float(anchor.get("verified_next_grid_units", -1)) not in {0.0, 1.0}:
            raise RuntimeError(f"{product}账本下一交易日网格状态非法")
    if len(set(days)) != 1:
        raise RuntimeError("IC/IM账本核验日期不一致，禁止部分推进")
    try:
        verified_day = date.fromisoformat(str(record.get("verified_day", ""))[:10])
    except ValueError as exc:
        raise RuntimeError("Poe账本verified_day非法") from exc
    if verified_day != days[0]:
        raise RuntimeError("Poe账本顶层verified_day与逐腿锚点不一致")
    allowed_weights = {0.0, 0.25, 0.5, 1.0}
    for product in PRODUCTS:
        anchor = record["products"][product]
        for key in ("verified_momentum_weight", "verified_next_momentum_weight"):
            value = float(anchor.get(key, math.nan))
            allowed = allowed_weights if product == "IC" else {0.0, 0.5, 1.0}
            if not math.isfinite(value) or value not in allowed:
                raise RuntimeError(f"{product}账本{key}非法")
        put_qty = float(anchor.get("verified_put_qty_normalized", math.nan))
        if not math.isfinite(put_qty) or put_qty < 0.0:
            raise RuntimeError(f"{product}账本Put数量非法")
    ic = record["products"]["IC"]
    if float(ic.get("verified_call_contracts_normalized", 0.0)) != 0.0:
        raise RuntimeError("IC 1.3明确禁止Call，账本出现Call状态")
    im = record["products"]["IM"]
    core_put = _finite_float(
        im.get("verified_core_put_qty_normalized"), "IM核心Put数量"
    )
    momentum_put = _finite_float(
        im.get("verified_momentum_put_qty_normalized"), "IM动量Put数量"
    )
    total_put = _finite_float(
        im.get("verified_total_put_qty_normalized"), "IM组合Put数量"
    )
    legacy_total = _finite_float(
        im.get("verified_put_qty_normalized"), "IM兼容Put总数量"
    )
    parent_puts = im.get("verified_parent_puts")
    if (
        core_put < 0.0
        or momentum_put < 0.0
        or total_put < 0.0
        or core_put > 2.0
        or momentum_put > 2.0
        or total_put > 4.0
    ):
        raise RuntimeError("IM核心/动量/组合Put数量超出合法域")
    if not math.isclose(total_put, core_put + momentum_put, abs_tol=1e-12):
        raise RuntimeError("IM组合Put数量不等于核心与动量之和")
    if not math.isclose(legacy_total, total_put, abs_tol=1e-12):
        raise RuntimeError("IM兼容Put总数量与双腿合计不一致")
    if not isinstance(parent_puts, int) or isinstance(parent_puts, bool) or parent_puts not in {0, 1, 2, 3, 4}:
        raise RuntimeError("IM父规则Put数量非法")
    if not math.isclose(core_put, 0.5 * parent_puts, abs_tol=1e-12):
        raise RuntimeError("IM核心Put数量不等于0.5倍父规则目标")
    core_contract = im.get("post_core_put_contract")
    momentum_contract = im.get("post_momentum_put_contract")
    if core_put > 0.0 and not core_contract:
        raise RuntimeError("IM核心Put非零但缺少独立合约")
    if momentum_put > 0.0 and not momentum_contract:
        raise RuntimeError("IM动量Put非零但缺少独立合约")
    if momentum_put == 0.0 and momentum_contract not in (None, ""):
        raise RuntimeError("IM动量Put为零但账本仍保留合约")


def _as_day(value: Any, label: str) -> date:
    try:
        return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label}日期非法") from exc


def _finite_float(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label}不是数值") from exc
    if not math.isfinite(number):
        raise RuntimeError(f"{label}不是有限数")
    return number


def bootstrap_record() -> dict[str, Any]:
    products = _jsonable(deepcopy(strategy.LIVE_CONTINUATION_ANCHOR))
    day = products["IC"]["last_verified_day"]
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "strategy_revision": STRATEGY_REVISION,
        "sequence": 0,
        "verified_day": day,
        "updated_at": datetime.now(strategy.BEIJING).isoformat(),
        "previous_digest": None,
        "products": products,
        "signals": {},
        "source": "audited_v1_3_replay_checkpoint",
        "genesis": {
            "parent_strategy_version": "1.2",
            "copied_parent_momentum_anchor": False,
            "momentum_rule": {
                "IC": "MA110_Mom24_W2_Abs20Blend_NAVDD6pct_half",
                "IM": "MA35_Mom18_W2.5_Abs20Blend_Score150_VolumeMA160_0.85",
            },
            "build": strategy.BUILD_ID,
            "im_put_ledgers": ["core_put", "momentum_put"],
        },
    }
    record["digest"] = _digest(record)
    _validate_record(record)
    return record


def anchors_from_record(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    _validate_record(record)
    return {
        product: _decode_anchor(deepcopy(record["products"][product]))
        for product in PRODUCTS
    }


def derive_next_anchors(
    current: dict[str, Any], signals: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    _validate_record(current)
    if set(signals) != set(PRODUCTS):
        raise RuntimeError("只有IC/IM均完整成功，才允许推进统一审计账本")
    current_anchors = anchors_from_record(current)
    signal_days = {signal.get("market_date") for signal in signals.values()}
    if len(signal_days) != 1:
        raise RuntimeError("IC/IM收盘信号日期不一致")
    signal_day_raw = next(iter(signal_days))
    signal_day = (
        signal_day_raw
        if isinstance(signal_day_raw, date)
        else date.fromisoformat(str(signal_day_raw)[:10])
    )
    previous_day = current_anchors["IC"]["last_verified_day"]
    expected = strategy._roll_forward_exchange_day(previous_day + timedelta(days=1))
    if signal_day != expected:
        raise RuntimeError(
            f"账本只能逐交易日推进：当前 {previous_day}，收到 {signal_day}，应为 {expected}"
        )

    result = deepcopy(current_anchors)
    for product in PRODUCTS:
        signal = signals[product]
        if str(signal.get("product")) != product:
            raise RuntimeError(f"{product}信号产品标签不一致")
        if not bool(signal.get("close_confirmed")):
            raise RuntimeError(f"{product}尚未收盘确认，禁止写入审计账本")
        if str(signal.get("market_phase")) != "收盘后":
            raise RuntimeError(f"{product}仅允许收盘后信号写入账本")
        if _as_day(signal.get("state_anchor_day"), f"{product} state_anchor_day") != previous_day:
            raise RuntimeError(f"{product}信号未从当前账本锚点续接")
        expected_next_day = strategy._roll_forward_exchange_day(signal_day + timedelta(days=1))
        if _as_day(signal.get("next_trade_date"), f"{product} next_trade_date") != expected_next_day:
            raise RuntimeError(f"{product}下一交易日不正确")
        anchor = result[product]
        current_weight = _finite_float(signal.get("momentum_current_weight"), f"{product}当前动量权重")
        next_weight = _finite_float(signal.get("momentum_next_weight"), f"{product}下一动量权重")
        allowed = {0.0, 0.25, 0.5, 1.0} if product == "IC" else {0.0, 0.5, 1.0}
        if current_weight not in allowed or next_weight not in allowed:
            raise RuntimeError(f"{product}动量权重超出离散合法域")
        if current_weight != float(anchor["verified_next_momentum_weight"]):
            raise RuntimeError(f"{product}当前动量权重不等于前日下一执行权重")
        current_grid = _finite_float(signal.get("grid_current"), f"{product}当前网格")
        target_grid = _finite_float(signal.get("grid_target"), f"{product}目标网格")
        if current_grid not in {0.0, 1.0} or target_grid not in {0.0, 1.0}:
            raise RuntimeError(f"{product}网格状态非法")
        if current_grid != float(anchor["verified_next_grid_units"]):
            raise RuntimeError(f"{product}当前网格不等于前日下一执行网格")
        anchor.update(
            {
                "last_verified_day": signal_day,
                "post_core_contract": str(signal["core_target"]),
                "verified_momentum_weight": current_weight,
                "verified_next_momentum_weight": next_weight,
                "verified_grid_units": current_grid,
                "verified_next_grid_units": target_grid,
            }
        )
        if product == "IC":
            put_target_contract = signal.get("put_target_contract")
            if put_target_contract:
                anchor["post_put_contract"] = str(put_target_contract)
            target_qty_raw = _finite_float(signal.get("put_target_total_qty"), "IC Put数量")
            if target_qty_raw < 0.0 or not target_qty_raw.is_integer():
                raise RuntimeError("IC Put数量必须为非负整数")
            target_qty = int(target_qty_raw)
            core_delta = _finite_float(signal.get("core_put_target_delta"), "IC核心Put Delta")
            momentum_delta = _finite_float(signal.get("momentum_put_target_delta"), "IC动量Put Delta")
            total_delta = _finite_float(signal.get("total_put_target_delta"), "IC总Put Delta")
            if min(core_delta, momentum_delta, total_delta) < 0.0 or max(core_delta, momentum_delta, total_delta) > 1.0:
                raise RuntimeError("IC Put Delta超出0到1")
            if not math.isclose(total_delta, core_delta + momentum_delta, abs_tol=1e-12):
                raise RuntimeError("IC总Put Delta不等于核心与动量之和")
            if _finite_float(signal.get("call_target_qty_normalized", 0.0), "IC Call数量") != 0.0:
                raise RuntimeError("IC 1.3明确禁止Call")
            target_security_id = signal.get("put_target_security_id")
            anchor.update(
                {
                    "post_put_qty": float(target_qty),
                    "verified_put_qty_normalized": float(target_qty),
                    "verified_core_put_delta": core_delta,
                    "verified_momentum_put_delta": momentum_delta,
                    "verified_total_put_delta": total_delta,
                    "verified_core_put_driver": str(signal["core_put_driver"]),
                    "verified_momentum_put_driver": str(
                        signal["momentum_put_driver"]
                    ),
                    "verified_call_contracts_normalized": 0.0,
                }
            )
            if target_security_id:
                anchor["post_put_security_id"] = str(target_security_id)
        else:
            core_current = _finite_float(
                signal.get("core_put_current_qty_normalized"), "IM当前核心Put数量"
            )
            momentum_current = _finite_float(
                signal.get("momentum_put_current_qty_normalized"), "IM当前动量Put数量"
            )
            total_current = _finite_float(
                signal.get("total_put_current_qty_normalized"), "IM当前组合Put数量"
            )
            core_put = _finite_float(
                signal.get("core_put_target_qty_normalized"), "IM核心Put数量"
            )
            momentum_put = _finite_float(
                signal.get("momentum_put_target_qty_normalized"), "IM动量Put数量"
            )
            total_put = _finite_float(
                signal.get("total_put_target_qty_normalized"), "IM组合Put数量"
            )
            call_qty = _finite_float(signal.get("call_target_qty_normalized", 0.0), "IM Call数量")
            parent_puts = signal.get("v13_parent_puts_per_full_core")
            if (
                core_put < 0.0
                or core_put > 2.0
                or momentum_put < 0.0
                or momentum_put > 2.0
                or total_put < 0.0
                or total_put > 4.0
                or call_qty not in {-1.0, 0.0}
            ):
                raise RuntimeError("IM期权目标数量超出合法域")
            if not isinstance(parent_puts, int) or isinstance(parent_puts, bool) or parent_puts not in {0, 1, 2, 3, 4}:
                raise RuntimeError("IM父规则Put数量非法")
            if not math.isclose(core_current, float(anchor["verified_core_put_qty_normalized"]), abs_tol=1e-12):
                raise RuntimeError("IM当前核心Put数量不等于账本锚点")
            if not math.isclose(momentum_current, float(anchor["verified_momentum_put_qty_normalized"]), abs_tol=1e-12):
                raise RuntimeError("IM当前动量Put数量不等于账本锚点")
            if not math.isclose(total_current, core_current + momentum_current, abs_tol=1e-12):
                raise RuntimeError("IM当前组合Put数量不等于核心与动量之和")
            current_core_contract = signal.get("core_put_current_contract")
            current_momentum_contract = signal.get("momentum_put_current_contract")
            if current_core_contract != anchor.get("post_core_put_contract"):
                raise RuntimeError("IM当前核心Put合约不等于账本锚点")
            if current_momentum_contract != anchor.get("post_momentum_put_contract"):
                raise RuntimeError("IM当前动量Put合约不等于账本锚点")
            if not math.isclose(core_put, 0.5 * parent_puts, abs_tol=1e-12):
                raise RuntimeError("IM核心Put目标不等于0.5倍父规则目标")
            expected_momentum = core_put * next_weight
            if not math.isclose(momentum_put, expected_momentum, abs_tol=1e-12):
                raise RuntimeError("IM动量Put目标不等于核心目标乘动量执行权重")
            if not math.isclose(total_put, core_put + momentum_put, abs_tol=1e-12):
                raise RuntimeError("IM组合Put目标不等于核心与动量之和")
            core_contract = signal.get("core_put_target_contract")
            momentum_contract = signal.get("momentum_put_target_contract")
            if core_put > 0.0 and not core_contract:
                raise RuntimeError("IM核心Put目标非零但缺少合约")
            if momentum_put > 0.0 and not momentum_contract:
                raise RuntimeError("IM动量Put目标非零但缺少独立合约")
            if momentum_put == 0.0 and momentum_contract not in (None, ""):
                raise RuntimeError("IM动量Put目标为零但仍保留合约")
            anchor.update(
                {
                    "post_put_contract": core_contract,
                    "post_core_put_contract": core_contract,
                    "post_momentum_put_contract": momentum_contract,
                    "post_put_equivalent_units": 0.5 * total_put,
                    "post_core_put_equivalent_units": 0.5 * core_put,
                    "post_momentum_put_equivalent_units": 0.5 * momentum_put,
                    "verified_put_qty_normalized": total_put,
                    "verified_core_put_qty_normalized": core_put,
                    "verified_momentum_put_qty_normalized": momentum_put,
                    "verified_total_put_qty_normalized": total_put,
                    "verified_parent_puts": parent_puts,
                    "verified_call_contracts_normalized": call_qty,
                    "verified_call_contract": signal.get("call_target_contract"),
                    "verified_call_expiry": signal.get("call_target_expiry"),
                    "verified_call_strike": signal.get("call_target_strike"),
                    "verified_threat_roll_count": int(
                        signal.get("call_target_threat_roll_count", 0)
                    ),
                }
            )
    return result


class StateStore:
    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        configured = root or os.environ.get(STATE_ENV)
        self.root = Path(configured) if configured else DEFAULT_STATE_DIR
        self.latest_path = self.root / "latest.json"
        self.journal_dir = self.root / "journal"
        self.lock_path = self.root / ".ledger.lock"

    @contextmanager
    def _exclusive_lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        stream = self.lock_path.open("a+b")
        try:
            stream.seek(0)
            stream.write(b"\0")
            stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()

    def _atomic_write(self, path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            _jsonable(record), ensure_ascii=False, sort_keys=True, indent=2
        ) + "\n"
        handle, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def initialize(self) -> dict[str, Any]:
        with self._exclusive_lock():
            if self.latest_path.exists():
                return self.load_latest()
            record = bootstrap_record()
            journal = self.journal_dir / f"000000-{record['verified_day']}.json"
            self._atomic_write(journal, record)
            self._atomic_write(self.latest_path, record)
            return record

    def load_latest(self) -> dict[str, Any]:
        try:
            record = json.loads(self.latest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"无法读取Poe持久化账本: {exc}") from exc
        _validate_record(record)
        previous_digest: str | None = None
        previous_day: date | None = None
        for sequence in range(int(record["sequence"]) + 1):
            item = self._load_sequence_record(sequence)
            if item.get("previous_digest") != previous_digest:
                raise RuntimeError(f"Poe账本序号 {sequence} 的前序SHA-256链断裂")
            previous_digest = str(item["digest"])
            item_day = _as_day(item["verified_day"], "账本verified_day")
            if previous_day is not None and item_day != strategy._roll_forward_exchange_day(previous_day + timedelta(days=1)):
                raise RuntimeError(f"Poe账本序号 {sequence} 未逐交易日推进")
            previous_day = item_day
        if previous_digest != record["digest"]:
            raise RuntimeError("latest.json未指向审计日志链的最新序号")
        return record

    def _load_sequence_record(self, sequence: int) -> dict[str, Any]:
        matches = sorted(self.journal_dir.glob(f"{sequence:06d}-*.json"))
        if len(matches) != 1:
            raise RuntimeError(f"Poe账本序号 {sequence} 不存在或重复")
        record = json.loads(matches[0].read_text(encoding="utf-8"))
        _validate_record(record)
        if int(record["sequence"]) != sequence:
            raise RuntimeError(f"Poe账本文件序号与内容不一致: {sequence}")
        expected_name = f"{sequence:06d}-{record['verified_day']}.json"
        if matches[0].name != expected_name:
            raise RuntimeError(f"Poe账本文件名与内容日期不一致: {matches[0].name}")
        return record

    def load_sequence(self, sequence: int) -> dict[str, Any]:
        latest = self.load_latest()
        if sequence < 0 or sequence > int(latest["sequence"]):
            raise RuntimeError(f"Poe账本序号 {sequence} 超出当前日志范围")
        return self._load_sequence_record(sequence)

    def append_confirmed_signals(
        self, current: dict[str, Any], signals: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        with self._exclusive_lock():
            latest = self.load_latest()
            if latest["digest"] != current["digest"]:
                raise RuntimeError("Poe账本已被另一请求推进，请重新读取后再写入")
            return self._append_locked(current, signals)

    def _append_locked(self, current: dict[str, Any], signals: dict[str, dict[str, Any]]) -> dict[str, Any]:
        anchors = derive_next_anchors(current, signals)
        signal_day = next(iter({signal["market_date"] for signal in signals.values()}))
        sequence = int(current["sequence"]) + 1
        record: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "strategy_version": STRATEGY_VERSION,
            "strategy_revision": STRATEGY_REVISION,
            "sequence": sequence,
            "verified_day": _jsonable(signal_day),
            "updated_at": datetime.now(strategy.BEIJING).isoformat(),
            "previous_digest": current["digest"],
            "products": _jsonable(anchors),
            "signals": _jsonable(signals),
            "source": "automatic_close_replay",
        }
        record["digest"] = _digest(record)
        _validate_record(record)
        journal = self.journal_dir / f"{sequence:06d}-{record['verified_day']}.json"
        if journal.exists():
            existing = json.loads(journal.read_text(encoding="utf-8"))
            _validate_record(existing)
            if (
                existing.get("previous_digest") != current["digest"]
                or str(existing.get("verified_day")) != str(record["verified_day"])
            ):
                raise RuntimeError(f"{record['verified_day']}账本已存在但内容冲突")
            # Recover a crash that completed the append-only journal write but
            # happened before latest.json was atomically replaced.
            self._atomic_write(self.latest_path, existing)
            return existing
        self._atomic_write(journal, record)
        self._atomic_write(self.latest_path, record)
        return record


def close_clock(day: date) -> datetime:
    return datetime.combine(day, time(15, 20), tzinfo=strategy.BEIJING)
