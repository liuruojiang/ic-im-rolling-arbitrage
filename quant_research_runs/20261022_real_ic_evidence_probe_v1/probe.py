"""Read-only live IC prerequisites; never initializes or advances a ledger."""
import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main():
    import sys
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    original_env = {n: os.environ.get(n) for n in ('ICIM_STATE_DIR', 'ICIM_EVIDENCE_DIR')}
    os.environ['ICIM_STATE_DIR'] = str(Path(tempfile.gettempdir()) / 'icim_evidence_probe_no_store')
    os.environ.pop('ICIM_EVIDENCE_DIR', None)
    # Import only the strategy, never the server or StateStore.
    import poe_ic_im_mainline_v1_4_bot as bot
    clock = datetime.now(bot.BEIJING)
    target = HERE / ('probe_' + clock.strftime('%Y%m%d_%H%M%S_%f') + '.json')
    runtime_before = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in (ROOT / 'runtime').rglob('*') if p.is_file()}
    result = {'actual_clock': clock.isoformat(), 'market_phase': bot._market_phase(clock),
              'configured_paths_before_probe': original_env, 'scope': 'live prerequisite probe, not a replay pass',
              'production_ledger_write': False, 'requests': []}
    original_get = bot.requests.get
    def trace_get(url, *args, **kwargs):
        row = {'url': str(url)}
        result['requests'].append(row)
        try:
            response = original_get(url, *args, **kwargs)
            row['http_status'] = response.status_code
            return response
        except Exception as exc:
            # Do not serialize credential-bearing proxy/error URLs.
            row['error_type'] = type(exc).__name__
            raise
    bot.requests.get = trace_get
    try:
        with bot.runtime_clock(clock), bot._network_budget(8):
            months = bot.fetch_sse_510500_expiries()
        result.update(status='LIVE_LISTING_AVAILABLE', listed_months=months,
                      note='Listing alone is not a complete IC original-session package.')
    except Exception as exc:
        result.update(status='LIVE_CAPTURE_PREREQUISITE_FAILED', error_type=type(exc).__name__,
                      reason='Actual listing request failed; no complete original-day IC package can be certified by this attempt.')
    runtime_after = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in (ROOT / 'runtime').rglob('*') if p.is_file()}
    result['formal_runtime_unchanged'] = runtime_before == runtime_after
    assert result['formal_runtime_unchanged']
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    print('saved=' + str(target.relative_to(ROOT)))


if __name__ == '__main__':
    main()
