#!/usr/bin/env python
"""Unified read-only research entrypoint for IC/IM momentum-sleeve v1.2."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

import ic_mainline_v1_2 as ic
import im_mainline_v1_2 as im


ROOT = Path(__file__).resolve().parent
VERSION = "ic_im_mainline_v1_2"
STATUS = "research_candidate_not_live_authority"
SPEC_PATH = ROOT / "docs" / "ic_im_mainline_v1_2_spec.md"


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def rule_manifest() -> dict[str, Any]:
    return {
        "version": VERSION,
        "status": STATUS,
        "products": {
            "IC": ic.rule_manifest(),
            "IM": im.rule_manifest(),
        },
        "cross_product_capital_allocation": "not_defined",
        "cross_product_performance": "not_claimed",
        "orders": "not_generated",
        "provenance": {
            "ic_module": "ic_mainline_v1_2.py",
            "im_module": "im_mainline_v1_2.py",
            "spec": str(SPEC_PATH),
            "source_sha256": {
                "ic_module": _sha256(ROOT / "ic_mainline_v1_2.py"),
                "im_module": _sha256(ROOT / "im_mainline_v1_2.py"),
                "ic_spec": _sha256(ic.SPEC_PATH),
                "im_spec": _sha256(im.SPEC_PATH),
                "combined_spec": _sha256(SPEC_PATH),
            },
        },
    }


def load_authoritative_local_state() -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    ic_schedule, ic_audit = ic.load_authoritative_local_state()
    im_schedule, im_audit = im.load_authoritative_local_state()

    ic_dates = pd.to_datetime(ic_schedule["date"]).reset_index(drop=True)
    im_dates = pd.to_datetime(im_schedule["date"]).reset_index(drop=True)
    date_parity = bool(ic_dates.equals(im_dates))
    if not date_parity:
        raise RuntimeError("IC and IM v1.2 target dates are not identical")
    if ic_audit["status"] != STATUS or im_audit["status"] != STATUS:
        raise RuntimeError("A product leg lost its research-only status")

    audit = {
        "version": VERSION,
        "status": STATUS,
        "start": ic_dates.min().date().isoformat(),
        "end": ic_dates.max().date().isoformat(),
        "rows_per_product": int(len(ic_dates)),
        "date_index_parity": date_parity,
        "cross_product_capital_allocation": "not_defined",
        "cross_product_performance": "not_claimed",
        "orders_generated": False,
        "IC": ic_audit,
        "IM": im_audit,
        "latest_state": {
            "IC": ic_audit["latest_state"],
            "IM": im_audit["latest_state"],
        },
    }
    return {"IC": ic_schedule, "IM": im_schedule}, audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-local", action="store_true")
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()
    if args.output_json and not args.audit_local:
        parser.error("--output-json requires --audit-local")

    payload: dict[str, Any] = {"rules": rule_manifest()}
    if args.audit_local:
        _schedules, audit = load_authoritative_local_state()
        payload["local_audit"] = audit
        if args.output_json:
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            payload["audit_output"] = str(args.output_json.resolve())
            args.output_json.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

