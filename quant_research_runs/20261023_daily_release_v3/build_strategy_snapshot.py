from pathlib import Path
import hashlib,json,subprocess
ROOT=Path(__file__).resolve().parents[2]
RUN=Path(__file__).resolve().parent
DEST=RUN/'strategy_checkout'
meta=json.loads((RUN/'publication_snapshot.json').read_text('utf-8'))
trees=json.loads((RUN/'snapshot_git_trees.json').read_text('utf-8'))
DEST.mkdir(exist_ok=False)
subprocess.run(['git','init',str(DEST)],check=True,capture_output=True)
def git(*args,data=None):
    return subprocess.check_output(['git','-C',str(DEST),*args],input=data).decode().strip()
def object_hash(data,kind='blob'):
    return hashlib.sha1(kind.encode()+b' '+str(len(data)).encode()+b'\0'+data).hexdigest()
def put(path,data,expected):
    assert object_hash(data)==expected,path
    target=DEST/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    assert git('hash-object','-w','--stdin',data=data)==expected
missing=[]
for entry in trees[0]['tree']:
    if entry['type']!='blob':continue
    p=ROOT/entry['path'];data=p.read_bytes() if p.is_file() else b''
    if object_hash(data)!=entry['sha']:
        proc=subprocess.run(['git','cat-file','blob',entry['sha']],cwd=ROOT,capture_output=True)
        if proc.returncode:missing.append(entry);continue
        data=proc.stdout
    put(entry['path'],data,entry['sha'])
assert not missing,missing
remote=json.loads((RUN/'strategy_remote_blobs.json').read_text('utf-8'))
for path in meta['paths']:
    data=(ROOT/path).read_bytes()
    expected=meta['uploaded'].get(path) or remote[path]
    put(path,data,expected)
for tree in trees:
    entries=sorted(tree['tree'],key=lambda x:(x['path']+('/' if x['type']=='tree' else '')).encode())
    raw=b''.join((('40000' if e['type']=='tree' else e['mode'])+' '+e['path']).encode()+b'\0'+bytes.fromhex(e['sha']) for e in entries)
    assert object_hash(raw,'tree')==tree['sha']
    assert git('hash-object','-t','tree','-w','--stdin',data=raw)==tree['sha']
raw=(RUN/'candidate_commit_verified.bin').read_bytes()
assert git('hash-object','-t','commit','-w','--stdin',data=raw)==meta['candidate']['sha']
git('update-ref','refs/heads/main',meta['candidate']['sha'])
assert git('rev-parse','HEAD')==meta['candidate']['sha']
manifest='docs/ic_im_daily_release_20261023_repairs_v3_r3_manifest.json'
assert subprocess.check_output(['git','-C',str(DEST),'show','HEAD:'+manifest])==(DEST/manifest).read_bytes()
receipt={'strategy_sha':meta['candidate']['sha'],'kind':'immutable production/test filesystem snapshot; partial Git object graph, not a complete repository clone','root_blobs':sum(x['type']=='blob' for x in trees[0]['tree']),'release_paths':len(meta['paths']),'manifest_from_commit_verified':True}
(RUN/'strategy_snapshot_receipt.json').write_text(json.dumps(receipt,indent=2),'utf-8')
print(json.dumps(receipt))
