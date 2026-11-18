"""Check candidate monthly plans against frozen target rows, prices and the resulting account."""

from collections import Counter
from hashlib import sha256
from io import BytesIO
import json
from math import isclose, isfinite
from pathlib import Path
import re
from zipfile import ZipFile

import pandas as pd


HERE = Path(__file__).resolve().parent
OUT = HERE.parent
BASE = OUT.parent / "fix11_nav_1y_20261118_close"
RAW = BASE / "fresh_sources/extended_inputs"
ANCHOR, FIRST, END = "2025-11-17", "2025-11-18", "2026-11-18"
LABEL = "T_plus_1_monthly_maintenance_dated_open"
EPS = 2e-6


def must(condition, message):
    if not condition:
        raise AssertionError(message)


def close(got, want, message, tol=EPS):
    must(isclose(float(got), float(want), rel_tol=0, abs_tol=tol),
         f"{message}: got {got}, expected {want}")
    return abs(float(got) - float(want))


class OptionSources:
    def __init__(self):
        self.master = pd.read_csv(RAW / "sse_510500_standard_put_snapshots.csv.gz", dtype={"security_id": str})
        self.master = self.master.set_index(["date", "contract_id"], verify_integrity=True)
        self.sina = pd.read_csv(RAW / "sina_510500_put_histories.csv.gz", dtype={"security_id": str}, low_memory=False)
        self.sina = self.sina.set_index(["date", "security_id"], verify_integrity=True)
        self.official = {}
        self.hash_paths = {
            RAW / "sse_510500_standard_put_snapshots.csv.gz", RAW / "sina_510500_put_histories.csv.gz",
        }

    def opening(self, product, signal, day, contract):
        sid = None
        if product == "IC":
            sid = self.master.at[(signal, contract), "security_id"]
            must(re.fullmatch(r"\d{8}", sid) and self.master.at[(day, contract), "security_id"] == sid,
                 f"{day} {contract} IC contract identity")
            row = self.sina.loc[(day, sid)]
            price, volume = row["open"], row.volume
            maturity = re.fullmatch(r"510500P(\d{4})M\d{5}", contract)[1]
        else:
            if day not in self.official:
                month = day[:7].replace("-", "")
                member = day.replace("-", "") + "_1.csv"
                file = RAW / f"cffex_raw/{month}.zip"
                self.hash_paths.add(file)
                with ZipFile(file) as archive:
                    frame = pd.read_csv(BytesIO(archive.read(member)), encoding="gb18030")
                frame["合约代码"] = frame["合约代码"].str.strip()
                rows = frame.loc[frame["合约代码"].str.fullmatch(r"MO\d{4}-P-\d+", na=False)]
                self.official[day] = rows.set_index("合约代码", verify_integrity=True)
            row = self.official[day].loc[contract]
            price, volume = row["今开盘"], row["成交量"]
            maturity = re.fullmatch(r"MO(\d{4})-P-\d+", contract)[1]
        must(isfinite(price) and price > 0 and isfinite(volume) and volume > 0,
             f"{day} {contract} not a real traded open")
        must("20" + maturity > day[:7].replace("-", ""),
             f"{day} {contract} expiry-month collision needs separate dated expiry check")
        return dict(price=float(price), volume=float(volume), security_id=sid)


def positions_asof(events, day):
    rows = events.loc[events.date.le(day) & events.symbol.notna()]
    qty = rows.groupby("symbol").quantity_change.sum()
    assignments = rows.loc[rows.event.eq("assign_short_put")].groupby("underlying").shares_received.sum()
    return {k: float(v) for k, v in qty.add(assignments, fill_value=0).items() if abs(v) > 1e-7}


def recalc_metrics(daily):
    eq = daily.loc[ANCHOR:END, "equity_close"]
    nav = eq / eq.iloc[0]
    dd = nav / nav.cummax() - 1
    close(nav.iloc[-1] - 1, (1 + daily.loc[FIRST:END, "return_net"]).prod() - 1,
          "equity vs compounded net return", 1e-12)
    return dict(ret=float(nav.iloc[-1] - 1), max_dd=float(dd.min()), current_dd=float(dd.iloc[-1]),
                dates=int(len(nav) - 1))


def check_cash_path(daily, events):
    max_err = 0.0
    equity = float(daily.at[ANCHOR, "equity_close"])
    for day, row in daily.loc[FIRST:END].iterrows():
        delta = float(events.loc[events.date.eq(day), "equity_change"].sum())
        eq = equity + delta
        max_err = max(max_err, close(eq, row.equity_close, f"{day} equity event identity"))
        close(eq / equity - 1, row.return_net, f"{day} return_net identity", 2e-13)
        equity = eq
    return max_err


def schedule(events):
    out = events.loc[events.date.between(FIRST, END) & events.event.eq("trade")].copy()
    out = out.loc[~out.symbol.str.startswith(("option|core_put|", "option|core_extra_put|", "option|momentum_put|"))]
    values = ["date", "symbol", "signal_date", "phase", "price_basis"]
    return Counter(tuple(x) for x in out[values].itertuples(index=False, name=None))


def count_marker(events, day, prefix):
    return [mark for mark in events.loc[events.date.eq(day) & events.event.eq("trade") & events.symbol.str.startswith(prefix, na=False),
                                       ["symbol", "phase", "price_basis"]].itertuples(index=False, name=None)]


def check_book(product, daily, events, baseline, prior_events, plans, sources):
    version = {"IC": "v2", "IM": "v3"}[product]
    target = pd.read_csv(
        BASE / f"terminal_inputs/native_fix4_{product.lower()}_noseller_targets_{version}.csv.gz"
    ).set_index("signal_date", verify_integrity=True)
    future_file = RAW / f"cffex_{product.lower()}_contracts.csv"
    futures = pd.read_csv(future_file).set_index(["date", "contract"], verify_integrity=True)
    spot_file = RAW / ("sina_510500_etf.csv" if product == "IC" else "im_ohlcv_frozen_plus_fresh.csv.gz")
    spot = pd.read_csv(spot_file).set_index("date", verify_integrity=True)
    sources.hash_paths.update({future_file, spot_file})
    plans_checked = 0
    sides_checked = 0
    max_qty = 0.0
    equal_size_at_anchor = positions_asof(events, ANCHOR) == positions_asof(prior_events, ANCHOR)
    must(equal_size_at_anchor, f"{product} anchor position mismatch")
    must(schedule(events) == schedule(prior_events), f"{product} non-buyer trade schedule differs")
    close(daily.at[ANCHOR, "equity_close"], baseline.at[ANCHOR, "equity_close"], f"{product} anchor equity")
    delayed = events.loc[events.event.eq("trade") & events.price_basis.eq(LABEL)]
    must(not events.loc[events.date.between(FIRST, END)].price_basis.eq("T_monthly_maintenance_close").any(),
         f"{product} candidate kept same-close monthly trades")
    plan_keys = set()
    raw_price_checks = []
    for plan in plans:
        signal, day = plan["signal_date"], plan["execution_date"]
        must((signal, day) not in plan_keys, f"{product} duplicate monthly plan {day}")
        plan_keys.add((signal, day))
        must(FIRST <= signal < day <= END and daily.loc[signal:].index[1] == day, "monthly plan wrong trading day")
        must(bool(target.at[signal, "monthly_reset_due"]), f"{signal} not a frozen monthly target")
        must(product != "IC" or target.at[signal, "monthly_reset_reference_day"] == signal,
             f"{signal} IC date was preview only")
        future_close = float(futures.at[(signal, target.at[signal, "quarter_core_eod"]), "close"])
        close(plan["future_close"], future_close, f"{signal} monthly true future close")
        t_events = events.loc[events.date.eq(signal)]
        final_items = t_events.loc[t_events.event.eq("cash_interest")
                                   | (t_events.event.eq("trade") & t_events.quarter_roll.eq(True))]
        t_equity = float(daily.at[signal, "equity_close"] - final_items.equity_change.sum())
        close(plan["sizing_equity"], t_equity, f"{signal} monthly pre-interest equity")
        before, after = positions_asof(events, signal), positions_asof(events, day)
        legs = delayed.loc[delayed.date.eq(day)]
        for sleeve in plan["sleeves"]:
            lane = sleeve["sleeve"]
            must(lane in {"core_put", "momentum_put"}, f"unexpected sleeve {lane}")
            prefix = f"option|{lane}|"
            qty = float(sleeve["normalized_quantity"])
            close(qty, target.at[signal, f"{lane}_qty_target"], f"{signal} monthly frozen normalized qty")
            if qty:
                contract_field = "core_put_contract_target" if product == "IC" else f"{lane}_contract_target"
                must(sleeve["contract"] == target.at[signal, contract_field], f"{signal} monthly contract changed")
            expected_qty = qty * t_equity / (200 * future_close)
            max_qty = max(max_qty, close(sleeve["frozen_quantity"], expected_qty, f"{day} T-known absolute qty", 2e-9))
            q = expected_qty if sleeve["route_permits_target"] else 0.0
            close(sleeve["executed_target_quantity"], q, f"{day} executed after route gate", 2e-9)
            if not sleeve["route_permits_target"]:
                must(lane == "core_put" and daily.at[day, "seller_route"] != "future",
                     f"{day} liquidates protected route")
            close(len(sleeve["held_before"]), sum(k.startswith(prefix) for k in before), f"{day} opening held set")
            for k, prior_qty in sleeve["held_before"].items():
                close(prior_qty, before[k], f"{day} opening carried qty", 2e-9)
            desired = prefix + sleeve["contract"] if q > 0 else None
            after_sleeve = {k: v for k, v in after.items() if k.startswith(prefix)}
            must(set(after_sleeve) == ({desired} if desired else set()), f"{day} monthly final contract {lane}")
            if desired:
                close(after_sleeve[desired], q, f"{day} monthly final quantity {lane}", 2e-9)
            wanted_deltas = {}
            for k in set(sleeve["held_before"]) | ({desired} if desired else set()):
                change = (q if k == desired else 0) - before.get(k, 0)
                if abs(change) >= 1e-7:
                    wanted_deltas[k] = change
            cl = legs.loc[legs.symbol.str.startswith(prefix)]
            must(set(cl.symbol) == set(wanted_deltas) and len(cl) == len(wanted_deltas),
                 f"{day} monthly lot changes {lane}")
            old_closures = cl.loc[cl.symbol.ne(desired)]
            new_execution = cl.loc[cl.symbol.eq(desired)]
            if len(old_closures) and len(new_execution):
                must(old_closures.index.max() < new_execution.index.min(), f"{day} monthly new before old closure")
            for r in cl.itertuples(index=False):
                must(r.phase == "open" and r.signal_date == signal, f"{day} monthly not causal opening")
                close(r.quantity_change, wanted_deltas[r.symbol], f"{day} monthly lot delta", 2e-9)
                src = sources.opening(product, signal, day, r.symbol.split("|")[-1])
                close(r.price, src["price"], f"{day} published open")
                close(sleeve["opening_prices"][r.symbol], src["price"], f"{day} plan evidence open")
                multiplier = 10000 if product == "IC" else 100
                fee = abs(r.quantity_change) * multiplier * float(spot.at[day, "open"]) * .0005
                close(r.fee, fee, f"{day} option opening fee")
                close(r.equity_change, -fee, f"{day} equity debit")
                close(r.cash_change, -r.quantity_change * multiplier * src["price"] - fee, f"{day} cash settlement")
                raw_price_checks.append(dict(day=day, symbol=r.symbol, fee=fee, **src))
                sides_checked += 1
        plans_checked += 1
    must(sides_checked == len(delayed), f"{product}: delayed trades without verified plan")
    if product == "IM":
        for day in ("2026-03-23", "2026-04-20"):
            old_recovery = count_marker(prior_events, day, "option|core_put|")
            new_recovery = count_marker(events, day, "option|core_put|")
            must(old_recovery and new_recovery == old_recovery, f"{day} monthly zero swallowed original recovery")
    return dict(plans=plans_checked, delayed_trade_sides=sides_checked, source_rows=raw_price_checks,
                maximum_frozen_quantity_discrepancy=max_qty, maximum_cash_equity_event_discrepancy=check_cash_path(daily, events),
                baseline_metrics=recalc_metrics(baseline), candidate_metrics=recalc_metrics(daily),
                non_buyer_trade_sides=sum(schedule(events).values()),
                monthly_expiry_recovery_normal_open_preserved=(product == "IM"))


def main():
    receipt_file = OUT / "full_account_verification.json"
    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    must(receipt["status"] == "PASS" and receipt["latest_completed_close"] == END, "candidate receipt invalid")
    src = OptionSources()
    evidence = {"status": "PASS", "products": {}}
    source_paths = {receipt_file}
    frames = {}
    for label in ("baseline", "t1_open"):
        for key in ("daily", "events"):
            file = OUT / f"{label}_{key}.csv.gz"
            source_paths.add(file)
            frames[label, key] = pd.read_csv(file, float_precision="round_trip")
    for product in ("IC", "IM"):
        per_product = {k: x.loc[x["product"].eq(product)] for k, x in frames.items()}
        result = check_book(product, per_product["t1_open", "daily"].set_index("date"),
                            per_product["t1_open", "events"], per_product["baseline", "daily"].set_index("date"),
                            per_product["baseline", "events"], receipt["monthly_open_plans"][product], src)
        evidence["products"][product] = result
        print(product, {k: v for k, v in result.items() if k != "source_rows"})
    evidence["input_hashes"] = {str(file): sha256(file.read_bytes()).hexdigest()
                                for file in sorted(source_paths | src.hash_paths)}
    (HERE / "candidate_independent_verification.json").write_text(
        json.dumps(evidence, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
