"""Retry only failed transport batches; never replace the failed receipts."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import survey


def main():
    run = survey.HERE / 'probe_04'
    run.mkdir(exist_ok=False)
    (run / 'raw').mkdir()
    original_get = survey.requests.get
    def get(url, **kwargs):
        kwargs['timeout'] = (8, 10)
        return original_get(url, **kwargs)
    survey.requests.get = get
    prior = json.loads((survey.HERE / 'probe_03/receipt.json').read_text(encoding='utf-8'))
    specs = [(r['name'], r['family'], r['url'], r['params']) for r in prior['rows'] if r['status'] != 'RECEIVED']
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = [f.result() for f in as_completed([pool.submit(survey.probe, run, s) for s in specs])]
    survey.save(run / 'receipt.json', {'rows': rows, 'retry_of': 'probe_03',
        'script_sha256': survey.sha(Path(__file__))})


if __name__ == '__main__':
    main()
