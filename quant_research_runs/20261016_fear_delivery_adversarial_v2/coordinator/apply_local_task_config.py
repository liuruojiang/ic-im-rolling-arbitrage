"""Apply only the reviewed ICIM task prompt, with an exact before-hash lease."""
from pathlib import Path
import hashlib,json,os,re,time,tomllib
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
TARGET=Path("D:/Codex/home/automations/ic-im/automation.toml")
PROPOSAL=HERE.parent/"local_delivery/automation.proposed.toml"
EXPECTED="b7417e9746e7a8202284a3823e22ca671cd24a2bb950da5905fd309f7bd24e43"
raw=TARGET.read_bytes()
assert hashlib.sha256(raw).hexdigest()==EXPECTED,"task changed; do not overwrite"
old=tomllib.loads(raw.decode("utf-8"));proposed=PROPOSAL.read_text(encoding="utf-8");new=tomllib.loads(proposed)
assert new["prompt"].startswith(old["prompt"])
for key,value in old.items():
 if key not in {"prompt","updated_at"}:assert new[key]==value,key
for term in ["test_ic_im_fear_delivery_adversarial_20261016.py","baifenwei_either_source_20261016_v1"]:
 assert term in new["prompt"],term
proposed=re.sub(r"^updated_at = \d+$","updated_at = "+str(int(time.time()*1000)),proposed,flags=re.M)
verified=tomllib.loads(proposed)
backup=HERE/"automation.before_apply.toml"
if backup.exists():assert backup.read_bytes()==raw
else:backup.write_bytes(raw)
staging=TARGET.with_name("automation.toml.icim-20261016.tmp")
assert not staging.exists()
with staging.open("xb") as stream:stream.write(proposed.encode("utf-8"))
assert tomllib.loads(staging.read_text(encoding="utf-8"))==verified
os.replace(staging,TARGET)
assert tomllib.loads(TARGET.read_text(encoding="utf-8"))==verified
(HERE/"local_task_applied.json").write_bytes(json.dumps(dict(target=str(TARGET),before_sha256=EXPECTED,after_sha256=hashlib.sha256(TARGET.read_bytes()).hexdigest(),original_prompt_preserved=True,other_fields_preserved=True,applied=True),indent=2).encode())
print("Reviewed ICIM task prompt applied; schedule and other fields preserved")
