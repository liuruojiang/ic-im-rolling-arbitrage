"""Read-only inventory; this program never initializes or appends a StateStore."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import ic_im_adjusted_put_migration_v1 as migration
import poe_ic_im_v1_4_state as state

FIXTURE = ROOT / "tests/fixtures/icim_external_review_round2_20261021"
RUNTIME = ROOT / "runtime/ic_im_v1_4_r1"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot():
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in RUNTIME.rglob("*")
            if p.is_file() and p.name != ".ledger.lock"}


def positions(anchor):
    rows = []
    for sleeve, contract, security, quantity in [
        ("core", "v14_core_put_contract", "v14_core_put_security_id", "v14_core_put_qty"),
        ("momentum", "post_put_contract", "post_put_security_id", "verified_momentum_put_qty"),
        ("short", "v14_short_put_contract", "v14_short_put_security_id", "v14_short_put_qty_normalized"),
    ]:
        if float(anchor.get(quantity) or 0) > 0:
            rows.append(dict(sleeve=sleeve, contract=anchor.get(contract), security_id=anchor.get(security),
                             quantity=anchor[quantity], quantity_field=quantity))
    return rows


def main():
    before = snapshot()
    manifest = read(FIXTURE / "manifest.json")
    assert all(sha(FIXTURE / name) == digest for name, digest in manifest.items())
    adjusted = migration.adjustment_terms(read(FIXTURE/"sse_adjustment_20260715.json"),
                                         effective_date="2026-07-15")
    master = migration.master_terms(read(FIXTURE/"sse_contract_master.json"), as_of="2026-10-20")
    quote = read(FIXTURE/"sse_december_identity.json")
    a_crosschecks = []
    for security, (_, terms) in adjusted.items():
        if terms.contract.startswith("510500P2612"):
            bound = migration.bind_daily_quote(terms, quote, as_of="2026-10-21")
            current = master[security]
            assert (bound.contract, bound.strike, bound.unit) == (current.contract, current.strike, current.unit)
            a_crosschecks.append(bound.json())
    records, exposures, previous = [], [], None
    for path in sorted((RUNTIME/"journal").glob("*.json")):
        record = read(path)
        state._validate_record(record)
        assert state._digest(record) == record["digest"]
        if previous:
            assert record["sequence"] == previous["sequence"]+1
            assert record["previous_digest"] == previous["digest"]
        anchor = record["products"]["IC"]
        held = positions(anchor)
        for row in held:
            security = row["security_id"]
            row["known_july_adjusted_id"] = security in adjusted
            row["label_is_a"] = isinstance(row["contract"], str) and row["contract"][11:12] == "A"
            if row["known_july_adjusted_id"] or row["label_is_a"]:
                exposures.append(dict(file=path.name, **row))
            # Do not extrapolate a current snapshot to the other 18 dates.
            if record["verified_day"] == "2026-10-20":
                terms = master.get(security)
                assert terms is not None and terms.contract == row["contract"] and terms.unit == 10000
                row["exact_dated_master_verified"] = True
            else:
                row["exact_dated_master_verified"] = False
        pending = {name: anchor.get(name) for name in ["v14_ordinary_put_pending", "v14_short_put_open_pending"]
                   if anchor.get(name) is not None}
        records.append(dict(file=path.name, sequence=record["sequence"], verified_day=record["verified_day"],
                            digest=record["digest"], active_positions=held,
                            pending_plan_types=list(pending), profit_pending=bool(anchor.get("v14_profit_pending"))))
        previous = record
    latest = read(RUNTIME/"latest.json")
    assert records and latest == previous
    after = snapshot()
    if before != after:
        raise RuntimeError("Concurrent production change during inventory: refuse a stable-snapshot claim")
    result = dict(observed_at=datetime.now(timezone.utc).isoformat(), runtime_before=before, runtime_after=after,
                  runtime_unchanged=True, records=records, record_count=len(records),
                  latest_sequence=latest["sequence"], latest_day=latest["verified_day"],
                  known_july_adjustment_exposures=exposures, real_a_quote_crosschecks=a_crosschecks,
                  real_a_put_crosscheck_count=len(a_crosschecks), fixture_hashes=manifest,
                  master_actual_date="2026-10-20", quote_actual_date="2026-10-21", quote_is_intraday=True,
                  cost_examples_are_synthetic=True, engine_a_supported=False,
                  blockers=["No independently verified daily master for the other historical dates",
                            "A unit/cash/plan migration is not wired into the production engine",
                            "Normalized short quantity is not an account contract count",
                            "Local inventory does not establish broker or remote holdings"])
    target = HERE/"local_state_inventory.json"
    if target.exists():
        raise RuntimeError("Refuse to overwrite prior inventory evidence")
    target.write_bytes(json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"))
    print(json.dumps(dict(records=len(records), latest=latest["sequence"],
                         adjustment_exposures=len(exposures), real_a_puts=len(a_crosschecks))))


if __name__ == "__main__":
    main()
