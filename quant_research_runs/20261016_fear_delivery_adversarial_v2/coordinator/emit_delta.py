import difflib,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
p=sys.argv[1]
new=(ROOT/p).read_text(encoding="utf-8").replace("\r\n","\n")
backup=HERE/"backups"/Path(p).name
if backup.exists():
 old=backup.read_text(encoding="utf-8").replace("\r\n","\n")
 a,b=old.splitlines(keepends=True),new.splitlines(keepends=True)
 changes=[dict(a=i,b=j,old=a[i:j],new=b[k:l]) for op,i,j,k,l in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes() if op!="equal"]
else:changes=None
raw=new.encode()
print(json.dumps(dict(path=p,changes=changes,content=new if changes is None else None,
 git_sha=hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()),ensure_ascii=False))
