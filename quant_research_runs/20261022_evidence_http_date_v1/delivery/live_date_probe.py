"""Repeat real SSE responses under the new narrowly scoped identity rule."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
RUN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import poe_ic_im_mainline_v1_4_bot as bot
import ic_im_evidence_adapter_v1 as adapter
import ic_im_signal_evidence_v1 as evidence

trial = RUN / sys.argv[1]
trial.mkdir(exist_ok=False)
clock = datetime.now(bot.BEIJING)
reader, _ = adapter.load_original(bot, ROOT / 'data/icim_v14_original_inputs', 'IC', clock.date())
request = next(r for r in reader.context['raw'] if r['url'].endswith('tstyle/510500_12'))
params = evidence.decode(request['params'])
observations, errors = [], []
with evidence.capture_session(trial / 'evidence', clock.date(), clock,
                              {'capture_kind': 'postclose_Date_probe', 'original_close': False}) as session:
    session.call('probe.scope', {}, lambda: {'purpose': 'HTTP Date observation; no strategy signal'})
    with bot._network_budget(45):
        for attempt in range(3):
            try:
                start = datetime.now(bot.BEIJING)
                response = bot._evidence_http_get(request['url'], params=params, timeout=bot._bounded_timeout(12),
                    headers={'User-Agent': 'Mozilla/5.0 POE-IC-IM-Research/1.0', 'Referer': 'https://www.sse.com.cn/'})
                try:
                    response.raise_for_status()
                    payload = response.json()
                    observations.append({'attempt': attempt + 1, 'started_at': start.isoformat(),
                        'received_at': datetime.now(bot.BEIJING).isoformat(), 'market_date': payload.get('date'),
                        'market_time': payload.get('time'), 'body_sha256': hashlib.sha256(response.content).hexdigest(),
                        'headers': {k: response.headers[k] for k in ['Date', 'Content-Type', 'ETag', 'Last-Modified'] if k in response.headers}})
                finally:
                    response.close()
                if len(observations) >= 2:
                    break
                time.sleep(2)
            except Exception as exc:
                errors.append({'attempt': attempt + 1, 'type': type(exc).__name__, 'error': str(exc)[:650]})
    if len(observations) < 2:
        session.failures.append({'role': 'probe', 'resolved': False, 'error': 'two observations unavailable'})
    session.annotate('probe_observations', observations)
    session.annotate('probe_errors', errors)
result = {'status': 'COMPLETE' if session.context['complete'] else 'INCOMPLETE',
          'observations': observations, 'errors': errors, 'failures': session.failures,
          'manifest': str(session.manifest_path), 'manifest_sha256': session.manifest_sha256,
          'formal_signal_generated': False, 'original_close': False,
          'limitation': 'one post-close repeat probe, not multi-day stability evidence'}
if session.context['complete']:
    replay = evidence.load_session(trial / 'evidence', clock.date()).http_response(request['url'], params)
    result.update(replay_matching_recordings=replay['matching_recordings'],
                  replay_body_sha256=hashlib.sha256(replay['body']).hexdigest(),
                  distinct_dates=len({x['headers'].get('Date') for x in observations}),
                  distinct_market_bodies=len({x['body_sha256'] for x in observations}))
(trial / 'receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
