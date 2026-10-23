"""Sanitized diagnostics through the already configured proxy; no bypass."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import socket
from urllib.parse import urlsplit

import requests

HERE = Path(__file__).resolve().parent
report = {'observed_at': datetime.now().astimezone().isoformat(), 'proxies': [], 'requests': []}
for name in ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY']:
    parsed = urlsplit(os.environ.get(name, ''))
    entry = {'name': name, 'scheme': parsed.scheme, 'host': parsed.hostname, 'port': parsed.port,
             'contains_auth': bool(parsed.username or parsed.password)}
    try:
        with socket.create_connection((parsed.hostname, parsed.port), timeout=3):
            entry['tcp_reachable'] = True
    except Exception as exc:
        entry.update(tcp_reachable=False, error_type=type(exc).__name__, winerror=getattr(exc, 'winerror', None))
    report['proxies'].append(entry)


def probe(url):
    row = {'url': url, 'started_at': datetime.now().astimezone().isoformat()}
    try:
        with requests.get(url, timeout=(12, 12), stream=True) as response:
            row.update(status=response.status_code, server=response.headers.get('Server'),
                       content_type=response.headers.get('Content-Type'))
    except Exception as exc:
        row.update(error_type=type(exc).__name__, error=str(exc)[:600])
    row['finished_at'] = datetime.now().astimezone().isoformat()
    return row


urls = ['https://www.rfc-editor.org/rfc/rfc9110.html',
        'https://yunhq.sse.com.cn:32042/v1/sho/list/tstyle/510500_12',
        'https://hq.sinajs.cn/etag.php?list=sh510500',
        'http://www.cffex.com.cn/quote_IC.txt']
with ThreadPoolExecutor(max_workers=4) as pool:
    report['requests'] = list(pool.map(probe, urls))
target = HERE / 'delivery' / ('network_probe_' + datetime.now().strftime('%H%M%S') + '.json')
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))
