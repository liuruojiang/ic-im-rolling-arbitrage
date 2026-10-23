"""Read-only, offline preflight for the next ledger session's original inputs."""
from datetime import date, timedelta
import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--source-pin", type=Path, required=True)
    parser.add_argument("--market-day", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    attempts = []
    result = {"status": "not_ready", "ledger_write_requested": False,
              "market_day": str(args.market_day), "evidence_dir": str(args.evidence_dir),
              "products": {}, "network_attempts": attempts}
    try:
        import requests

        def offline(_self, method, url, *a, **kw):
            attempts.append({"method": method, "url": str(url)})
            raise requests.ConnectionError("readiness check is strictly offline")

        requests.sessions.Session.request = offline
        import capture_ic_im_v1_4_inputs as capture
        pin = capture.SourcePin(args.source_pin)
        import poe_ic_im_mainline_v1_4_bot as bot
        import poe_ic_im_v1_4_state as state
        import ic_im_evidence_adapter_v1 as adapter
        pin.verify(capture.loaded_sources())
        state_root = args.state_dir.resolve(strict=True)
        before = capture.ledger_hashes(state_root)
        latest, _ = capture.load_ledger_readonly(state, state_root)
        verified = date.fromisoformat(str(latest["verified_day"])[:10])
        expected = bot._roll_forward_exchange_day(verified + timedelta(days=1), require_official=True)
        result["ledger"] = {k: latest[k] for k in ["verified_day", "sequence", "digest"]}
        result["next_ledger_session"] = str(expected)
        result["source_pin_sha256"] = pin.manifest_sha256
        if args.market_day != expected:
            raise RuntimeError("market-day must equal the next unverified ledger session")
        for product in ["IC", "IM"]:
            try:
                reader, closed = adapter.load_original(bot, args.evidence_dir, product, args.market_day)
                result["products"][product] = {
                    "status": "ready", "manifest": str(reader.manifest_path),
                    "manifest_body_sha256": reader.manifest_sha256,
                    "acquired_until": closed.isoformat(),
                    "typed_inputs": len(reader.context["entries"]),
                    "raw_responses": len(reader.context["raw"]),
                    "all_blobs_verified": True,
                }
            except Exception as exc:
                result["products"][product] = {"status": "unavailable", "error_type": type(exc).__name__, "error": str(exc)}
        result["ledger_paths_and_bytes_unchanged"] = before == capture.ledger_hashes(state_root)
        pin.verify(capture.loaded_sources())
        if all(p["status"] == "ready" for p in result["products"].values()) and not attempts and result["ledger_paths_and_bytes_unchanged"]:
            result["status"] = "ready_for_isolated_recovery"
        result["meaning"] = "Original input integrity only; formal persistence still requires complete, eligible IC and IM signals."
    except Exception as exc:
        result.update(error_type=type(exc).__name__, error=str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ready_for_isolated_recovery" else 2


if __name__ == "__main__":
    raise SystemExit(main())
