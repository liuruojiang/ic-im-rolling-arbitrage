"""Fresh bounded source probes, preserving the inherited proxy and TLS."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
previous = ROOT / "quant_research_runs/20261021_round3_source_validation_v1/probe_sources.py"
spec = importlib.util.spec_from_file_location("bounded_public_probe", previous)
probe_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe_module)
probe_module.HERE = HERE
PROBES = [
    ("sse_etf", "https://yunhq.sse.com.cn:32042/v1/sh1/list/self/510500",
     {"select": "code,cpxxextendname,last,change,chg_rate,amp_rate,volume,amount,prev_close"},
     "https://www.sse.com.cn/assortment/options/price/"),
    ("sse_tstyle", "https://yunhq.sse.com.cn:32042/v1/sho/list/tstyle/510500_11",
     {"select": "contractid,last,chg_rate,presetpx,exepx,code", "order": "contractid,ase"},
     "https://www.sse.com.cn/assortment/options/price/"),
    ("sina_mo_batch", "https://hq.sinajs.cn/etag.php",
     {"list": "P_OP_mo2612P7200,P_OP_mo2703P7200"},
     "https://stock.finance.sina.com.cn/"),
]


def main():
    target = HERE / "probe_results.json"
    if target.exists():
        raise FileExistsError("use a new experiment directory instead of overwriting source evidence")
    (HERE / "raw").mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(probe_module.probe, PROBES))
    target.write_text(json.dumps({"created_at": datetime.now(timezone.utc).isoformat(),
        "results": results, "inherited_proxy_preserved": True, "tls_verification_preserved": True,
        "strategy_imported": False, "ledger_written": False,
        "oracle_limits": "Actual acquisition only; response receipt alone does not validate source semantics."},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps([{key: row[key] for key in
        ("name", "status", "status_code", "bytes", "elapsed_seconds", "error_type") if key in row}
        for row in results], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
