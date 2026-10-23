from __future__ import annotations

import argparse
import html
import json
import math
import os
from datetime import date
from pathlib import Path
from typing import Any

import ic_im_v1_4_put_display as put_display
import ic_im_option_maintenance_notice as maintenance_notice
import ic_im_v1_4_delivery_contract as delivery_contract
import ic_im_put_execution_contract as put_execution_contract


def calendar_notices(payload: dict[str, Any]) -> list[str]:
    return maintenance_notice.reminders(
        payload.get("signals", {}), signal_day=payload.get("market_date"),
        is_session=delivery_contract.is_session,
        historical=payload.get("signal_timing") == "retrospective_replay")


def execution_notices(payload: dict[str, Any]) -> list[str]:
    return [line for product in ("IC", "IM")
            for line in put_execution_contract.display_lines(product, payload["signals"][product])]


HOLD_ACTIONS = {"", "HOLD", "NONE", "NO_ACTION", "MAINTAIN", "WAIT_IV"}
ACTION_CN = {
    "UNCONFIRMED": "未确认",
    "HOLD": "维持",
    "NONE": "无",
    "NO_ACTION": "无调整",
    "MAINTAIN": "维持",
    "OPEN": "开仓",
    "CLOSE": "平仓",
    "ROLL": "展期",
    "RESIZE": "调整",
    "RESET": "重置",
    "BUY": "买入",
    "SELL": "卖出",
    "TURN_ON": "开启",
    "TURN_OFF": "关闭",
    "RESCUE": "救援换仓",
    "WAIT_IV": "等待IV条件（无需操作）",
}
EXPECTED_BUILD = "v1.4-20260928-r1-coreput3x-open-fix7-nocall-repeatroll-iciv30-qdelta05"
EXPECTED_DELIVERY_REVISION = "20260928-v14-coreput3x-open-fix7-nocall-repeatroll-iciv30-qdelta05"
# Pre-fix6 archived payloads retain their historical, incomplete field contract.
# Every successful modern producer result must pass the full per-leg boundary.
STRICT_SIGNAL_IDENTITY_DATE = date(2026, 9, 26)


def expected_rule_revision_for_day(day: date) -> str:
    """Mirror the date-authoritative strategy rule registry, not payload fields."""
    if day >= date(2026, 10, 8):
        return "ic_im_v1_4_ic_csi500_ma105_w16_abs40_static_20261008_v1"
    if day >= date(2026, 9, 29):
        return "ic_im_v1_4_ordinaryput_open_ic_seller_mom120_20260929_v1"
    if day >= date(2026, 9, 28):
        return "ic_im_v1_4_coreput3x_t1_open_20260928_v1"
    if day >= STRICT_SIGNAL_IDENTITY_DATE:
        return "ic_im_v1_4_no_im_call_repeat_short_put_roll_20260926_v1"
    if day >= date(2026, 9, 18):
        return "ic_im_v1_4_iciv30_qdelta05_20260918_v1"
    return "ic_im_v1_4_coreput3x_fixed_short95_20260917_v1"


def number(value: Any) -> str:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return str(value or "N/A")
    return f"{result:g}"


def decimal(value: Any, places: int = 3) -> str:
    try:
        return f"{float(value):.{places}f}"
    except (TypeError, ValueError):
        return "N/A"


def percent(value: Any, places: int = 2) -> str:
    try:
        return f"{float(value):.{places}%}"
    except (TypeError, ValueError):
        return "N/A"


def _finite_qty(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _iso_day(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def ordinary_put_transition_identity_only(product: str, signal: dict[str, Any]) -> bool:
    """Recognize only the exact, one-time IC identity migration record."""
    if (
        product != "IC"
        or signal.get("product") != "IC"
        or signal.get("v14_ordinary_put_plan_status") != "scheduled_t_plus_1_open"
    ):
        return False
    plan = signal.get("v14_ordinary_put_pending")
    if not isinstance(plan, dict):
        return False
    legs = plan.get("legs")
    if not isinstance(legs, dict) or set(legs) != {"core", "momentum"}:
        return False
    core, momentum = legs["core"], legs["momentum"]
    if not isinstance(core, dict) or not isinstance(momentum, dict):
        return False

    current_qty = _finite_qty(signal.get("put_current_core_qty"))
    target_qty = _finite_qty(signal.get("put_target_core_qty"))
    current_momentum_qty = _finite_qty(signal.get("put_current_momentum_qty"))
    target_momentum_qty = _finite_qty(signal.get("put_target_momentum_qty"))
    core_old_qty = _finite_qty(core.get("old_qty"))
    core_new_qty = _finite_qty(core.get("new_qty"))
    momentum_old_qty = _finite_qty(momentum.get("old_qty"))
    momentum_new_qty = _finite_qty(momentum.get("new_qty"))
    target_contract = signal.get("put_target_contract")
    current_contract = signal.get("put_current_contract")
    target_security_id = signal.get("put_target_security_id")
    current_security_id = signal.get("put_current_security_id")
    return bool(
        _iso_day(signal.get("market_date")) == "2026-09-29"
        and _iso_day(signal.get("next_trade_date")) == "2026-09-30"
        and plan.get("product") == "IC"
        and _iso_day(plan.get("signal_day")) == "2026-09-29"
        and _iso_day(plan.get("execution_day")) == "2026-09-30"
        and core.get("changed") is True
        and core.get("old_contract") is None
        and core.get("old_security_id") is None
        and core_old_qty is not None and math.isclose(core_old_qty, 0.0, abs_tol=1e-12)
        and current_qty is not None and target_qty is not None
        and current_qty > 0 and math.isclose(current_qty, target_qty, abs_tol=1e-12)
        and isinstance(current_contract, str) and bool(current_contract)
        and current_contract == target_contract
        and isinstance(target_contract, str) and bool(target_contract)
        and isinstance(target_security_id, str) and bool(target_security_id)
        and (current_security_id is None or current_security_id == target_security_id)
        and core.get("new_contract") == target_contract
        and core.get("new_security_id") == target_security_id
        and core_new_qty is not None and math.isclose(core_new_qty, target_qty, abs_tol=1e-12)
        and momentum.get("changed") is False
        and momentum.get("old_contract") is None
        and momentum.get("new_contract") is None
        and momentum.get("old_security_id") is None
        and momentum.get("new_security_id") is None
        and momentum_old_qty is not None and math.isclose(momentum_old_qty, 0.0, abs_tol=1e-12)
        and momentum_new_qty is not None and math.isclose(momentum_new_qty, 0.0, abs_tol=1e-12)
        and current_momentum_qty == target_momentum_qty == 0.0
    )


def validate_ordinary_put_plan(product: str, signal: dict[str, Any], next_trade_day: str) -> None:
    """Fail closed on a scheduled plan whose identity differs from its signal."""
    if signal.get("v14_ordinary_put_plan_status") != "scheduled_t_plus_1_open":
        return
    plan = signal.get("v14_ordinary_put_pending")
    if not isinstance(plan, dict) or not isinstance(plan.get("legs"), dict):
        raise ValueError(f"{product} scheduled ordinary Put plan is incomplete")
    signal_day = _iso_day(signal.get("market_date"))
    signal_next_day = _iso_day(signal.get("next_trade_date"))
    if (
        signal.get("product") != product
        or plan.get("product") != product
        or signal_day is None
        or _iso_day(plan.get("signal_day")) != signal_day
        or _iso_day(plan.get("execution_day")) != next_trade_day
        or signal_next_day != next_trade_day
        or set(plan["legs"]) != {"core", "momentum"}
    ):
        raise ValueError(f"{product} ordinary Put plan product/date/legs do not match the signal")

    # This valid special case deliberately has an old-core quantity of zero in
    # the v1.4 plan, while the parent ledger already holds the same IC contract.
    # It is not an executable plan; verify it strictly before generic matching.
    if ordinary_put_transition_identity_only(product, signal):
        return

    if product == "IC":
        current_contracts = {
            "core": signal.get("v14_core_put_current_contract"),
            "momentum": signal.get("put_current_contract"),
        }
        target_contracts = {
            "core": signal.get("v14_core_put_contract") or signal.get("put_target_contract"),
            "momentum": signal.get("put_target_contract"),
        }
        current_security_ids = {
            "core": (signal.get("v14_core_put_security_id")
                     if current_contracts["core"] == signal.get("v14_core_put_contract")
                     else signal.get("put_current_security_id")
                     if current_contracts["core"] == signal.get("put_current_contract") else None),
            "momentum": signal.get("put_current_security_id"),
        }
        target_security_ids = {
            "core": signal.get("v14_core_put_security_id") or signal.get("put_target_security_id"),
            "momentum": signal.get("put_target_security_id"),
        }
        current_quantities = {
            "core": signal.get("put_current_core_qty"),
            "momentum": signal.get("put_current_momentum_qty"),
        }
        target_quantities = {
            "core": signal.get("put_target_core_qty"),
            "momentum": signal.get("put_target_momentum_qty"),
        }
    else:
        current_contracts = {
            "core": signal.get("core_put_current_contract"),
            "momentum": signal.get("momentum_put_current_contract"),
        }
        target_contracts = {
            "core": signal.get("core_put_target_contract"),
            "momentum": signal.get("momentum_put_target_contract"),
        }
        current_quantities = {
            "core": signal.get("core_put_current_qty_normalized"),
            "momentum": signal.get("momentum_put_current_qty_normalized"),
        }
        target_quantities = {
            "core": signal.get("core_put_target_qty_normalized"),
            "momentum": signal.get("momentum_put_target_qty_normalized"),
        }
        current_security_ids = target_security_ids = {"core": None, "momentum": None}

    # Match the producer's profit_core_reference / ordinary scheduler. A route
    # switch owns the core exit; a concurrent 3x event owns its own core trade.
    # In either case only momentum may change in this ordinary T+1 plan.
    route = signal.get("v14_route_state", "future")
    action = signal.get("v14_action")
    executed = action == "EXECUTE_CORE_PUT_PROFIT3X_REENTER"
    profit_core = signal.get("v14_profit_pending") or executed or action == "CORE_PUT_PROFIT3X_REENTER"
    core_locked = route != "future" or profit_core
    if route != "future":
        if (route not in {"short_put", "assigned_etf", "cash_wait", "recovery_future"}
                or profit_core or _finite_qty(target_quantities["core"]) != 0.0
                or (product == "IC" and _finite_qty(signal.get("v14_core_put_qty")) != 0.0)):
            raise ValueError(f"{product} ordinary Put route has an invalid core target")
        current_contracts["core"] = target_contracts["core"] = None
        current_security_ids["core"] = target_security_ids["core"] = None
        current_quantities["core"] = target_quantities["core"] = 0.0
    elif profit_core:
        if executed:
            contract = signal.get("v14_core_put_contract") if product == "IC" else target_contracts["core"]
            security_id = signal.get("v14_core_put_security_id") if product == "IC" else None
            qty = _finite_qty(signal.get("v14_core_put_qty") if product == "IC" else target_quantities["core"])
            target_qty = _finite_qty(target_quantities["core"])
            executed_qty = _finite_qty(signal.get("v14_profit_open_executed_qty"))
            old_qty = _finite_qty(signal.get("v14_profit_open_old_qty"))
            current_qty = _finite_qty(current_quantities["core"])
            open_prices = [_finite_qty(signal.get(field)) for field in (
                "v14_profit_exit_open_price", "v14_profit_reentry_entry_premium")]
            if (signal.get("v14_profit_pending")
                    or signal.get("v14_profit_reentry_status") != "confirmed_open_research_price"
                    or _iso_day(signal.get("v14_profit_open_price_day")) != signal_day
                    or any(value is None or value <= 0 for value in open_prices)
                    or signal.get("v14_profit_open_executed_contract") != contract
                    or contract != target_contracts["core"]
                    or qty is None or executed_qty is None or target_qty is None
                    or not math.isclose(qty, target_qty, abs_tol=1e-12)
                    or not math.isclose(qty, executed_qty, abs_tol=1e-12)
                    or signal.get("v14_profit_open_old_contract") != current_contracts["core"]
                    or old_qty is None or current_qty is None or old_qty <= 0
                    or not math.isclose(old_qty, current_qty, abs_tol=1e-12)):
                raise ValueError(f"{product} ordinary Put core lacks confirmed 3x opening identity")
        else:
            contract = signal.get("v14_profit_old_contract")
            security_id = signal.get("v14_profit_old_security_id") if product == "IC" else None
            qty = _finite_qty(signal.get("v14_profit_old_qty"))
            current_qty = _finite_qty(current_quantities["core"])
            if (signal.get("v14_profit_pending") is not True
                    or contract != current_contracts["core"]
                    or qty is None or current_qty is None
                    or not math.isclose(qty, current_qty, abs_tol=1e-12)
                    or (product == "IC" and current_security_ids["core"] is not None
                        and security_id != current_security_ids["core"])):
                raise ValueError(f"{product} ordinary Put core differs from pending 3x held identity")
        if (not contract or qty is None or qty <= 0
                or (product == "IC" and not security_id)):
            raise ValueError(f"{product} ordinary Put 3x core reference is incomplete")
        current_contracts["core"] = target_contracts["core"] = contract
        current_security_ids["core"] = target_security_ids["core"] = security_id
        current_quantities["core"] = target_quantities["core"] = qty

    for name in ("core", "momentum"):
        leg = plan["legs"][name]
        if not isinstance(leg, dict):
            raise ValueError(f"{product} ordinary Put {name} leg is invalid")
        old_qty = _finite_qty(current_quantities[name])
        new_qty = _finite_qty(target_quantities[name])
        plan_old_qty = _finite_qty(leg.get("old_qty"))
        plan_new_qty = _finite_qty(leg.get("new_qty"))
        if None in (old_qty, new_qty, plan_old_qty, plan_new_qty):
            raise ValueError(f"{product} ordinary Put {name} quantities must be finite")
        if min(old_qty, new_qty, plan_old_qty, plan_new_qty) < 0:
            raise ValueError(f"{product} ordinary Put {name} quantities must be nonnegative")
        if ((old_qty > 0 and not current_contracts[name])
                or (new_qty > 0 and not target_contracts[name])):
            raise ValueError(f"{product} ordinary Put {name} positive quantity lacks contract")
        expected_old_contract = current_contracts[name]
        expected_new_contract = target_contracts[name]
        if old_qty == 0:
            expected_old_contract = None
        if new_qty == 0:
            expected_new_contract = None
        expected_new_security = (
            target_security_ids[name] if product == "IC" and new_qty > 0 else None
        )
        old_security_matches = (
            leg.get("old_security_id") is None if product != "IC" or old_qty == 0
            else leg.get("old_security_id") == current_security_ids[name]
            if current_security_ids[name] is not None or (core_locked and name == "core")
            else True
        )
        changed = expected_old_contract != expected_new_contract or not math.isclose(
            old_qty, new_qty, abs_tol=1e-12
        )
        if (
            leg.get("old_contract") != expected_old_contract
            or leg.get("new_contract") != expected_new_contract
            or not math.isclose(plan_old_qty, old_qty, abs_tol=1e-12)
            or not math.isclose(plan_new_qty, new_qty, abs_tol=1e-12)
            or not old_security_matches
            or leg.get("new_security_id") != expected_new_security
            or type(leg.get("changed")) is not bool
            or leg.get("changed") != changed
        ):
            raise ValueError(f"{product} ordinary Put {name} leg differs from signal identity/quantity")


def action_parts(signal: dict[str, Any], product: str = "") -> list[str]:
    labels = {
        "core_action": "核心合约",
        "momentum_action": "动量袖",
        "grid_action": "网格",
        "put_action": "Put",
        "call_action": "Call",
    }
    parts = []
    for key, label in labels.items():
        value = str(signal.get(key, "")).strip().upper()
        if value not in HOLD_ACTIONS:
            parts.append(f"{label} {value}")
    if signal.get("v14_profit_reentry_status") == "scheduled_t_plus_1_open":
        parts.append("核心买Put三倍兑现次日开盘计划")
    if (signal.get("v14_ordinary_put_plan_status") == "scheduled_t_plus_1_open"
            and not ordinary_put_transition_identity_only(product or str(signal.get("product", "")), signal)):
        parts.append("普通核心/动量买Put次日开盘计划")
    return parts


def put_text(product: str, signal: dict[str, Any], target: bool) -> str:
    prefix = "target" if target else "current"
    maintenance_prefix = (f"{signal.get('market_date')}收盘维护候选，盘尾复核："
                          if target and put_display.monthly_candidate(signal) else "")
    contract = signal.get(f"put_{prefix}_contract") or "无"
    if product == "IC":
        qty = signal.get(f"put_{prefix}_total_qty", 0)
        # Historical sparse results have only a combined quantity. Modern
        # results record independent core and momentum identities and sizes.
        core_qty = signal.get(f"put_{prefix}_core_qty")
        momentum_qty = signal.get(f"put_{prefix}_momentum_qty")
        if core_qty is not None and momentum_qty is not None:
            def leg_contract(leg: str, leg_qty: Any) -> str:
                if leg_qty == 0:
                    return "无"
                return put_display.ic_contract(signal, prefix, leg) or "N/A（result合约缺失）"
            return (
                maintenance_prefix + f"合计{number(qty)}张（核心{number(core_qty)}张 {leg_contract('core', core_qty)}；"
                f"动量{number(momentum_qty)}张 {leg_contract('momentum', momentum_qty)}）"
            )
        return maintenance_prefix + f"{number(qty)}张 {contract}"
    core_qty = signal.get(f"core_put_{prefix}_qty_normalized", 0)
    momentum_qty = signal.get(f"momentum_put_{prefix}_qty_normalized", 0)
    total_qty = signal.get(f"total_put_{prefix}_qty_normalized", 0)
    core_contract = signal.get(f"core_put_{prefix}_contract") or "无"
    momentum_contract = signal.get(f"momentum_put_{prefix}_contract") or "无"
    return (
        maintenance_prefix + f"合计{number(total_qty)}张（核心{number(core_qty)}张 {core_contract}；"
        f"动量{number(momentum_qty)}张 {momentum_contract}）"
    )


def call_text(signal: dict[str, Any], target: bool) -> str:
    prefix = "target" if target else "current"
    contract = signal.get(f"call_{prefix}_contract") or "无"
    if not target:
        qty = 2 if bool(signal.get("call_has_position")) else 0
    else:
        qty = signal.get("call_target_qty_normalized", 0)
    return f"{number(qty)}张 {contract}"


def escaped(value: Any) -> str:
    return html.escape(str(value if value is not None else "N/A"), quote=True)


def action_cn(value: Any) -> str:
    normalized = str(value or "HOLD").strip().upper()
    return ACTION_CN.get(normalized, normalized)


def product_action_text(signal: dict[str, Any]) -> str:
    labels = {
        "core_action": "核心",
        "momentum_action": "动量",
        "grid_action": "网格",
        "put_action": "Put",
        "call_action": "Call",
    }
    parts = []
    for key, label in labels.items():
        value = str(signal.get(key, "")).strip().upper()
        if value not in HOLD_ACTIONS:
            parts.append(f"{label}：{action_cn(value)}")
    if signal.get("v14_profit_reentry_status") == "scheduled_t_plus_1_open":
        parts.append("核心买Put：下一交易日开盘兑现计划")
    return "；".join(parts) or "维持现状"


def momentum_reason(product: str, signal: dict[str, Any]) -> str:
    score = signal.get("momentum_score")
    new_ic = product == "IC" and str(signal.get("market_date", ""))[:10] >= "2026-10-08"
    abs_days = 40 if new_ic else 20
    abs_value = signal.get("momentum_abs") if new_ic else signal.get("momentum_abs20")
    weight = number(signal.get("momentum_next_weight"))
    if score is None or abs_value is None:
        return f"动量袖：下一交易日权重为{weight}；详细判定字段未随本次结果输出。"
    score_value = float(score)
    legacy_state_missing = (
        not new_ic and str(signal.get("market_date", ""))[:10] >= "2026-09-16"
        and "momentum_abs_on" not in signal
    )
    if legacy_state_missing:
        prefix = (
            f"动量袖：Score {decimal(score)}（{'>0' if score_value > 0 else '≤0'}），"
            f"Abs{abs_days} {percent(abs_value)}；历史账本未保存 Abs 放行状态，"
            "以已记录的目标权重为准。"
        )
    else:
        abs_on = bool(signal.get("momentum_abs_on", float(abs_value) > 0))
        base_weight = 0.0 if score_value <= 0 else (1.0 if abs_on else 0.5)
        prefix = (
            f"动量袖：Score {decimal(score)}（{'>0' if score_value > 0 else '≤0'}），"
            f"Abs{abs_days} {percent(abs_value)}（{'放行' if abs_on else '不放行'}），"
            f"基础权重{number(base_weight)}。"
        )
    if product == "IC":
        base_dd = signal.get("momentum_base_dd")
        dd_text = percent(base_dd) if base_dd is not None else "N/A"
        defense = bool(signal.get("momentum_nav_defense", False))
        gate_text = "触发并减半" if defense else "未触发"
        return (
            f"{prefix}{'Abs40>0即恢复，不使用两日防抖；' if new_ic else ''}"
            f"基础NAV回撤{dd_text}，6%防守门槛{gate_text}；"
            f"最终目标权重{weight}，对应期货名义"
            f"{number(0.5 * float(signal.get('momentum_next_weight', 0)))}倍。"
        )
    placeholder = bool(signal.get("momentum_volume_placeholder", False))
    ratio = signal.get("momentum_volume_ratio")
    volume_text = (
        "盘中冻结为上一确认交易日状态"
        if placeholder
        else f"Volume/MA160={decimal(ratio)}，{'通过' if signal.get('momentum_volume_pass', True) else '未通过'}0.85门槛"
    )
    hot_text = "触发清仓" if signal.get("momentum_score_hot", False) else "未触发"
    return (
        f"{prefix}{volume_text}；Score≥150极热门槛{hot_text}；"
        f"最终目标权重{weight}，对应期货名义"
        f"{number(0.5 * float(signal.get('momentum_next_weight', 0)))}倍。"
    )


def grid_reason(product: str, signal: dict[str, Any]) -> str:
    if signal.get("grid_action") == "UNCONFIRMED":
        return "独立网格：百分位缺少合格读数，网格及合计目标未确认；其他模块照常展示。" + jiu_reference_text(signal)
    if str(signal.get("market_date", ""))[:10] >= "2026-10-08":
        fear = signal.get("fear_greed_index")
        fear_text = "缺失" if fear is None else decimal(fear)
        return (
            f"独立网格：估值分{decimal(signal.get('score'))}，恐贪{fear_text}"
            f"（{signal.get('fear_data_status', 'N/A')}）；"
            f"当前{number(signal.get('grid_current'))}倍，目标{number(signal.get('grid_target'))}倍；"
            f"入场来源{signal.get('grid_entry_source_current', 'N/A')}→"
            f"{signal.get('grid_entry_source_target', 'N/A')}，"
            f"原因{signal.get('grid_transition_reason', 'N/A')}。"
            "恐慌入场按≥50退出，估值入场按原估值线退出；网格不配Put或Call。"
            + str(signal.get("fear_source_warning") or "") + jiu_reference_text(signal)
        )
    entry, exit_ = ((0.5, 1.0) if product == "IC" else (1.6, 2.0))
    score = signal.get("score")
    current = float(signal.get("grid_current", 0) or 0)
    target = float(signal.get("grid_target", 0) or 0)
    if score is None:
        return f"估值网格：当前{number(current)}倍，目标{number(target)}倍；估值分未随本次结果输出。"
    score_value = float(score)
    if current <= 0 and target > 0:
        conclusion = f"已触发入场线≤{entry:g}，因此加至{number(target)}倍"
    elif current > 0 and target <= 0:
        conclusion = f"已触发退出线≥{exit_:g}，因此降至0倍"
    elif current <= 0:
        conclusion = f"未触发入场线≤{entry:g}，因此继续0倍"
    else:
        conclusion = f"未触发退出线≥{exit_:g}，因此维持{number(target)}倍"
    return f"估值网格：当前估值分{decimal(score_value)}；{conclusion}。网格腿不配置Put或Call。"


def im_put_calendar_reason(signal: dict[str, Any]) -> str:
    """Explain the execution-day Put calendar in the delivered digest."""
    revision = signal.get("im_put_calendar_revision")
    if not revision:
        return ""
    reference_future = signal.get("put_reference_future") or "N/A"
    price_date = signal.get("put_reference_price_date") or signal.get("market_date") or "N/A"
    execution_date = signal.get("put_monthly_reset_execution_date") or "N/A"
    if bool(signal.get("put_monthly_reset_preview")):
        return (
            f"月度Put维护预告：{execution_date}执行；当前为预告日，沿用既有合约，"
            f"当天按已持有IM价格重选，当前不锁定新合约；日历版本{revision}。"
        )
    if bool(signal.get("option_monthly_reset_due")):
        return (
            f"月度Put执行日：{execution_date}；参考期货{reference_future}，取价日{price_date}；"
            f"当天按该参考期货价格重选Put并写入执行记录，不提前锁定；日历版本{revision}。"
        )
    return (
        f"月度Put日历：本日非重置执行日，沿用既有合约；参考期货{reference_future}，"
        f"取价日{price_date}；日历版本{revision}。"
    )


def put_reason(product: str, signal: dict[str, Any]) -> str:
    if product == "IC":
        return (
            f"Put保护：估值分{decimal(signal.get('score'))}，处于{signal.get('valuation_tier_label', '未知估值档')}，"
            f"估值目标为{percent(signal.get('valuation_put_delta'), 0)} Delta；MOM120为"
            f"{percent(signal.get('momentum_120'))}，核心袖下限为{percent(signal.get('mom120_floor_delta'), 0)} Delta。"
            f"两者取高后由{signal.get('core_put_driver', '规则判定')}主导，乘0.5倍核心袖贡献"
            f"{percent(signal.get('core_put_target_delta'), 1)} Delta；动量袖只看估值，贡献"
            f"{percent(signal.get('momentum_put_target_delta'), 1)}，合计目标"
            f"{percent(signal.get('total_put_target_delta'), 1)}。"
        )
    text = (
        f"Put保护：估值分{decimal(signal.get('score'))}；绝对轴为"
        f"{signal.get('absolute_valuation_tier_label', '未知档位')}，相对轴为"
        f"{signal.get('relative_valuation_tier_label', '未知档位')}。估值给每1倍核心IM "
        f"{number(signal.get('valuation_puts_per_full_core'))}张，而MOM120为"
        f"{percent(signal.get('momentum_120'))}，负动量下限给"
        f"{number(signal.get('mom120_floor_puts_per_full_core'))}张；两者取高，本次由"
        f"{signal.get('core_put_driver', '规则判定')}主导。只覆盖0.5倍核心袖，所以规范化目标为"
        f"{number(signal.get('core_put_target_qty_normalized'))}张；{signal.get('im_put_policy_description', '历史政策：独立动量Put按父规则数量×0.5×动量执行权重配置')}，"
        f"本次目标为{number(signal.get('momentum_put_target_qty_normalized'))}张 "
        f"{signal.get('momentum_put_target_contract') or '无'}；合计目标"
        f"{number(signal.get('total_put_target_qty_normalized'))}张。网格不配Put。"
    )
    calendar_reason = im_put_calendar_reason(signal)
    return f"{text} {calendar_reason}".strip() if calendar_reason else text


def call_reason(product: str, signal: dict[str, Any]) -> str:
    if product == "IC":
        return "Call：IC 1.4规则明确禁止卖Call，所以当前和目标都为无。"
    target = str(signal.get("call_target") or "未输出Call目标说明")
    if str(signal.get("market_date") or "")[:10] >= "2026-09-26":
        if bool(signal.get("call_has_position")):
            return f"Call：当前模型账本有旧Call，仅给出买回目标，不再救援或新开；{target}。"
        return f"Call：当前没有旧Call，目标保持空仓；fix6不再卖Call；{target}。"
    if not bool(signal.get("call_has_position")):
        return f"Call：当前没有旧Call，只评估D10候选；{target}。"
    otm = signal.get("call_otm")
    if otm is None:
        return f"Call：当前有旧Call；{target}。"
    trigger = "已触发" if float(otm) <= 0.05 else "未触发"
    return f"Call：旧Call虚值度{percent(otm)}，5%救援线{trigger}；{target}。"


def roll_reason(signal: dict[str, Any]) -> str:
    if signal.get("roll_policy", {}).get("tenor") == "legacy_monthly_pre_effective":
        return "展期：本行情日早于r7生效日，保留原月度历史续接；2026-09-04起IM季度T-1、IC季度T-3，期权维护独立。"
    current = signal.get("core_target") or signal.get("core_current") or "当前核心合约"
    next_core = signal.get("next_core")
    roll_date = signal.get("roll_date")
    if bool(signal.get("scheduled_roll_completed")):
        prefix = "本次季度换仓已计入已确认规则仓位，不再重复算作下一交易日动作"
    else:
        prefix = f"核心合约动作是{action_cn(signal.get('core_action'))}"
    if next_core and roll_date and current != next_core:
        return f"展期：{prefix}；{current}规则到期日为{roll_date}，按 T-{signal.get('roll_policy', {}).get('trading_days_before_expiry', 'N/A')} 在 {signal.get('roll_execution_date', 'N/A')} 收盘转入{next_core}；Put维护独立。"
    return f"展期：{prefix}。"


def quarter_spread_reason(signal: dict[str, Any]) -> str:
    value = signal.get("quarter_spread") or {}
    if value.get("status") != "ok":
        return "季月价差：N/A（" + str(value.get("reason", "缺少同日完整报价")) + "）"
    return (
        f"季月价差（近季－远季）：{value['near_contract']} {decimal(value['near_price'], 2)}"
        f" − {value['far_contract']} {decimal(value['far_price'], 2)} = {float(value['points']):+.2f}点"
        f"（{float(value['ratio']):+.2%}；按到期间隔折算{float(value['annualized']):+.2%}/年，非保证收益）；"
        f"行情 {value['source_date']}，{value['source']}"
    )


def month_spread_reasons(signal: dict[str, Any]) -> list[str]:
    lines = []
    for key, label in (("month_next_spread", "当月－次月"), ("month_quarter_spread", "当月－当季")):
        value = signal.get(key) or {}
        if value.get("status") != "ok":
            lines.append(f"月度价差（{label}）：N/A（" + str(value.get("reason", "缺少同日完整报价")) + "）")
            continue
        annual = ("同一合约，年化不适用" if value.get("same_contract") else
                  f"按到期间隔折算{float(value['annualized']):+.2%}/年，非保证收益")
        lines.append(
            f"月度价差（{label}）：{value['near_contract']} {decimal(value['near_price'], 2)}"
            f" − {value['far_contract']} {decimal(value['far_price'], 2)} = {float(value['points']):+.2f}点"
            f"（{float(value['ratio']):+.2%}；{annual}）；行情 {value['source_date']}，{value['source']}"
        )
    return lines


def v14_route_reason(signal: dict[str, Any], product: str = "") -> str:
    scope = signal.get("v14_signal_scope", "research_model_signal_only")
    route = signal.get("v14_route_state", "future")
    action = signal.get("v14_action", "HOLD")
    reason = signal.get("v14_action_reason", "N/A")
    text = f"1.4固定核心路由：{route}；动作{action}；原因{reason}；范围{scope}。"
    profit_status = signal.get("v14_profit_reentry_status")
    if profit_status == "scheduled_t_plus_1_open":
        text += (f"核心买Put三倍兑现：{signal.get('market_date')}收盘确认，"
                 f"{signal.get('v14_profit_execution_day')}开盘计划卖旧"
                 f"{signal.get('v14_profit_old_contract')}、买预选"
                 f"{signal.get('v14_profit_reentry_contract')}，数量"
                 f"{signal.get('v14_profit_reentry_qty')}；尚未成交。")
    elif profit_status == "confirmed_open_research_price":
        text += (f"核心买Put三倍兑现纸面确认：{signal.get('v14_profit_open_price_day')}开盘"
                 f"卖旧{signal.get('v14_profit_open_old_contract')} @ {signal.get('v14_profit_exit_open_price')}，"
                 f"买新{signal.get('v14_profit_open_executed_contract')} @ "
                 f"{signal.get('v14_profit_reentry_entry_premium')}，数量"
                 f"{signal.get('v14_profit_open_executed_qty')}；并非账户成交。")
    ordinary_status = signal.get("v14_ordinary_put_open_status")
    if ordinary_status == "confirmed_open_research_price":
        prices = signal.get("v14_ordinary_put_open_prices") or []
        rendered = "、".join(f"{item.get('contract')} @ {item.get('openprice')}" for item in prices)
        text += f"普通核心/动量买Put前次计划已按指定日开盘价纸面核验：{rendered}；并非账户成交。"
    elif ordinary_status and ordinary_status != "awaiting_execution_day":
        text += (f"普通核心/动量买Put前次开盘计划关闭：{ordinary_status}；"
                 f"{signal.get('v14_ordinary_put_open_reason', '未形成可核验开盘成交')}。")
    if ordinary_put_transition_identity_only(product or str(signal.get("product", "")), signal):
        text += "普通核心买Put内部迁移身份已与正式持仓对齐；合约和数量均未变化，不构成调整。"
    elif signal.get("v14_ordinary_put_plan_status") == "scheduled_t_plus_1_open":
        plan = signal.get("v14_ordinary_put_pending") or {}
        changes = "、".join(
            f"{name} {leg.get('old_contract') or '空仓'}→{leg.get('new_contract') or '空仓'} "
            f"{leg.get('old_qty')}→{leg.get('new_qty')}"
            for name, leg in (plan.get("legs") or {}).items() if leg.get("changed")
        )
        text += (
            f"普通核心/动量买Put：按{plan.get('signal_day')}收盘口径补录模型计划；"
            f"实际创建{plan.get('created_at')}；下一模型执行日{plan.get('execution_day')}；{changes}；尚非成交。"
            if plan.get("creation_timing") == "retrospective_replay" else
            f"普通核心/动量买Put：{plan.get('signal_day')}收盘预选，"
            f"{plan.get('execution_day')}开盘待核验；{changes}；尚非成交。")
    if isinstance(signal.get("v14_expiry_conditional_signal"), dict):
        text += (
            "到期条件信号：模型结算依据尚待核验；价外失效则结束卖Put周期并返回普通期货路线；"
            "价内行权/现金结算则进入模型承接与恢复路线。实际账户操作由用户自行处理。"
        )
    return text


def product_reasons(product: str, signal: dict[str, Any]) -> list[str]:
    return [
        valuation_disclosure(signal),
        momentum_reason(product, signal),
        grid_reason(product, signal),
        put_reason(product, signal),
        f"Put报价口径：{signal.get('put_market') or 'N/A（缺少报价证据）'}",
        call_reason(product, signal),
        roll_reason(signal),
        quarter_spread_reason(signal),
        *(month_spread_reasons(signal) if any(key in signal for key in ("month_next_spread", "month_quarter_spread"))
          or str(signal.get("market_date", ""))[:10] >= "2026-10-18" else []),
        v14_route_reason(signal, product),
    ]


def valuation_disclosure(signal: dict[str, Any]) -> str:
    p = signal.get("valuation_provenance") or {}
    if p.get("mode") == "vip_actual":
        text = f"真实PE/PB：乐咕VIP，数据日期 {p.get('real_data_date')}。"
    else:
        text = ("代理估值／非当日真实PE、PB："
                f"{p.get('reason', '旧账本未记录VIP接入，保留原代理结果')}；"
                f"可用真实数据日期 {p.get('real_data_date') or '未收到/未通过校验'}；"
                f"代理锚点 {p.get('proxy_anchor_date', '2026-08-14')}，按指数价格比例推算。")
    rate = p.get("gov10y")
    if rate:
        if rate.get("mode") == "official_actual":
            text += (f"国债利率：中债官方10年期 {float(rate['yield_decimal']):.4%}，"
                     f"数据日期 {rate['used_date']}（估值与期权定价共用）。")
        else:
            text += (f"国债利率回退：{rate['reason']}；沿用 {rate['used_date']} 冻结值 "
                     f"{float(rate['yield_decimal']):.4%}，非当日利率；"
                     f"本次源日期 {rate.get('observed_date') or '未取得'}（估值与期权定价共用）。")
        return text + "股息和相对估值阈值沿用原冻结口径。"
    return text + "国债收益率、股息和相对估值阈值沿用原冻结口径。"


def valuation_banner(signals: dict[str, Any]) -> str:
    return "；".join(f"{product}：{valuation_disclosure(signals[product])}" for product in ("IC", "IM"))


def reasons_html(product: str, signal: dict[str, Any]) -> str:
    items = "".join(
        f'<li style="margin:0 0 9px;padding-left:2px;">{escaped(reason)}</li>'
        for reason in product_reasons(product, signal)
    )
    return f'''<div style="margin:4px 10px 12px;padding:14px 14px 5px;background:#f8fafc;border:1px solid #e4e7ec;border-radius:10px;color:#344054;font-size:13px;line-height:1.65;">
      <div style="margin-bottom:8px;color:#101828;font-size:14px;font-weight:750;">为什么是这个结果</div>
      <ol style="margin:0;padding-left:20px;">{items}</ol>
    </div>'''


def leg_row(label: str, current: Any, target: Any, note: str = "") -> str:
    note_html = (
        f'<div style="margin-top:4px;color:#667085;font-size:12px;line-height:1.45;">{escaped(note)}</div>'
        if note
        else ""
    )
    return f'''<tr>
      <td style="padding:11px 10px;border-top:1px solid #edf0f5;color:#475467;font-size:13px;vertical-align:top;width:30%;">{escaped(label)}</td>
      <td style="padding:11px 10px;border-top:1px solid #edf0f5;color:#101828;font-size:14px;line-height:1.5;vertical-align:top;">
        <span style="color:#667085;">{escaped(current)}</span>
        <span style="padding:0 7px;color:#98a2b3;">→</span>
        <strong style="color:#101828;">{escaped(target)}</strong>{note_html}
      </td>
    </tr>'''


def iv_warning_text(signal: dict[str, Any]) -> str:
    return str((signal.get("iv_warning") or {}).get("text", "IV预警：N/A（历史记录未保存IV监测值）"))


def iv_warning_html(signal: dict[str, Any]) -> str:
    level = (signal.get("iv_warning") or {}).get("level", "unavailable")
    color = {"critical": "#b42318", "high": "#b54708", "normal": "#344054", "unavailable": "#667085"}.get(level, "#667085")
    return f'<div style="margin:12px 0;padding:10px;border:2px solid {color};color:{color};font-weight:700;">{escaped(iv_warning_text(signal))}</div>'


def fear_observation_text(payload: dict[str, Any]) -> str:
    day = str(payload.get("market_date", "未知日期"))
    if day < "2026-10-08":
        return f"当日恐慌指数（{day}）：历史日报未接入该来源，未核验。"
    if payload.get("status") in {"ok", "partial"} and payload.get("signals"):
        signal = (payload.get("signals") or {}).get("IC") or {}
        score = signal.get("fear_greed_index")
        status = signal.get("fear_data_status", "N/A")
        data_day = signal.get("fear_data_date") or "未发布"
    else:
        score = payload.get("fear_greed_index")
        status = payload.get("fear_data_status", "unavailable")
        data_day = payload.get("fear_data_date") or "未发布"
    value = "未核验" if score is None else decimal(score)
    basis = "；沿用上一交易日（当日未发布）" if status in {
        "intraday_previous_session", "close_previous_session"
    } else ""
    title = f"信号日{day}的恐慌输入" if basis else f"当日恐慌指数（{day}）"
    signal = {**((payload.get("signals") or {}).get("IC") or payload), **payload.get("jiu_reference", {})}
    warning = signal.get("fear_source_warning") or ("百分位CSV与网页已完成双侧验证。" if signal.get("fear_cross_validation") == "matched" else "")
    reference = jiu_reference_text(signal)
    extra = " ".join(x for x in (warning, reference) if x)
    return f"{title}：{value}；数据日 {data_day}；状态 {status}{basis}。" + (" " + extra if extra else "")


def jiu_reference_text(signal):
    if "jiu_status" not in signal and "jiu_fear_greed_index" not in signal:
        return ""
    value = signal.get("jiu_fear_greed_index")
    if value is None:
        return "9圈恐慌贪婪指数：暂未取得；仅供参考，不参与网格信号。"
    mismatch = "；与百分位数据日不同" if signal.get("jiu_data_date") != signal.get("fear_data_date") else ""
    return (f"9圈恐慌贪婪指数：{decimal(value)}；数据日 {signal.get('jiu_data_date')}"
            f"；采集 {signal.get('jiu_retrieved_at')}{mismatch}；仅供参考，不参与网格信号。"
            "官网：https://jiucaishuo.com/indicators?tab=fg")


def product_card(product: str, signal: dict[str, Any], actionable: bool) -> str:
    title = "IC / 中证500" if product == "IC" else "IM / 中证1000"
    accent = "#f79009" if actionable else "#12b76a"
    badge_bg = "#fff4e5" if actionable else "#ecfdf3"
    badge_text = product_action_text(signal)
    current_total = number(signal.get("total_units_current"))
    target_total = number(signal.get("total_units_target"))
    core_current = signal.get("core_current") or "当前核心合约"
    core_target = signal.get("core_target") or core_current
    momentum_current = f"权重 {number(signal.get('momentum_current_weight'))}"
    momentum_target = f"权重 {number(signal.get('momentum_next_weight'))}"
    grid_current = f"{number(signal.get('grid_current'))}倍"
    grid_target = f"{number(signal.get('grid_target'))}倍"
    put_current = put_text(product, signal, False)
    put_target = put_text(product, signal, True)
    call_current = "禁止卖Call" if product == "IC" else call_text(signal, False)
    call_target = "禁止卖Call" if product == "IC" else call_text(signal, True)
    rows = "".join(
        [
            leg_row("核心期货", core_current, core_target, f"动作：{action_cn(signal.get('core_action'))}"),
            leg_row("动量袖", momentum_current, momentum_target, f"动作：{action_cn(signal.get('momentum_action'))}"),
            leg_row("估值/恐慌网格" if str(signal.get("market_date", ""))[:10] >= "2026-10-08" else "估值网格",
                    grid_current, grid_target, f"动作：{action_cn(signal.get('grid_action'))}"),
            leg_row("Put保护", put_current, put_target, f"动作：{action_cn(signal.get('put_action'))}"),
            leg_row("Call", call_current, call_target, "IC明确禁止Call" if product == "IC" else f"动作：{action_cn(signal.get('call_action'))}"),
        ]
    )
    put_details = put_display.html_tables(product, signal)
    put_details_row = f'\n  <tr><td>{put_details}</td></tr>' if put_details else ''
    return f'''<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 16px;background:#ffffff;border:1px solid #e4e7ec;border-radius:14px;overflow:hidden;border-collapse:separate;">
  <tr>
    <td style="padding:18px 18px 14px;border-left:5px solid {accent};">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
        <tr>
          <td style="font-size:19px;font-weight:750;color:#101828;">{escaped(title)}</td>
          <td align="right"><span style="display:inline-block;padding:5px 9px;border-radius:999px;background:{badge_bg};color:{accent};font-size:12px;font-weight:700;">{escaped(badge_text)}</span></td>
        </tr>
      </table>
      {iv_warning_html(signal)}
      <div style="margin-top:14px;color:#667085;font-size:12px;">期货总仓</div>
      <div style="margin-top:3px;font-size:24px;line-height:1.2;color:#101828;font-weight:760;">{escaped(current_total)}倍 <span style="color:#98a2b3;font-weight:500;">→</span> <span style="color:{accent};">{escaped(target_total)}倍</span></div>
    </td>
  </tr>
  <tr><td style="padding:0 8px 8px;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">{rows}</table></td></tr>
  <tr><td>{reasons_html(product, signal)}</td></tr>{put_details_row}
</table>'''


def build_success_html(payload: dict[str, Any], run_url: str) -> str:
    signals = {p: {**s, **payload.get("jiu_reference", {})} for p, s in payload.get("signals", {}).items()}
    if set(signals) != {"IC", "IM"}:
        raise ValueError("result must contain IC and IM signals")
    actions = {product: action_parts(signals[product], product) for product in ("IC", "IM")}
    actionable = any(actions.values())
    notices = calendar_notices(payload)
    opening_notices = execution_notices(payload)
    opening_attention = any(put_execution_contract.needs_attention(signal) for signal in signals.values())
    realtime = str(payload.get("publication_mode", "close_confirmed")) == "realtime"
    day = str(payload.get("market_date" if realtime else "completed_day", "未知日期"))
    mode_text = "盘中实时预估" if realtime else "收盘确认"
    headline = "存在预估调整" if actionable and realtime else "盘中暂时无需调整" if realtime else "存在下一交易日调整" if actionable else "无需调整"
    warning = (
        "盘中结果会随行情变化，必须等待收盘确认；本邮件不会自动下单。"
        if realtime
        else "这是研究审计信号，不是账户持仓，也不会自动下单；实际成交、行权、结算和交割由用户自行处理。"
    )
    if payload.get("signal_timing") == "retrospective_replay":
        mode_text, headline = "历史模型补录", "参考差异" if actionable else "模型持仓维持"
        warning = (f"实际报告完成 {payload.get('report_generated_at', payload.get('generated_at', 'N/A'))}；retrospective_replay。"
                   "这是历史模型补录，不是原信号日已发布的前瞻调整目标或账户成交。")
    if payload.get("status") == "partial":
        headline = "网格及合计目标未确认，其他模块照常展示"
        warning = "百分位没有合格读数；日报继续发布。网格及合计目标以N/A表示，不代表零仓或维持仓位。"
    if notices:
        headline = "月度Put维护日历需核对" if payload.get("signal_timing") == "retrospective_replay" else "月度Put需维护" + ("，另有常规目标调整" if actionable else "")
    elif opening_attention:
        headline = "Put开盘计划或成本待核验" + ("，另有常规目标调整" if actionable else "")
    accent = "#f79009" if actionable or notices or opening_attention else "#12b76a"
    banner_bg = "#fff7ed" if actionable or notices or opening_attention else "#ecfdf3"
    notice_html = "".join(
        '<div style="margin-bottom:16px;padding:16px;background:#fff7ed;border:2px solid #f79009;border-radius:12px;color:#7a2e0e;font-size:16px;line-height:1.7;">'
        + escaped(notice.replace("**", "")).replace("\n\n", "<br><br>") + '</div>'
        for notice in notices
    )
    if opening_notices:
        notice_html += '<div style="margin-bottom:16px;padding:16px;background:#fffaeb;border:2px solid #f79009;line-height:1.7;">' + "<br><br>".join(escaped(line) for line in opening_notices) + '</div>'
    link = (
        f'<a href="{escaped(run_url)}" style="color:#175cd3;text-decoration:none;font-weight:650;">查看GitHub运行记录 →</a>'
        if run_url
        else ""
    )
    cards = "".join(
        product_card(product, signals[product], bool(actions[product]))
        for product in ("IC", "IM")
    )
    preheader = f"{mode_text}｜{headline}｜信号日 {day}"
    return f'''<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f2f4f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Arial,sans-serif;color:#101828;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{escaped(preheader)}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f2f4f7;"><tr><td align="center" style="padding:20px 10px;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:680px;">
  <tr><td style="padding:24px 22px;background:#101828;border-radius:16px 16px 0 0;color:#ffffff;">
    <div style="font-size:12px;letter-spacing:.08em;color:#b9c2d0;font-weight:700;">IC / IM 1.4-r1 · {escaped(mode_text)}</div>
    <div style="margin-top:8px;font-size:27px;line-height:1.25;font-weight:780;">{escaped(headline)}</div>
    <div style="margin-top:10px;color:#d0d5dd;font-size:14px;line-height:1.55;">信号日 {escaped(day)} · 下一交易日 {escaped(payload.get('next_trade_day', 'N/A'))}</div>
  </td></tr>
  <tr><td style="padding:18px 18px 2px;background:#f8fafc;">
    {notice_html}<div style="margin-bottom:16px;padding:14px 15px;background:{banner_bg};border:1px solid {accent}33;border-radius:12px;color:#344054;font-size:14px;line-height:1.6;"><strong style="color:{accent};">{escaped(headline)}</strong><br>{escaped(warning)}</div>
    <div style="margin-bottom:16px;padding:14px 15px;background:#fffaeb;border:1px solid #fedf89;border-radius:12px;color:#7a2e0e;font-size:14px;line-height:1.6;"><strong>今日恐慌指数</strong><br>{escaped(fear_observation_text(payload))}</div>
    <div style="margin-bottom:16px;padding:14px 15px;background:#fffaeb;border:1px solid #fedf89;border-radius:12px;color:#7a2e0e;font-size:14px;line-height:1.6;"><strong>估值数据来源</strong><br>{escaped(valuation_banner(signals))}</div>
    <div style="margin-bottom:16px;font-size:14px;line-height:1.6;">{escaped(payload.get('grid_release_note', ''))}</div>
    {cards}
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:2px 0 16px;background:#ffffff;border:1px solid #e4e7ec;border-radius:12px;">
      <tr><td style="padding:15px 16px;color:#667085;font-size:12px;line-height:1.65;">
        <strong style="color:#344054;">审计状态</strong><br>
        已核验账本日 {escaped(payload.get('verified_day', 'N/A'))} · 序号 {escaped(payload.get('sequence', 'N/A'))} · 摘要 {escaped(str(payload.get('digest', ''))[:12])}<br>
        构建 {escaped(payload.get('build', 'N/A'))} · 本次补写 {escaped(payload.get('advanced_sessions', 'N/A'))} 个交易日
      </td></tr>
    </table>
  </td></tr>
  <tr><td style="padding:16px 20px 22px;background:#ffffff;border-radius:0 0 16px 16px;color:#667085;font-size:12px;line-height:1.6;">
    完整逐腿解释见附件。{link}
  </td></tr>
</table>
</td></tr></table>
</body></html>'''


def failed_calendar_notices(payload: dict[str, Any]) -> list[str]:
    """Derive only dated calendar facts from this attempt's generation clock.

    Failed output must never borrow a prior signal's date, prices or targets.
    An absent, naive, stale or future generation clock gives no reminder.
    """
    try:
        if payload.get("status") != "failed":
            return []
        from datetime import datetime, time, timedelta
        from zoneinfo import ZoneInfo
        mode = payload.get("publication_mode")
        if mode not in {"realtime", "close_confirmed"} or payload.get("signal_timing") == "retrospective_replay":
            return []
        stamp = datetime.fromisoformat(str(payload.get("report_generated_at") or payload.get("generated_at")))
        now = delivery_contract._validation_clock()
        if stamp.tzinfo is None or now.tzinfo is None or stamp > now:
            return []
        clock = stamp.astimezone(ZoneInfo("Asia/Shanghai"))
        day = clock.date()
        if (day != now.astimezone(ZoneInfo("Asia/Shanghai")).date()
                or day.isoformat() != payload.get("market_date") or not delivery_contract.is_session(day)):
            return []
        intraday = mode == "realtime"
        if (intraday and not time(9, 30) <= clock.time() < time(15)) or (not intraday and clock.time() < time(15)):
            return []
        first = day.replace(day=1)
        execution = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
        while not delivery_contract.is_session(execution):
            execution += timedelta(days=1)
        next_day = delivery_contract.adjacent_session(day)
        signals = {product: dict(product=product, market_date=day.isoformat(),
                                next_trade_date=next_day.isoformat(), close_confirmed=not intraday,
                                option_monthly_reset_preview=not intraday,
                                put_monthly_reset_preview=not intraday,
                                option_monthly_reset_execution_date=execution.isoformat(),
                                put_monthly_reset_execution_date=execution.isoformat())
                   for product in ("IC", "IM")}
        return maintenance_notice.reminders(signals, signal_day=day,
                                            is_session=delivery_contract.is_session)
    except (KeyError, TypeError, ValueError, AttributeError):
        return []


def partial_calendar_notices(payload: dict[str, Any]) -> list[str]:
    """Keep a partial grid warning while showing independently dated calendar facts."""
    if payload.get("status") != "partial":
        return failed_calendar_notices(payload)
    try:
        from ic_im_v1_4_delivery_contract import validate_session_dates
        mode = payload["publication_mode"]
        if mode not in {"realtime", "close_confirmed"}:
            return []
        days = {key: date.fromisoformat(payload[key]) for key in
                ("market_date", "completed_day", "verified_day", "next_trade_day")}
        if days["verified_day"] != days["completed_day"]:
            return []
        if mode == "close_confirmed" and days["market_date"] != days["completed_day"]:
            return []
        validate_session_dates(days, mode)
        signals = payload["signals"]
        if set(signals) != {"IC", "IM"}:
            return []
        for product, signal in signals.items():
            if (signal.get("product") != product or signal.get("market_date") != payload["market_date"]
                    or signal.get("next_trade_date") != payload["next_trade_day"]
                    or signal.get("close_confirmed") is not (mode == "close_confirmed")):
                return []
        return calendar_notices(payload)
    except (KeyError, TypeError, ValueError, AttributeError):
        return []


def build_failure_html(payload: dict[str, Any], run_url: str) -> str:
    notices = partial_calendar_notices(payload)
    notice_html = "".join('<p style="border:2px solid #f79009;padding:14px;">仅维护日历提醒；当日合约及数量尚未确认。<br>'
                          + escaped(n.replace("**", "")).replace("\n\n", "<br><br>") + '</p>' for n in notices)
    realtime = str(payload.get("publication_mode", "close_confirmed")) == "realtime"
    mode_text = "盘中实时" if realtime else "收盘确认"
    link = (
        f'<p style="margin:16px 0 0;"><a href="{escaped(run_url)}" style="color:#175cd3;text-decoration:none;font-weight:650;">查看GitHub运行记录 →</a></p>'
        if run_url
        else ""
    )
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:24px 10px;background:#f2f4f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Arial,sans-serif;color:#101828;">
<div style="max-width:640px;margin:0 auto;background:#ffffff;border:1px solid #fecdca;border-radius:16px;overflow:hidden;">
  <div style="padding:22px;background:#b54708;color:#ffffff;"><div style="font-size:12px;font-weight:700;">IC / IM 1.4-r1 · {escaped(mode_text)}</div><div style="margin-top:8px;font-size:24px;font-weight:760;">当日信号警告</div></div>
  <div style="padding:20px;color:#344054;font-size:14px;line-height:1.7;"><strong>当日新目标尚未确认，请勿依据旧邮件调整。</strong>{notice_html}<p>{escaped(fear_observation_text(payload))}</p><p>{escaped(payload.get('fear_warning', '恐慌读数仅供独立观察'))}</p><p>提示：{escaped(payload.get('error_type', 'RuntimeError'))}: {escaped(payload.get('error', '未知原因'))}</p><p>持久账本不会把未确认目标记为当日信号。</p>{link}</div>
</div></body></html>'''


def validate_success_payload(payload: dict[str, Any]) -> None:
    import math
    from check_ic_im_v1_4_delivery import expected_identity_for_day
    from prepare_ic_im_v1_4_marker import marker_name

    expected_build, expected_delivery = expected_identity_for_day(date.fromisoformat(str(payload.get("market_date", ""))))
    if str(payload.get("build", "")) != expected_build or str(payload.get("delivery_revision", "")) != expected_delivery:
        raise ValueError("digest build/revision does not match signal-day identity")

    marker_name(payload)
    if payload.get("status") == "partial":
        import copy
        payload = copy.deepcopy(payload)
        for signal in payload["signals"].values():
            if signal.get("grid_action") != "UNCONFIRMED":
                continue
            if (not delivery_contract.flexible_source(signal) or signal.get("fear_greed_index") is not None
                    or signal.get("grid_target") is not None or signal.get("total_units_target") is not None
                    or signal.get("signal_completeness") != "partial"
                    or signal.get("reference_grid_target") != signal.get("grid_current")):
                raise ValueError("partial Fear report must hide targets and preserve grid state")
            for field in ("grid_target", "grid_action", "grid_entry_source_target", "grid_transition_reason", "total_units_target", "total_units_change"):
                signal[field] = signal.get("reference_" + field)
    days = {}
    for field in ("market_date", "completed_day", "verified_day", "next_trade_day"):
        value = payload.get(field)
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError(f"successful digest requires ISO {field}")
        days[field] = date.fromisoformat(value)
    if days["verified_day"] != days["completed_day"]:
        raise ValueError("verified ledger day must equal completed day")
    if payload["publication_mode"] == "close_confirmed":
        if days["market_date"] != days["completed_day"]:
            raise ValueError("close-confirmed market and ledger dates must match")
    elif days["completed_day"] >= days["market_date"]:
        raise ValueError("realtime digest must retain prior completed ledger day")
    if days["next_trade_day"] <= days["market_date"]:
        raise ValueError("next trade day must follow market day")
    strict_identity = days["market_date"] >= STRICT_SIGNAL_IDENTITY_DATE
    if strict_identity:
        from ic_im_v1_4_delivery_contract import validate_session_dates
        validate_session_dates(days, payload["publication_mode"])
    expected_rule = expected_rule_revision_for_day(days["market_date"])
    if strict_identity and payload.get("signal_build") != expected_build:
        raise ValueError("signal build must match the authoritative signal-day identity")
    if strict_identity and payload.get("signal_rule_revision") != expected_rule:
        raise ValueError("signal rule must match the authoritative signal-day identity")
    signals = payload.get("signals")
    if not isinstance(signals, dict) or set(signals) != {"IC", "IM"}:
        raise ValueError("result must contain IC and IM signals")
    from ic_im_v1_4_delivery_contract import validate_report_clock
    validate_report_clock(payload, days["market_date"])
    if days["market_date"] >= date(2026, 10, 8):
        if payload.get("grid_policy_revision") != "ic_im_or_fear25_paired_exit50_20261008_v1":
            raise ValueError("fix10 grid policy identity mismatch")
        shared = ("fear_greed_index", "fear_data_date", "fear_data_status", "fear_csv_sha256",
                  "fear_intraday_policy_revision", "fear_close_policy_revision", "fear_fallback_applied",
                  "fear_previous_exchange_date", "fear_fallback_reason", "fear_fallback_timing",
                  "fear_source_policy_revision", "fear_available_sources", "fear_selected_source",
                  "fear_cross_validation", "fear_page_updated_at", "fear_page_data_date", "fear_page_value",
                  "fear_csv_latest_date", "fear_csv_latest_value", "fear_csv_observations")
        if any(signals["IC"].get(field) != signals["IM"].get(field) for field in shared):
            raise ValueError("IC/IM Fear source snapshots disagree")
    for product, signal in signals.items():
        if not isinstance(signal, dict):
            raise ValueError(f"invalid {product} signal")
        for field, expected in (
            ("product", product),
            ("strategy_version", "1.4"),
            ("strategy_revision", payload["strategy_revision"]),
            ("market_date", days["market_date"].isoformat()),
            ("next_trade_date", days["next_trade_day"].isoformat()),
        ):
            if (strict_identity or field in signal) and signal.get(field) != expected:
                raise ValueError(f"{product} {field} identity does not match the result and signal day")
        # Old delivery tags used a simplified LEGACY mapping. Preserve their
        # recorded producer identity; when available, compare it with the
        # original payload identity instead of relabeling it by today's map.
        for field, payload_field, expected in (
            ("v14_build_id", "signal_build", expected_build),
            ("v14_rule_revision", "signal_rule_revision", expected_rule),
        ):
            if strict_identity:
                if signal.get(field) != expected:
                    raise ValueError(f"{product} {field} identity does not match the authoritative signal day")
            elif field in signal and payload_field in payload and signal[field] != payload[payload_field]:
                raise ValueError(f"{product} {field} identity differs from its archived payload")
        realtime = payload["publication_mode"] == "realtime"
        if (strict_identity or "close_confirmed" in signal) and signal.get("close_confirmed") is not (not realtime):
            raise ValueError(f"{product} close_confirmed must be a mode-consistent boolean")
        if (strict_identity or "market_phase" in signal) and signal.get("market_phase") != ("盘中" if realtime else "收盘后"):
            raise ValueError(f"{product} market phase does not match publication mode")
        if realtime and (strict_identity or "state_anchor_day" in signal) and signal.get("state_anchor_day") != days["completed_day"].isoformat():
            raise ValueError(f"{product} realtime signal must use the completed ledger day")
        for field in ("total_units_current", "total_units_target", "momentum_current_weight", "momentum_next_weight", "grid_current", "grid_target"):
            value = signal.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{product} requires finite numeric {field}")
        if product == "IC" and days["market_date"] >= date(2026, 10, 8):
            active_abs = signal.get("momentum_abs")
            if (signal.get("momentum_abs_days") != 40 or
                    signal.get("momentum_abs_debounce_active") is not False or
                    isinstance(active_abs, bool) or not isinstance(active_abs, (int, float)) or
                    not math.isfinite(active_abs) or
                    signal.get("momentum_abs_on") is not (active_abs > 0.0)):
                raise ValueError("fix11 IC Abs40 static signal fields missing or inconsistent")
        if days["market_date"] >= date(2026, 10, 8):
            if signal.get("grid_policy_revision") != payload.get("grid_policy_revision"):
                raise ValueError(f"{product} fix10 grid policy mismatch")
            for field, units in (("grid_entry_source_current", signal["grid_current"]),
                                 ("grid_entry_source_target", signal["grid_target"])):
                source = signal.get(field)
                if units not in (0.0, 0.5) or source not in {"none", "valuation_only", "fear_only", "both"} or (units == 0.0) != (source == "none"):
                    raise ValueError(f"{product} fix10 grid source/units mismatch")
            fear = signal.get("fear_greed_index")
            from ic_im_v1_4_delivery_contract import validate_fear_clock
            validate_fear_clock(signal, days["market_date"], payload["publication_mode"])
            lagged_fear = signal.get("fear_data_status") in {"intraday_previous_session", "close_previous_session"}
            if lagged_fear:
                from ic_im_v1_4_delivery_contract import validate_fear_fallback
                validate_fear_fallback(signal, days["market_date"], payload["publication_mode"])
            if fear is not None and (isinstance(fear, bool) or not isinstance(fear, (int, float)) or
                                     not math.isfinite(fear) or not 0 <= fear <= 100 or
                                     (not lagged_fear and signal.get("fear_data_date") != days["market_date"].isoformat())):
                raise ValueError(f"{product} fix10 Fear score/date invalid")
            if payload["publication_mode"] == "close_confirmed" and signal.get("fear_data_status") not in {
                "same_day_post_close", "retrospective_replay", "missing_published_history", "close_previous_session",
                *({"same_day_unverified_close", "unavailable"} if delivery_contract.flexible_source(signal) else set())
            }:
                raise ValueError(f"{product} fix10 Fear score not close-confirmed")
            if payload["publication_mode"] == "realtime" and signal.get("fear_data_status") not in {
                "intraday_provisional", "intraday_unpublished", "intraday_previous_session",
                *({"unavailable"} if delivery_contract.flexible_source(signal) else set())
            }:
                raise ValueError(f"{product} fix10 Fear status is not a valid realtime observation")
            if signal.get("fear_fallback_timing") == "retrospective_replay" and payload.get("signal_timing") != "retrospective_replay":
                raise ValueError("digest must label retrospective Fear fallback as historical model replay")
        if days["market_date"] >= date(2026, 9, 26):
            from ic_im_v1_4_delivery_contract import validate_pending_plans, validate_quantities
            validate_quantities(product, signal)
            validate_pending_plans(product, signal)
            put_execution_contract.validate_execution(product, signal, is_session=delivery_contract.is_session,
                                                       adjacent_session=delivery_contract.adjacent_session)
            if str(signal.get("market_date") or "")[:10] != str(days["market_date"]):
                raise ValueError(f"{product} signal day must match digest day")
            if signal.get("v14_ordinary_put_plan_status") == "scheduled_t_plus_1_open":
                validate_ordinary_put_plan(product, signal, days["next_trade_day"].isoformat())
            if product == "IM":
                qty = signal.get("call_target_qty_normalized")
                if isinstance(qty, bool) or not isinstance(qty, (int, float)) or not math.isfinite(qty) or abs(qty) > 1e-12:
                    raise ValueError("fix6 IM Call target quantity must be zero")
                if any(signal.get(field) is not None for field in ("call_target_contract", "call_target_expiry", "call_target_strike")) or str(signal.get("call_action")) not in {"HOLD", "CLOSE_CALL"}:
                    raise ValueError("fix6 IM Call must not open or rescue")
                has_old_call = bool(signal.get("call_has_position")) and bool(signal.get("call_current_contract"))
                if has_old_call and signal.get("call_action") != "CLOSE_CALL":
                    raise ValueError("fix6 IM old Call must have close-only target")


def build_success(payload: dict[str, Any], run_url: str, subject_prefix: str) -> tuple[str, str, bool]:
    validate_success_payload(payload)
    signals = {p: {**s, **payload.get("jiu_reference", {})} for p, s in payload.get("signals", {}).items()}
    if set(signals) != {"IC", "IM"}:
        raise ValueError("result must contain IC and IM signals")
    actions = {product: action_parts(signals[product], product) for product in ("IC", "IM")}
    actionable = any(actions.values())
    notices = calendar_notices(payload)
    opening_notices = execution_notices(payload)
    opening_attention = any(put_execution_contract.needs_attention(signal) for signal in signals.values())
    mode = str(payload.get("publication_mode", "close_confirmed"))
    realtime = mode == "realtime"
    tag = (
        ("预估需调整" if actionable else "预估无需调整")
        if realtime
        else ("需调整" if actionable else "无需调整")
    )
    mode_tag = "盘中实时" if realtime else "收盘确认"
    replay = payload.get("signal_timing") == "retrospective_replay"
    if replay:
        mode_tag, tag = "历史模型补录", "参考差异" if actionable else "模型持仓维持"
    if notices:
        tag = "月度维护日历复核" if replay else "月度Put需维护" + ("/需调整" if actionable else "")
    elif opening_attention:
        tag = "Put开盘待核验" + ("/需调整" if actionable else "")
    elif opening_notices:
        tag += "/Put开盘已核验"
    if payload.get("status") == "partial":
        tag = "部分数据缺失/网格未确认"
    prefix = f"[{subject_prefix.strip().strip('[]')}]" if subject_prefix.strip() else ""
    day = str(
        payload.get("market_date" if realtime else "completed_day", "未知日期")
    )
    subject = f"{prefix}[{mode_tag}][{tag}] IC/IM 1.4-r1 日报 - {day}"
    lines = [
        "## 今日结论",
        "",
        f"报告完成：{payload.get('report_generated_at', payload.get('generated_at', 'N/A'))}",
        "",
        (
            "**历史模型补录 retrospective_replay；以下差异仅供参考，不是原信号日已发布的前瞻调整目标。**"
            if replay else
            "**百分位没有合格读数；网格及合计目标未确认，其他模块照常展示。**"
            if payload.get("status") == "partial" else
            "**月度Put需维护，请按下列日期在盘尾自行判断合约，并于维护日收盘完成换仓。常规仓位目标另见下表。**"
            if notices else
            "**Put开盘计划或入场成本仍待核验，请先核对下列独立开盘事件；未核验不视为成交。**"
            if opening_attention else
            "**盘中预估存在下一交易日调整，请等待收盘确认。**"
            if actionable and realtime
            else "**盘中暂未出现下一交易日调整，收盘前仍可能变化。**"
            if realtime
            else "**存在下一交易日调整，请查看逐腿变化。**"
            if actionable
            else "**IC、IM均无需调整。**"
        ),
        "",
        f"信号日：**{day}**｜下一交易日：**{payload.get('next_trade_day', 'N/A')}**",
        "",
        *[notice + "\n" for notice in notices],
        *[notice + "\n" for notice in opening_notices],
        "**估值数据来源：" + valuation_banner(signals) + "**",
        "",
        "**" + fear_observation_text(payload) + "**",
        "",
        "**IC：" + iv_warning_text(signals["IC"]) + "**",
        "",
        "**IM：" + iv_warning_text(signals["IM"]) + "**",
        "",
        "| 品种 | 期货总仓 | 动量袖 | 网格 | Put | Call | 预估变化 |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    for product in ("IC", "IM"):
        signal = signals[product]
        current_total = number(signal.get("total_units_current"))
        target_total = number(signal.get("total_units_target"))
        current_mom = number(signal.get("momentum_current_weight"))
        target_mom = number(signal.get("momentum_next_weight"))
        current_grid = number(signal.get("grid_current"))
        target_grid = number(signal.get("grid_target"))
        put = f"{put_text(product, signal, False)} → {put_text(product, signal, True)}"
        call = "明确禁止" if product == "IC" else f"{call_text(signal, False)} → {call_text(signal, True)}"
        action = "；".join(actions[product]) or "无调整"
        lines.append(
            f"| {product} | {current_total} → {target_total} | {current_mom} → {target_mom} | "
            f"{current_grid} → {target_grid} | {put} | {call} | {action} |"
        )
    lines += ["", "## 为什么是这个结果", ""]
    for product in ("IC", "IM"):
        lines.append(f"### {product}")
        lines.append("")
        lines.extend(f"- {reason}" for reason in product_reasons(product, signals[product]))
        lines.append("")
        put_details = put_display.markdown(product, signals[product])
        if put_details:
            lines.append(put_details)
    lines += [
        "## 审计状态",
        "",
        f"- 构建：`{payload.get('build', 'N/A')}`",
        str(payload.get('grid_release_note', '')),
        f"- 已核验账本日：`{payload.get('verified_day', 'N/A')}`",
        f"- 账本序号：`{payload.get('sequence', 'N/A')}`",
        f"- 账本摘要：`{str(payload.get('digest', ''))[:12]}`",
        f"- 本次补写交易日数：`{payload.get('advanced_sessions', 'N/A')}`",
        "",
        (
            "本邮件是盘中研究预估，使用实时/延时行情与上一已核验收盘账本；"
            "盘中值并未写入账本，必须等待收盘确认。它不是账户持仓，不会自动下单。完整逐腿解释见附件。"
            if realtime
            else "本邮件是研究审计信号，不是账户持仓，不会自动下单；实际成交、行权、结算和交割由用户自行处理。完整逐腿解释见附件。"
        ),
    ]
    if run_url:
        lines.append(f"[查看GitHub运行记录]({run_url})")
    return subject, "\n".join(lines), actionable


def build_failure(payload: dict[str, Any], run_url: str, subject_prefix: str) -> tuple[str, str]:
    prefix = f"[{subject_prefix.strip().strip('[]')}]" if subject_prefix.strip() else ""
    day = str(payload.get("market_date") or payload.get("generated_at", ""))[:10] or "未知日期"
    realtime = str(payload.get("publication_mode", "close_confirmed")) == "realtime"
    notices = partial_calendar_notices(payload)
    calendar_tag = "[月度Put维护日历]" if notices else ""
    subject = f"{prefix}[警告][{'盘中实时' if realtime else '收盘确认'}]{calendar_tag} IC/IM 1.4-r1 日报 - {day}"
    body = "\n".join(
        [
            "## 今日结论",
            "",
            (
                "**IC/IM盘中实时信号尚未确认，请勿依据旧邮件调整。**"
                if realtime
                else "**IC/IM收盘信号尚未确认，请勿依据旧邮件调整。**"
            ),
            "",
            *(["仅维护日历提醒；当日合约及数量尚未确认。", "", *[n + "\n" for n in notices]] if notices else []),
            f"- {fear_observation_text(payload)}",
            f"- 恐慌数据提示：{payload.get('fear_warning', '读数仅供独立观察')}",
            f"- 构建：`{payload.get('build', 'N/A')}`",
            f"- 未确认原因：{payload.get('error_type', 'RuntimeError')}: {payload.get('error', '未知原因')}",
            "- 持久账本不会把未确认目标记为当日信号。",
            f"- [查看GitHub运行记录]({run_url})" if run_url else "",
        ]
    ).rstrip()
    return subject, body


def required_success_report(result_path: Path, report_arg: str, payload: dict[str, Any]) -> str:
    report = report_arg or str(result_path.resolve().parent / str(payload.get("report_file", "")))
    if not report_arg and not payload.get("report_file"):
        raise FileNotFoundError("successful IC/IM digest requires report_file")
    if not Path(report).is_file():
        raise FileNotFoundError(f"successful IC/IM digest report is missing: {report}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True)
    parser.add_argument("--report", default="")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--subject-prefix", default="")
    args = parser.parse_args()

    payload = json.loads(Path(args.result).read_text(encoding="utf-8"))
    if str(payload.get("strategy_revision")) != "r1":
        raise ValueError("digest requires strategy_revision=r1")
    from datetime import date
    from check_ic_im_v1_4_delivery import expected_identity_for_day
    expected_build, expected_delivery = expected_identity_for_day(date.fromisoformat(str(payload.get("market_date", ""))))
    if str(payload.get("build", "")) != expected_build or str(payload.get("delivery_revision", "")) != expected_delivery:
        raise ValueError("digest build/revision does not match signal-day identity")
    run_url = os.environ.get("GITHUB_RUN_URL", "")
    if payload.get("status") in {"ok", "partial"} and payload.get("signals"):
        subject, body, _ = build_success(payload, run_url, args.subject_prefix)
        html_body = build_success_html(payload, run_url)
        attachment = required_success_report(Path(args.result), args.report, payload)
    else:
        subject, body = build_failure(payload, run_url, args.subject_prefix)
        html_body = build_failure_html(payload, run_url)
        attachment = None
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ic_im_v1_4_digest.md").write_text(body, encoding="utf-8")
    (out_dir / "ic_im_v1_4_digest.html").write_text(html_body, encoding="utf-8")
    (out_dir / "metadata.json").write_text(
        json.dumps(
            {
                "subject": subject,
                "body": body,
                "html_body": html_body,
                "attachment": attachment,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())




