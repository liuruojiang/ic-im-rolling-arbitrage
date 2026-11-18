"""Input and result gates for the frozen 18 November FIX11 research release.

Treat this reviewed module as the local trust anchor. Do not derive the pins
from a manifest beside the inputs being checked. The pins attest bytes for
this release, rather than authenticity of arbitrary future market snapshots.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path
from xml.etree import ElementTree

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
FROZEN = ROOT / "outputs/fix11_nav_1y_20261118_close"
SIGNAL_DAY = date(2026, 11, 17)
MARK_DAY = date(2026, 11, 18)

# Prior frozen replay receipt and independent review committed these inputs.
MARKET_MANIFEST_SHA256 = "555f382d8a035a971d0ef870faf9cb138ac4859b2c4fa637f13470e964de8d61"
# Canonical SHA map includes the 53 historical CFFEX ZIPs, also consumed by the
# engine, beyond the 14 top-level members of the original refresh manifest.
MARKET_FILE_TREE_SHA256 = "4d026a0a10df93b25d61d33085bc389cf600394bd32933146c9b057ce0754a2e"
OFFICIAL_TERMINAL_SHA256 = "44bd3c768d12b489230064a2a244c0bf197b020af08a00eb19fffe43c796be8d"
LEGACY_STAGE_RECEIPT_SHA256 = "b8056a1a493f3cc0677816f4ded8465d277ec8eed20b57fa0812d53800b7682c"

REQUIRED_STAGE_FILES = frozenset({
    "native_fix4_ic_risk_signals_v1.csv.gz",
    "native_fix4_ic_noseller_targets_v2.csv.gz",
    "native_fix4_ic_noseller_targets_v2.json",
    "native_fix4_im_risk_signals_v1.csv.gz",
    "native_fix4_im_noseller_targets_v3.csv.gz",
    "native_fix4_im_noseller_targets_v3.json",
    "native_fix4_seller_candidates_v1.csv.gz",
    "native_fix4_seller_candidates_v1.json",
})

PREFIX_FIELDS = (
    "nav", "return_net", "equity_close", "cash_close", "margin_reserved",
    "option_value", "etf_value", "fees_cumulative",
)
PRODUCTS = ("IC", "IM")
SHA_PATTERN = re.compile(r"[0-9a-f]{64}")


def sha256(path: Path) -> str:
    with Path(path).open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def _duplicate_rejecting_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value):
    raise RuntimeError(f"non-finite JSON number: {value}")


def read_receipt(path: Path) -> dict:
    result = json.loads(
        Path(path).read_text(encoding="utf-8"),
        object_pairs_hook=_duplicate_rejecting_object,
        parse_constant=_reject_json_constant,
    )
    if not isinstance(result, dict):
        raise RuntimeError(f"expected JSON object in {path}")
    return result


def _regular_file(path: Path) -> bool:
    return not path.is_symlink() and not path.is_junction() and path.is_file()


def _market_tree(raw: Path) -> dict[str, str]:
    if raw.is_symlink() or raw.is_junction() or not raw.is_dir():
        raise RuntimeError("market input directory is missing or linked")
    tree = {}
    for entry in raw.rglob("*"):
        if entry.is_symlink() or entry.is_junction():
            raise RuntimeError(f"linked market input is not admitted: {entry.name}")
        if entry.is_file():
            tree[entry.relative_to(raw).as_posix()] = sha256(entry)
    return tree


def _tree_sha256(tree: dict[str, str]) -> str:
    canonical = json.dumps(tree, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _xml_values(xml_file: Path, pattern: str) -> dict[str, tuple[float, float, float, int]]:
    all_rows = ElementTree.parse(xml_file).getroot().iter("dailydata")
    selected = {}
    for row in all_rows:
        contract = row.findtext("instrumentid")
        if not isinstance(contract, str) or re.fullmatch(pattern, contract) is None:
            continue
        if contract in selected or row.findtext("tradingday") != MARK_DAY.strftime("%Y%m%d"):
            raise RuntimeError(f"ambiguous instrument/day in independent CFFEX XML: {contract}")

        def number(name):
            value = row.findtext(name)
            return float(value) if value not in {None, ""} else np.nan

        selected[contract] = (
            number("openprice"), number("closeprice"), number("settlementprice"),
            int(row.findtext("volume")),
        )
    if not selected:
        raise RuntimeError(f"independent CFFEX XML contains no rows for {pattern}")
    return selected


def verify_terminal_official_prices(raw: Path, xml_file: Path | None = None) -> dict[str, int]:
    """Compare the frozen terminal data to separately pinned exchange bytes."""
    xml_file = Path(xml_file) if xml_file is not None else FROZEN / "source_crosscheck/cffex_20261118_http.bin"
    if not _regular_file(xml_file) or sha256(xml_file) != OFFICIAL_TERMINAL_SHA256:
        raise RuntimeError("independent terminal CFFEX XML does not match its pin")
    checks = {}
    for product in PRODUCTS:
        expected = _xml_values(xml_file, rf"{product}\d{{4}}")
        actual = pd.read_csv(raw / f"cffex_{product.lower()}_contracts.csv")
        actual = actual.loc[actual.date.eq(MARK_DAY.isoformat())].set_index("contract", verify_integrity=True)
        if set(actual.index) != set(expected):
            raise RuntimeError(f"terminal {product} futures coverage differs from official XML")
        for contract, values in expected.items():
            observed = actual.loc[contract, ["open", "close", "settle", "volume"]].to_numpy(float)
            if not np.allclose(observed, values, atol=1e-9, rtol=0, equal_nan=True):
                raise RuntimeError(f"terminal futures values differ from independent XML: {contract}")
        checks[product] = len(expected)
    expected = _xml_values(xml_file, r"MO\d{4}-P-\d+")
    history = pd.read_csv(raw / "sina_mo_put_histories.csv.gz")
    actual = history.loc[history.date.eq(MARK_DAY.isoformat())].set_index("instrument", verify_integrity=True)
    if set(actual.index) != set(expected):
        raise RuntimeError("terminal MO Put coverage differs from official XML")
    for contract, (opening, closing, settlement, volume) in expected.items():
        observed = actual.loc[contract, ["openprice", "raw_close", "settle", "volume", "lastprice"]].to_numpy(float)
        values = (opening, closing, settlement, volume, closing if volume > 0 else settlement)
        if not np.allclose(observed, values, atol=1e-9, rtol=0, equal_nan=True):
            raise RuntimeError(f"terminal MO Put values differ from independent XML: {contract}")
    checks["MO_put"] = len(expected)
    return checks


def verify_frozen_market_snapshot(raw: Path, manifest_path: Path):
    raw, manifest_path = Path(raw), Path(manifest_path)
    if not _regular_file(manifest_path) or sha256(manifest_path) != MARKET_MANIFEST_SHA256:
        raise RuntimeError("research input manifest does not match its independent frozen pin")
    manifest = read_receipt(manifest_path)
    if manifest.get("classification") != "isolated_research_raw_inputs_for_latest_fix11_replay":
        raise RuntimeError("unexpected research source manifest")
    if (manifest.get("latest_completed_signal_input_date") != SIGNAL_DAY.isoformat()
            or manifest.get("latest_completed_market_date") != MARK_DAY.isoformat()
            or manifest.get("latest_mark_date") != MARK_DAY.isoformat()):
        raise RuntimeError("research signal/mark dates differ from the frozen release")
    tree = _market_tree(raw)
    direct = {name: digest for name, digest in tree.items() if "/" not in name}
    if manifest.get("extended_input_hashes") != direct:
        raise RuntimeError("research input hashes differ from the frozen manifest")
    if len(tree) != 67 or _tree_sha256(tree) != MARKET_FILE_TREE_SHA256:
        raise RuntimeError("research input tree differs from its independently pinned contents")
    verify_terminal_official_prices(raw)
    return SIGNAL_DAY, MARK_DAY, manifest_path


def verify_resume_receipt(
    receipt_path: Path,
    inputs: Path,
    *,
    manifest_sha256: str,
    formal_source_sha256: str,
    expected_receipt_sha256: str | None = None,
) -> dict:
    """Strictly validate the predecessor's eight immutable stage members.

    Alternative receipts require an independently supplied trusted pin.
    A different economic model must calculate its own new stage outputs.
    """
    receipt_path, inputs = Path(receipt_path), Path(inputs)
    pin = LEGACY_STAGE_RECEIPT_SHA256 if expected_receipt_sha256 is None else expected_receipt_sha256
    if not isinstance(pin, str) or SHA_PATTERN.fullmatch(pin) is None:
        raise RuntimeError("missing independent resume receipt pin")
    if not _regular_file(receipt_path) or sha256(receipt_path) != pin:
        raise RuntimeError("resume receipt does not match its independent frozen pin")
    receipt = read_receipt(receipt_path)
    if receipt.get("status") != "PASS":
        raise RuntimeError("resume receipt did not pass")
    if (receipt.get("manifest_sha256") != manifest_sha256
            or receipt.get("formal_source_sha256") != formal_source_sha256):
        raise RuntimeError("saved research stages are bound to a different source snapshot")
    declared = receipt.get("input_stage_hashes")
    if not isinstance(declared, dict) or set(declared) != REQUIRED_STAGE_FILES:
        raise RuntimeError("resume receipt must name exactly the eight required stage files")
    for name, digest in sorted(declared.items()):
        if not isinstance(digest, str) or SHA_PATTERN.fullmatch(digest) is None:
            raise RuntimeError(f"resume receipt has an invalid hash: {name}")
        path = inputs / name
        if not _regular_file(path) or sha256(path) != digest:
            raise RuntimeError(f"saved research stage changed: {name}")
    return receipt


def _day_string(value) -> str:
    if isinstance(value, pd.Timestamp):
        if pd.isna(value) or value != value.normalize() or value.tzinfo is not None:
            raise RuntimeError(f"not a plain exchange date: {value}")
        return value.date().isoformat()
    if type(value) is date:
        return value.isoformat()
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise RuntimeError(f"not a plain exchange date: {value}") from exc
        if parsed.isoformat() == value:
            return value
    raise RuntimeError(f"not a plain exchange date: {value}")


def _ordered_days(values, label):
    sessions = [_day_string(value) for value in values]
    if not sessions or sessions != sorted(set(sessions)):
        raise RuntimeError(f"empty, duplicate or out-of-order dates: {label}")
    return sessions


def _finite_columns(frame: pd.DataFrame, fields, label: str) -> np.ndarray:
    try:
        numbers = frame.loc[:, list(fields)].to_numpy(float)
    except (KeyError, ValueError, TypeError) as exc:
        raise RuntimeError(f"financial fields missing or nonnumeric: {label}") from exc
    if not np.isfinite(numbers).all():
        raise RuntimeError(f"missing or non-finite financial field: {label}")
    return numbers


def compare_prefix(daily: pd.DataFrame, old: pd.DataFrame, end: str) -> dict:
    """Exact calendar membership and finite, bounded numerical comparison."""
    end = _day_string(end)
    result = {}
    for product in PRODUCTS:
        reference = old.loc[old["product"].eq(product)].reset_index(drop=True)
        candidate = daily.loc[daily["product"].eq(product)].reset_index(drop=True)
        old_days = _ordered_days(reference.date, f"previous {product}")
        new_days = _ordered_days(candidate.date, f"candidate {product}")
        original_through = [day for day in old_days if day <= end]
        candidate_through = [day for day in new_days if day <= end]
        if original_through != candidate_through or not original_through or original_through[-1] != end:
            raise RuntimeError(f"previous published close-prefix dates changed: {product}")
        length = len(original_through)
        before = _finite_columns(reference.iloc[:length], PREFIX_FIELDS, f"previous {product}")
        after = _finite_columns(candidate.iloc[:length], PREFIX_FIELDS, f"candidate {product}")
        differences = np.max(np.abs(before - after), axis=0)
        errors = dict(zip(PREFIX_FIELDS, map(float, differences), strict=True))
        if errors["nav"] > 1e-10 or errors["return_net"] > 1e-10 or max(errors.values()) > 1e-5:
            raise RuntimeError(f"previous published economic close-prefix changed: {product}: {errors}")
        result[product] = {"rows": length, "max_abs_errors": errors}
    return {"status": "PASS", "through": end, "products": result}


def expected_exchange_days(formal, first, last) -> list[str]:
    """Get dates from the configured official calendar, independently of prices."""
    first, last = date.fromisoformat(_day_string(first)), date.fromisoformat(_day_string(last))
    if first > last:
        raise RuntimeError("official exchange calendar range is reversed")
    days = []
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        formal._require_official_exchange_calendar(day, "FIX11 chart calendar")
        if formal._is_exchange_trading_day(day):
            days.append(day.isoformat())
    return _ordered_days(days, "official exchange calendar")


def verify_chart_gate(daily: pd.DataFrame, parent_receipt: dict, *, officially_expected_days) -> dict:
    """Authorize an ordinary passed parent result and verify all required days."""
    if parent_receipt.get("status") != "PASS":
        raise RuntimeError("chart parent account receipt did not pass")
    days = _ordered_days(officially_expected_days, "official exchange calendar")
    if (parent_receipt.get("latest_completed_close") != days[-1]
            or parent_receipt.get("latest_mark_date") != days[-1]):
        raise RuntimeError("chart parent report date differs from the official calendar endpoint")
    if set(daily["product"]) != set(PRODUCTS):
        raise RuntimeError("chart requires exactly IC and IM accounts")
    coverage = {}
    for product in PRODUCTS:
        product_daily = daily.loc[daily["product"].eq(product)]
        observed = _ordered_days(product_daily.date, f"chart {product}")
        selected = [day for day in observed if days[0] <= day <= days[-1]]
        if selected != days or observed[-1] != days[-1]:
            missing = sorted(set(days) - set(selected))
            extra = sorted(set(selected) - set(days))
            raise RuntimeError(f"chart {product} disagrees with official calendar. Missing {missing}, extra {extra}")
        recent = product_daily.iloc[-len(days):]
        _finite_columns(recent, ("nav", "return_net"), f"chart {product}")
        coverage[product] = len(selected)
    return {"status": "PASS", "first": days[0], "last": days[-1], "rows": coverage}
