"""Recover the exact originally imported policy without touching concurrent work."""
import hashlib,json,subprocess,shutil
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
p=ROOT/'ic_im_v1_4_policy.py'
guard=HERE/'outputs/start_source_hashes.json'
hashes=json.loads(guard.read_text(encoding='utf-8'))
expected=hashes[str(p)]
raw=subprocess.check_output(['git','-c',f'safe.directory={ROOT.as_posix()}','show','HEAD:ic_im_v1_4_policy.py'],cwd=ROOT)
variants=[raw,raw.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')]
matches=[v for v in variants if hashlib.sha256(v).hexdigest()==expected]
assert matches,'Cannot recover original loaded policy exactly'
dest=HERE/'inputs/source_backups/ic_im_v1_4_policy.py';dest.parent.mkdir(exist_ok=True)
dest.write_bytes(matches[0])
shutil.copy2(guard,HERE/'outputs/first_pass_start_source_hashes.json')
evidence={'path':str(p),'first_pass_imported_sha256':expected,'current_disk_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'frozen_copy':str(dest),'recovered_from':'git HEAD exact original byte hash; policy left untouched','first_pass_policy_in_memory':'original imported module; no reload','resolution':'Preload exact frozen module for resumed validation and timing replay'}
(HERE/'outputs/concurrent_dependency_change.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(evidence,ensure_ascii=False))
