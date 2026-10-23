"""Use Sina's documented literal-comma path; preserve even empty HTTP-200 trials."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import survey


def main():
    run = survey.HERE / 'probe_03'
    run.mkdir(exist_ok=False)
    (run / 'raw').mkdir()
    original_get = survey.requests.get
    def get(url, **kwargs):
        kwargs['timeout'] = (8, 10)
        return original_get(url, **kwargs)
    survey.requests.get = get
    ids = json.loads((survey.HERE / 'probe_01/sina_december_put_ids.json').read_text(encoding='utf-8'))['summary']['listed_security_ids']
    specs = [('sina_literal_batch_' + str(i // 6), 'Sina',
              'https://hq.sinajs.cn/list=' + ','.join('CON_OP_' + sid for sid in ids[i:i + 6]), None)
             for i in range(0, len(ids), 6)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = [f.result() for f in as_completed([pool.submit(survey.probe, run, s) for s in specs])]
    survey.save(run / 'receipt.json', {'rows': rows, 'requested_ids': ids,
        'script_sha256': survey.sha(Path(__file__)), 'warning': 'All payloads still require content validation.'})


if __name__ == '__main__':
    main()
