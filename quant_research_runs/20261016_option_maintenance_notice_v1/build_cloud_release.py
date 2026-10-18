from pathlib import Path
import json

HERE = Path(__file__).resolve().parent
paths = ['scripts/build_ic_im_v1_4_digest.py', 'scripts/ic_im_option_maintenance_notice.py',
         'tests/test_ic_im_v1_4_monthly_maintenance_notice.py', 'docs/ic-im-monthly-put-notice-20261016.md']
elements = [dict(path=p, mode='100644', type='blob', content=(HERE/'cloud'/p).read_text(encoding='utf-8')) for p in paths]
(HERE/'cloud_release.json').write_text(json.dumps(elements, ensure_ascii=False), encoding='utf-8')
print('Prepared 4 reporting-only files; strategy pin, execution logic, ledgers and workflow unchanged.')
