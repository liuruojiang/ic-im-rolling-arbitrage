"""Durable, hash-chained research ledger for the IC/IM v1.2 Poe server.

The immutable constants in ``poe_ic_im_mainline_v1_2_bot.py`` are only a
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
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import poe_ic_im_mainline_v1_2_bot as strategy


SCHEMA_VERSION = 1
STATE_ENV = "ICIM_STATE_DIR"
DEFAULT_STATE_DIR = Path(__file__).resolve().parent / "runtime" / "ic_im_v1_2"
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
    ic = record["products"]["IC"]
    if float(ic.get("verified_call_contracts_normalized", 0.0)) != 0.0:
        raise RuntimeError("IC 1.2明确禁止Call，账本出现Call状态")


def bootstrap_record() -> dict[str, Any]:
    products = _jsonable(deepcopy(strategy.LIVE_CONTINUATION_ANCHOR))
    day = products["IC"]["last_verified_day"]
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "strategy_version": "1.2",
        "sequence": 0,
        "verified_day": day,
        "updated_at": datetime.now(strategy.BEIJING).isoformat(),
        "previous_digest": None,
        "products": products,
        "signals": {},
        "source": "audited_bootstrap_checkpoint",
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
        if not bool(signal.get("close_confirmed")):
            raise RuntimeError(f"{product}尚未收盘确认，禁止写入审计账本")
        anchor = result[product]
        anchor.update(
            {
                "last_verified_day": signal_day,
                "post_core_contract": str(signal["core_target"]),
                "verified_momentum_weight": float(signal["momentum_current_weight"]),
                "verified_next_momentum_weight": float(signal["momentum_next_weight"]),
                "verified_grid_units": float(signal["grid_current"]),
                "verified_next_grid_units": float(signal["grid_target"]),
            }
        )
        put_target_contract = signal.get("put_target_contract")
        if put_target_contract:
            anchor["post_put_contract"] = str(put_target_contract)
        if product == "IC":
            target_qty = int(
                signal.get("put_target_total_qty", anchor.get("post_put_qty", 0))
            )
            target_security_id = signal.get("put_target_security_id")
            anchor.update(
                {
                    "post_put_qty": float(target_qty),
                    "verified_put_qty_normalized": float(target_qty),
                    "verified_core_put_delta": float(signal["core_put_target_delta"]),
                    "verified_momentum_put_delta": float(
                        signal["momentum_put_target_delta"]
                    ),
                    "verified_total_put_delta": float(signal["total_put_target_delta"]),
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
            put_qty = float(signal["core_put_target_qty_normalized"])
            call_qty = float(signal.get("call_target_qty_normalized", 0.0))
            anchor.update(
                {
                    "post_put_equivalent_units": 0.5 * put_qty,
                    "verified_put_qty_normalized": put_qty,
                    "verified_parent_puts": int(signal["v12_parent_puts_per_full_core"]),
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
        for sequence in range(int(record["sequence"]) + 1):
            item = self._load_sequence_record(sequence)
            if item.get("previous_digest") != previous_digest:
                raise RuntimeError(f"Poe账本序号 {sequence} 的前序SHA-256链断裂")
            previous_digest = str(item["digest"])
        if previous_digest != record["digest"]:
            raise RuntimeError("latest.json未指向审计日志链的最新序号")
        return record

    def _load_sequence_record(self, sequence: int) -> dict[str, Any]:
        matches = sorted(self.journal_dir.glob(f"{sequence:06d}-*.json"))
        if len(matches) != 1:
            raise RuntimeError(f"Poe账本序号 {sequence} 不存在或重复")
        record = json.loads(matches[0].read_text(encoding="utf-8"))
        _validate_record(record)
        return record

    def load_sequence(self, sequence: int) -> dict[str, Any]:
        latest = self.load_latest()
        if sequence < 0 or sequence > int(latest["sequence"]):
            raise RuntimeError(f"Poe账本序号 {sequence} 超出当前日志范围")
        return self._load_sequence_record(sequence)

    def append_confirmed_signals(
        self, current: dict[str, Any], signals: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        latest = self.load_latest()
        if latest["digest"] != current["digest"]:
            raise RuntimeError("Poe账本已被另一请求推进，请重新读取后再写入")
        anchors = derive_next_anchors(current, signals)
        signal_day = next(iter({signal["market_date"] for signal in signals.values()}))
        sequence = int(current["sequence"]) + 1
        record: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "strategy_version": "1.2",
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
