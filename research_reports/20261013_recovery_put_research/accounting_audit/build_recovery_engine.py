"""Create an isolated native-account extension using exact counted patch anchors."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "quant_param_scan_runs/20260926_icim_fix8_momentum_transfer_robustness/native_account.py"
OUTPUT = Path(__file__).resolve().parents[1] / "recovery_account.py"
source = BASE.read_text(encoding="utf-8")
patches = []


def once(old, new, name):
    global source
    if source.count(old) != 1:
        raise RuntimeError((name, source.count(old)))
    source = source.replace(old, new, 1)
    patches.append(name)


once("from trade_account import Account  # noqa: E402\n",
     "from trade_account import Account  # noqa: E402\n"
     "from native_fix4_seller_lifecycle_v1 import candidate_from_row\n"
     "from recovery_cycle_controller import RecoveryCycleController\n", "imports")
once("FUTURES_BASIS_RETENTION = 1.0\n", "FUTURES_BASIS_RETENTION = 1.0\n"
     "RECOVERY_RESEARCH_MODE = 'A0'\nRECOVERY_IC_MIN_DELTA = .125\nRECOVERY_IM_MIN_QTY = 1.5\n", "flags")
once("    market = Market(source, product)\n", "    market = Market(source, product)\n"
     "    controller = None\n"
     "    if RECOVERY_RESEARCH_MODE != 'A0':\n"
     "        if EXECUTION_MODEL != 'mixed' or VERSION != 'v7' or CORE_UNITS != .5 or FIXED_SELLER_CORE_UNITS is not None:\n"
     "            raise RuntimeError('recovery research requires the unchanged v7 mixed fixed-half-core configuration')\n"
     "        controller = RecoveryCycleController(\n"
     "            product=product, arm=arm, mode=RECOVERY_RESEARCH_MODE, market=market, formal=formal,\n"
     "            selector=REENTRY_SELECTOR, roll_selector=roll_candidate, candidate_decoder=candidate_from_row,\n"
     "            gov=gov, spot=spot, source=source, option_expiry=option_expiry, option_fee=OPTION_FEE,\n"
     "            ic_min_delta=RECOVERY_IC_MIN_DELTA, im_min_qty=RECOVERY_IM_MIN_QTY)\n", "controller")
once("            l = life.iloc[previous]\n", "            l = (controller.consume(day, account) if controller is not None\n"
     "                 else life.iloc[previous])\n", "dynamic_route")
once("            names = sorted(set(account.positions) | set(target_positions))\n",
     "            if controller is not None:\n"
     "                controller.add_protection_target(target_positions, stable_quantity, scale, PositionTarget)\n"
     "            names = sorted(set(account.positions) | set(target_positions))\n", "protection_target")
once("                    if name.startswith(\"future|\") or name.startswith(\"option|seller|\"):\n",
     "                    if name.startswith(\"option|recovery_put|\"):\n"
     "                        open_names.add(name)\n"
     "                    elif name.startswith(\"future|\") or name.startswith(\"option|seller|\"):\n", "protection_open")
once("                    if strict_ordinary_open and not strict_profit_open:\n",
     "                    if name.startswith(\"option|recovery_put|\"):\n"
     "                        price, price_basis = market.buyer_open_quote(day, contract, signal_day)\n"
     "                    elif strict_ordinary_open and not strict_profit_open:\n", "protection_price")
once("                for name, lot in list(account.positions.items()):\n                    contract = current_contract(name)\n",
     "                if controller is not None:\n"
     "                    controller.after_open_trades(day, account)\n"
     "                for name, lot in list(account.positions.items()):\n                    contract = current_contract(name)\n", "cycle_close_after_open")
once("        if ordinal < len(risk) - 1:\n            core = next((n for n in account.positions if n.startswith(\"option|core_put|\")), None)\n",
     "        if controller is not None:\n"
     "            next_day = date.fromisoformat(str(risk.execution_date.iloc[ordinal])) if ordinal < len(risk) - 1 else None\n"
     "            controller.close(day, next_day, account, risk.iloc[ordinal], targets.iloc[ordinal],\n"
     "                             candidates.iloc[ordinal] if ordinal < len(candidates) else None)\n"
     "            history[-1].update(controller.snapshot(account))\n"
     "        if ordinal < len(risk) - 1:\n            core = next((n for n in account.positions if n.startswith(\"option|core_put|\")), None)\n", "end_close_decision")
once("    return daily, events, audit\n", "    if controller is not None:\n        audit.update(controller.audit())\n"
     "    else:\n        audit['recovery_research_mode'] = 'A0'\n"
     "    return daily, events, audit\n", "audit")
source = source.replace('"""Isolated native v7 account with one same-close monthly maintenance event.',
                        '"""Research-only recovery-Put extension; A0 retains native v7 baseline behavior.', 1)
OUTPUT.write_text(source, encoding="utf-8")
print({"output": str(OUTPUT), "exact_patch_anchors": patches})
