"""Read-only audit snapshot; production and frozen files never modified."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import json

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
guard = json.loads((ROOT/'quant_research_runs/20261016_put_execution_repairs_v1/backup_manifest.json').read_text(encoding='utf-8'))['guarded']
current = {key:sha(ROOT/key) for key in guard}
sources = ['ic_im_put_execution_v1.py', 'ic_im_option_maintenance_notice.py', 'ic_im_v1_4_policy.py',
           'poe_ic_im_mainline_v1_4_bot.py', 'poe_ic_im_v1_4_state.py', 'run_ic_im_v1_4_github_digest.py',
           'notes/mainline_registry.md']
snap = dict(at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
            protected=current, changed_since_first_repair=[p for p in guard if guard[p]!=current[p]],
            sources={p:sha(ROOT/p) for p in sources})
dest = HERE/'baseline_snapshot.json'
if dest.exists():
    before=json.loads(dest.read_text(encoding='utf-8'))
    snap['changed_during_audit']=[p for p in before['protected'] if before['protected'][p]!=current[p]]
    snap['sources_changed_during_audit']=[p for p in sources if before['sources'][p]!=snap['sources'][p]]
    dest=HERE/'final_snapshot.json'
dest.write_text(json.dumps(snap,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in snap.items() if k not in {'protected','sources'}},ensure_ascii=False))
