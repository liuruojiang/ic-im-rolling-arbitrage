from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_mo_call_threat_roll_extended_proxy_v26r1 as base


ROOT = Path(__file__).resolve().parent
VERSION = "im_mo_call_threat_roll_extended_price_proxy_v26r2"
SPEC_SHA256 = "ea261c2b36f7c260cce2835cc0e20389ed09c1842620218a81cb2ebcdaaaf68a"
SCAN_NAME = (
    "20260819_new_strategy_research_im_mo_call_threat_roll_extended_price_proxy_"
    "v26r2_price_index_p25_p50_p75_fixed_threat5_split_pe_history"
)


def configure_base() -> None:
    base.VERSION = VERSION
    base.SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
    base.SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
    base.SPEC_SHA256 = SPEC_SHA256
    base.OUTPUT = ROOT / "outputs" / VERSION
    base.STAGING = ROOT / "outputs" / f".{VERSION}.staging"
    base.SCAN = ROOT / "quant_param_scan_runs" / SCAN_NAME
    base.__file__ = str(Path(__file__).resolve())
    base.FROZEN_HASHES = {
        **base.FROZEN_HASHES,
        ROOT / "im_mo_call_threat_roll_extended_proxy_v26r1.py": (
            "562251ffad4f372b9e92708ad79b59ad74859b8452f529a237fe429c1b49851e"
        ),
        ROOT / "docs" / "im_mo_call_threat_roll_extended_proxy_v26r1_spec.md": (
            "82236d6e0125d7c53e8467650b198384f3c6afc016e0577a11559c214977971d"
        ),
        ROOT / "outputs" / "im_mo_call_threat_roll_extended_proxy_v26r1" / "output_manifest.json": (
            "51a75b882d942e9c48e4e953e5522f00a69750bef06c9681764d4b5d00db81b6"
        ),
    }


def price_proxy_base(market: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    frame = market[["date", "spot_close"]].copy()
    frame["gross_ret"] = frame["spot_close"].pct_change().fillna(0.0)
    roll_dates = set(
        pd.to_datetime(
            events.loc[events["reason"].eq("monthly"), "current_expiry"]
        )
    )
    frame["cost_rate"] = np.where(frame["date"].isin(roll_dates), 0.0002, 0.0)
    frame.loc[frame.index[0], "cost_rate"] = 0.0001
    frame["put_pnl_ret"] = 0.0
    frame["put_cost_rate"] = 0.0
    frame["put_mark_fraction"] = 0.0
    return frame


def price_decision_result(
    axis_pass: dict[str, bool], pair_table: pd.DataFrame
) -> dict[str, Any]:
    result = original_decision_result(axis_pass, pair_table)
    mapping = {
        "extended_proxy_directionally_supported_both_axes": (
            "extended_price_proxy_directionally_supported_both_axes"
        ),
        "extended_proxy_axis_dependent": "extended_price_proxy_axis_dependent",
        "iv_assumption_sensitive": "extended_price_proxy_iv_assumption_sensitive",
        "extended_proxy_not_supported": "extended_price_proxy_not_supported",
    }
    result["conclusion"] = mapping[result["conclusion"]]
    result["evidence_scope"] = (
        "prepublication_price_index_backcast_and_synthetic_call_proxy_only"
    )
    return result


def price_record_text(*args: Any, **kwargs: Any) -> str:
    text = original_record_text(*args, **kwargs)
    return (
        text.replace("v26r1", "v26r2")
        .replace("中证1000TRI+合成Call", "中证1000价格指数+合成Call")
        .replace("发布前指数回算", "发布前价格指数回算")
    )


def rewrite_output_manifest() -> None:
    manifest_path = base.OUTPUT / "data_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["proxy_scope"] = (
        "CSI1000 price index plus synthetic Call; no historical IM basis or Put"
    )
    manifest["supporting_tri_use"] = "trailing dividend-yield estimate only"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    command_path = base.OUTPUT / "command_log.txt"
    command_path.write_text(f"python {Path(__file__).name}\n", encoding="utf-8")
    output_manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "files": {
            path.name: {"sha256": base.sha256(path), "bytes": path.stat().st_size}
            for path in sorted(base.OUTPUT.iterdir())
            if path.is_file() and path.name != "output_manifest.json"
        },
    }
    (base.OUTPUT / "output_manifest.json").write_text(
        json.dumps(output_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def price_write_outputs(*args: Any, **kwargs: Any) -> None:
    original_write_outputs(*args, **kwargs)
    rewrite_output_manifest()


def price_update_scan(*args: Any, **kwargs: Any) -> None:
    original_update_scan(*args, **kwargs)
    meta_path = base.SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["baseline"]["proxy"] = (
        "CSI1000 price index plus synthetic Call; no historical IM basis or Put"
    )
    meta["data_snapshot"]["tri_source_use"] = "trailing dividend-yield estimate only"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    command_path = base.SCAN / "command_log.txt"
    command_path.write_text(
        command_path.read_text(encoding="utf-8").replace(
            "im_mo_call_threat_roll_extended_proxy_v26r1.py",
            Path(__file__).name,
        ),
        encoding="utf-8",
    )


original_decision_result = base.decision_result
original_record_text = base.record_text
original_write_outputs = base.write_outputs
original_update_scan = base.update_scan


def main() -> None:
    configure_base()
    base.proxy_base = price_proxy_base
    base.decision_result = price_decision_result
    base.record_text = price_record_text
    base.write_outputs = price_write_outputs
    base.update_scan = price_update_scan
    base.main()


if __name__ == "__main__":
    main()
