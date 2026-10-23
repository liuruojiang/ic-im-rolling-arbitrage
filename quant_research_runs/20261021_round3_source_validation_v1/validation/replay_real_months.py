"""Revalidate the retained real SSE listing through the current provider.

The source bytes stay untouched; corruptions below are explicit copied inputs.
No network provider, StateStore or strategy signal builder is called.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
import poe_ic_im_mainline_v1_4_bot as bot


def main():
    source = ROOT / "outputs/external_review_real_sources_20260911/sse_months_c73f6aee4b65.bin"
    original = source.read_bytes()
    expected_sha = "c73f6aee4b6596544a6bdc125b20eba653b01caa48bb4d9882b8a8688d0f5aa7"
    assert hashlib.sha256(original).hexdigest() == expected_sha
    payload = json.loads(original)
    production = ROOT / "poe_ic_im_mainline_v1_4_bot.py"
    before = hashlib.sha256(production.read_bytes()).hexdigest()
    with patch.object(bot, "_sse_quote_json", lambda *a, **kw: deepcopy(payload)):
        result = bot.fetch_sse_510500_expiries()
    assert result == ["202609", "202610", "202612", "202703"]
    rejected = []
    for fault in ["invalid_requested_month", "partial_pagination"]:
        changed = deepcopy(payload)
        if fault == "invalid_requested_month":
            next(row for row in changed["list"] if row[0] == "510500")[1] = "202713"
        else:
            changed["total"] += 1
        with patch.object(bot, "_sse_quote_json", lambda *a, **kw: deepcopy(changed)):
            try:
                bot.fetch_sse_510500_expiries()
            except RuntimeError as exc:
                rejected.append({"fault": fault, "rejected": True, "reason": str(exc)})
            else:
                raise AssertionError("corrupted copy was accepted: " + fault)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == expected_sha
    after = hashlib.sha256(production.read_bytes()).hexdigest()
    assert before == after
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source": source.relative_to(ROOT).as_posix(), "source_sha256": expected_sha,
        "source_market_date": payload["date"], "source_time": payload["time"],
        "source_rows": len(payload["list"]), "parsed_510500_months": result,
        "source_unchanged": True, "production_sha256": before,
        "source_before_equals_after": before == after,
        "corrupted_copy_checks": rejected, "passed": True,
        "real_preopen_or_morning_availability_verified": False,
    }
    (HERE / "real_months_replay.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
