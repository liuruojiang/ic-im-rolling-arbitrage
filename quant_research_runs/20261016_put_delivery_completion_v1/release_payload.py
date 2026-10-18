from pathlib import Path
import json
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STRATEGY_FILES = [
    'ic_im_put_execution_v1.py', 'ic_im_option_maintenance_notice.py', 'ic_im_v1_4_policy.py',
    'poe_ic_im_mainline_v1_4_bot.py', 'poe_ic_im_v1_4_state.py', 'run_ic_im_v1_4_github_digest.py',
    'test_ic_im_put_execution_repairs_20261016.py', 'test_ic_im_option_maintenance_notice_20261016.py',
    'test_ic_im_put_delivery_completion_20261016.py', 'docs/ic_im_put_execution_repairs_20261016_v1_spec.md',
    'docs/ic_im_put_execution_repairs_20261016_v1_spec.md.sha256',
    'docs/ic_im_put_delivery_completion_20261016_v1_spec.md',
    'docs/ic_im_put_delivery_completion_20261016_v1_spec.md.sha256',
    'docs/ic_im_put_delivery_completion_20261016.md', 'notes/mainline_registry.md',
]
kind = sys.argv[1]
paths = STRATEGY_FILES if kind == 'strategy' else json.loads((HERE/'cloud/publish_paths.json').read_text())
base = ROOT if kind == 'strategy' else HERE/'cloud'
if len(sys.argv) == 2:
    print(json.dumps(list(paths)))
else:
    p = list(paths)[int(sys.argv[2])]
    print(json.dumps(dict(path=p, content=(base/p).read_text(encoding='utf-8')), ensure_ascii=False))
