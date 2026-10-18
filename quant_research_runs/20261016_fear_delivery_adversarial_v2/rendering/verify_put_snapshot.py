"""Read-only independent rendering of the actual 13:02 handoff snapshot."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
SOURCE = ROOT / 'quant_research_runs/20261016_put_delivery_completion_v1/handoff-38853603565/report/result.json'
raw = SOURCE.read_bytes()
payload = json.loads(raw)
signal = payload['signals']['IC']
assert signal['v14_core_put_current_contract'] == '510500P2612M07500'
assert signal['v14_core_put_contract'] == '510500P2612M07500'
assert signal['put_target_contract'] == '510500P2612M06500'
assert signal['put_current_core_qty'] == 10 and signal['put_target_core_qty'] == 17
mode = sys.argv[1]
if mode == 'root':
    sys.path.insert(0, str(ROOT))
    import poe_ic_im_mainline_v1_4_bot as bot
    saved = deepcopy(payload['signals'])
    for item in saved.values(): item['market_date'] = date.fromisoformat(item['market_date'])
    before = deepcopy(saved)
    chunks = []
    class Capture:
        @contextmanager
        def start_message(self):
            class Message:
                def write(self, text): chunks.append(str(text))
            yield Message()
    bot.poe = Capture()
    bot._now_beijing = lambda: datetime(2026, 10, 16, 13, 2, 39, tzinfo=bot.BEIJING)
    bot.build_live_signal_with_transport_retry = lambda p, *args: deepcopy(saved[p])
    bot._SIGNAL_OBSERVER = None
    bot.ICIMMainlinesBot()._handle_signal(('IC', 'IM'), 'intraday')
    text = ''.join(chunks)
    (OUT / 'put_root_actual_snapshot.md').write_text(text, encoding='utf8')
    assert '所以，今日（2026-10-16）收盘候选共 **17张**' in text
    assert '期货下一交易日：**2026-10-19**' in text
    assert '下一交易日张数' not in text and '下一交易日规范化张数' not in text
    assert '所以，下一交易日共' not in text
    assert '510500P2612M06500' in text and '510500P2612M07500' in text
    assert saved == before
else:
    base = ROOT / 'quant_research_runs' / ('20261016_put_delivery_completion_v1' if mode == 'completion' else '20261016_fear_single_source_delivery_v1') / 'cloud'
    sys.path.insert(0, str(base / 'scripts'))
    import build_ic_im_v1_4_digest as mail
    import ic_im_v1_4_put_display as display
    before = deepcopy(payload)
    subject, plain, _ = mail.build_success(payload, '', '')
    html = mail.build_success_html(payload, '')
    (OUT / f'put_{mode}_actual_snapshot.txt').write_text(subject+'\n'+plain, encoding='utf8')
    (OUT / f'put_{mode}_actual_snapshot.html').write_text(html, encoding='utf8')
    actual_target = display.ic_contract(signal, 'target', 'core')
    actual_current = display.ic_contract(signal, 'current', 'core')
    checks = {
        'current_remains_07500': actual_current == '510500P2612M07500',
        'ordinary_candidate_is_06500': actual_target == '510500P2612M06500',
        'plain_labels_put_today': '2026-10-16收盘维护候选，盘尾复核：合计17张（核心17张 510500P2612M06500' in plain,
        'html_labels_put_today': '2026-10-16收盘维护候选，盘尾复核：合计17张（核心17张 510500P2612M06500' in html,
        'futures_and_put_separate': '期货：下一交易日2026-10-19研究目标' in plain and 'Put：2026-10-16收盘维护候选（盘尾复核）' in plain,
        'input_unchanged': before == payload,
    }
    (OUT / f'put_{mode}_checks.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf8')
    assert all(checks.values()), checks
assert SOURCE.read_bytes() == raw
print(json.dumps({'mode': mode, 'status': 'PASS', 'input_sha256':hashlib.sha256(raw).hexdigest()}))
