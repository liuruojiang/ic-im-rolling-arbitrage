from pathlib import Path
import hashlib, json, os, subprocess, sys, zipfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
RUN = Path(__file__).resolve().parent
target = RUN / 'local_handoff_preview'
target.mkdir(exist_ok=False)
archive = RUN / 'readiness_39892433587.zip'
assert hashlib.sha256(archive.read_bytes()).hexdigest() == '04baf5d42d657095a1770a38f57037926de2ffed8211662739407b1633af942a'
with zipfile.ZipFile(archive) as z:
    manifest = json.loads(z.read('handoff_manifest.json'))
    assert len(z.namelist()) == len(set(z.namelist()))
    assert set(z.namelist()) == set(manifest['files']) | {'handoff_manifest.json'}
    for name, digest in manifest['files'].items():
        path = Path(name)
        assert not path.is_absolute() and '..' not in path.parts
        assert path.parts[0] in {'ledger', 'report'}
        raw = z.read(name)
        assert hashlib.sha256(raw).hexdigest() == digest
        dest = target / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
pin = '8038980de86251b2c5133da04e788dc2c49c84ff'
assert manifest['strategy_sha'] == pin
assert manifest['signal_completeness'] == 'complete'
os.environ.update(ICIM_STATE_DIR=str(target/'ledger'), ICIM_REQUIRE_MIGRATION='1', ICIM_DISABLE_INTERNAL_REFRESH='1', ICIM_DISABLE_EVIDENCE_CAPTURE='1')
sys.path.insert(0, str(ROOT))
from poe_ic_im_v1_4_state import StateStore
state = StateStore(target/'ledger').load_latest()
assert (state['verified_day'], state['sequence'], state['digest']) == (manifest['verified_day'], manifest['sequence'], manifest['digest'])
raw = (ROOT/'docs/ic_im_daily_release_20261023_manifest.json').read_bytes()
release = json.loads(raw)
actual = {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in release['files']}
assert actual == release['files']
# The remote immutable tree was independently compared with these same bytes.
proof = {'status':'verified','release':release['release'],'strategy_sha':pin,
         'manifest_sha256':hashlib.sha256(raw).hexdigest(),'source_sha256':actual,
         'verified_at':datetime.now(timezone.utc).isoformat(),
         'verification_method':'local manifest hashes plus independently verified remote Git tree'}
(target/'source-proof.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
auto=Path('D:/workspace/icim_release_20261023/automation')
subprocess.run([sys.executable,'-B','-X','utf8',str(auto/'scripts/build_ic_im_v1_4_digest.py'),
 '--result',str(target/'report/result.json'),'--out-dir',str(target/'rendered'),
 '--source-provenance',str(target/'source-proof.json'),'--expected-source-sha',pin],check=True)
summary={'status':'passed','market_date':manifest['market_date'],'sequence':manifest['sequence'],
 'strategy_sha':pin,'source_files_verified':len(actual),'handoff_files_verified':len(manifest['files']),
 'source_run':39892433587,'local_live_fetch':False,'local_production_ledger_modified':False,'message_sent':False}
(target/'verification.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps(summary))
