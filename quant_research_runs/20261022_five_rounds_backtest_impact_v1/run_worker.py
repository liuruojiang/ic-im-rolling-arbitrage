"""Isolated offline workers. Output relocation only; original failures retained."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
EARLY = ROOT / 'quant_research_runs/20261021_external_review_v1/backup'
HIST = ROOT / 'quant_research_runs/20261016_put_execution_repairs_v1'


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['performance', 'execution_history', 'ic_research', 'im_research'])
    parser.add_argument('version', choices=['before', 'after'])
    args = parser.parse_args()
    destination = HERE / f'{args.mode}_{args.version}'
    destination.mkdir(exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    tick = time.monotonic()
    requests_seen = []
    temp_state = HERE / 'temporary_state' / f'{args.mode}_{args.version}'
    temp_state.mkdir(parents=True, exist_ok=False)
    os.environ['ICIM_STATE_DIR'] = str(temp_state)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    if args.version == 'before':
        sys.path.insert(0, str(EARLY))
    import requests
    def no_network(self, method, url, *a, **kw):
        requests_seen.append({'method': str(method), 'url': str(url)})
        raise RuntimeError('AUDIT_OFFLINE_NETWORK_BLOCKED: ' + str(url))
    requests.sessions.Session.request = no_network
    result = {'mode': args.mode, 'version': args.version, 'started_at': started,
              'network_requests': requests_seen,
              'scope': 'Frozen listed-history theoretical research; no production ledger writes.'}
    status = 0
    try:
        # Pin canonical module before nested historical importers prepend ROOT.
        import poe_ic_im_mainline_v1_4_bot as bot
        result['loaded_bot_path'] = bot.__file__
        result['loaded_bot_sha256'] = sha(bot.__file__)
        if args.mode == 'performance':
            import pandas as pd
            output = {}
            for product in ('IC', 'IM'):
                frame = bot.performance_frame(product, date(1900, 1, 1), date(2026, 8, 14),
                                              allow_invalidated_im_history=True)
                frame.to_csv(destination / f'{product}.csv', float_format='%.17g')
                output[product] = {'rows': len(frame), 'start': str(frame.index.min()),
                                   'end': str(frame.index.max()), 'metrics': bot.performance_metrics(frame),
                                   'sha256': sha(destination / f'{product}.csv'),
                                   'IM_old_history_invalidated': product == 'IM'}
            result['products'] = output
        elif args.mode == 'execution_history':
            module = load('impact_execution_history', HIST / 'run_history.py')
            module.HERE = destination
            original_policy = HIST / 'backups/ic_im_v1_4_policy.py'
            life_relative = module.NATIVE.relative_to(ROOT) / 'native_fix4_seller_lifecycle_v1.py'
            for source, target in [(original_policy, destination / 'backups/ic_im_v1_4_policy.py'),
                                   (HIST / 'backups' / life_relative, destination / 'backups' / life_relative)]:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            result['historical_policy_is_explicitly_pinned'] = True
            module.main()
        else:
            import importlib
            name = ('research_ic_v13_to_v14_final_joint_gate_v1' if args.mode == 'ic_research'
                    else 'research_im_v14_mom_negative_put_qty_2_vs_3_v1')
            module = importlib.import_module(name)
            original = json.loads((module.RUN / 'scan_meta.json').read_text(encoding='utf-8-sig'))
            original.update(phase='init', outputs={}, audit={}, decision=None,
                            stability_label='read_only_backtest_impact_rerun')
            (destination / 'scan_meta.json').write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding='utf-8')
            module.RUN = destination
            module.main()
        result['status'] = 'PASS'
    except Exception as exc:
        result.update(status='FAIL', error_type=type(exc).__name__, error=str(exc),
                      traceback=traceback.format_exc())
        traceback.print_exc()
        status = 1
    finally:
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        result['seconds'] = time.monotonic() - tick
        result['loaded_local_sources'] = {}
        for name, module in list(sys.modules.items()):
            raw = getattr(module, '__file__', None)
            if raw:
                p = Path(raw)
                if p.suffix == '.py' and p.is_file() and (p.is_relative_to(ROOT)):
                    result['loaded_local_sources'][str(p.relative_to(ROOT))] = sha(p)
        (destination / 'receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k: result[k] for k in ['mode', 'version', 'status', 'seconds']}, ensure_ascii=False), flush=True)
    raise SystemExit(status)


if __name__ == '__main__':
    main()
