"""Independent T+1 opening price availability for original monthly-maintenance trade sides."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

import pandas as pd


HERE = Path(__file__).resolve().parent
BUNDLE = HERE.parents[1] / "fix11_nav_1y_20261118_close"
RAW = BUNDLE / "fresh_sources/extended_inputs"
FIRST = "2025-11-18"


def sha(p: Path):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    events_file = BUNDLE / "full_account_events.csv.gz"
    daily_file = BUNDLE / "full_account_daily.csv.gz"
    master_file = RAW / "sse_510500_standard_put_snapshots.csv.gz"
    history_file = RAW / "sina_510500_put_histories.csv.gz"
    e = pd.read_csv(events_file)
    d = pd.read_csv(daily_file)
    master = pd.read_csv(master_file, dtype={"security_id": str}).set_index(
        ["date", "contract_id"], verify_integrity=True
    )
    history = pd.read_csv(history_file, dtype={"security_id": str}, low_memory=False).set_index(
        ["date", "security_id"], verify_integrity=True
    )
    # The recent MO extension CSV is not the historical universe.
    # Read the CFFEX original dated daily file directly from each stored monthly archive.
    cffex_days = {}
    matches = []
    frozen = {events_file, daily_file, master_file, history_file}
    for old in e.loc[e.event.eq("trade") & e.price_basis.eq("T_monthly_maintenance_close")].itertuples(index=False):
        dates = d.loc[d["product"].eq(old.product), "date"]
        after = dates[dates.gt(old.date)]
        day = after.iat[0] if len(after) else None
        contract = old.symbol.split("|", 2)[2]
        row = dict(product=old.product, signal_date=old.date, execution_date=day,
                   old_symbol=old.symbol, original_signed_quantity=old.quantity_change,
                   original_price=old.price, positive_actual_open=False,
                   in_window=old.date >= FIRST)
        if day is None:
            row["reason"] = "No next trading session in frozen replay"
        elif old.product == "IC":
            row["source"] = "SSE dated contract/security master × Sina dated unadjusted option OHLC"
            match = re.fullmatch(r"510500P(\d{4})M\d{5}", contract)
            if match is None:
                raise AssertionError("Unexpected IC contract identity")
            try:
                sid = master.at[(old.date, contract), "security_id"]
                current_sid = master.at[(day, contract), "security_id"]
                row.update(security_id=sid, same_security_id=sid == current_sid,
                           open=float(history.at[(day, sid), "open"]),
                           volume=float(history.at[(day, sid), "volume"]))
                row["positive_actual_open"] = sid == current_sid and row["open"] > 0 and row["volume"] > 0
            except KeyError:
                row["reason"] = "Missing T or T+1 contract/security or T+1 option OHLC"
        else:
            archive = RAW / f"cffex_raw/{day[:7].replace('-', '')}.zip"
            member = f"{day.replace('-', '')}_1.csv"
            row.update(source=str(archive.relative_to(BUNDLE)), member=member)
            frozen.add(archive)
            match = re.fullmatch(r"MO(\d{4})-P-\d+", contract)
            if match is None:
                raise AssertionError("Unexpected IM contract identity")
            if day not in cffex_days:
                with zipfile.ZipFile(archive) as z:
                    book = pd.read_csv(io.BytesIO(z.read(member)), encoding="gb18030")
                    book["合约代码"] = book["合约代码"].str.strip()
                    mo = book["合约代码"].str.fullmatch(r"MO[0-9]{4}-P-[0-9]+", na=False)
                    cffex_days[day] = book.loc[mo].set_index("合约代码", verify_integrity=True)
            try:
                book = cffex_days[day]
                row.update(open=float(book.at[contract, "今开盘"]), volume=float(book.at[contract, "成交量"]))
                row["positive_actual_open"] = row["open"] > 0 and row["volume"] > 0
            except KeyError:
                row["reason"] = "Dated official file has no such contract"
        if day is not None:
            row["days_elapsed"] = (pd.Timestamp(day) - pd.Timestamp(old.date)).days
            row["contract_month_strictly_after_execution_month"] = "20" + match.group(1) > day[:7].replace("-", "")
            if row["positive_actual_open"]:
                row["open_minus_original_close"] = row["open"] - old.price
        matches.append(row)
    out = pd.DataFrame(matches)
    affected = out.loc[out.in_window]
    evidence = {
        "status": "PASS" if affected.positive_actual_open.all() else "INCOMPLETE_PRICE_COVERAGE",
        "input_hashes": {str(p.relative_to(BUNDLE)): sha(p) for p in sorted(frozen)},
        "all_original_trade_sides": len(out),
        "all_true_open_sides": int(out.positive_actual_open.sum()),
        "window_original_trade_sides": len(affected),
        "window_true_open_sides": int(affected.positive_actual_open.sum()),
        "rows": out.to_dict(orient="records"),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # Serialize missing fields as JSON null.
    evidence["rows"] = json.loads(out.to_json(orient="records"))
    args.out.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print({k: v for k, v in evidence.items() if k not in {"rows", "input_hashes"}})
    print(affected[["product", "signal_date", "execution_date", "old_symbol", "open", "volume",
                    "contract_month_strictly_after_execution_month"]].to_string(index=False))
    if not out.positive_actual_open.all():
        print(out.loc[~out.positive_actual_open].to_string(index=False, max_rows=12))


if __name__ == "__main__":
    main()
