import copy, hashlib, importlib.util, json, sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest

ROOT=Path(__file__).resolve().parents[2]
CLOUD=ROOT/"20261016_fear_single_source_delivery_v1"/"cloud"
# ROOT here is quant_research_runs; all temporary writes stay beside this file.
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(CLOUD/"scripts"))
spec=importlib.util.spec_from_file_location("fearfixtures",CLOUD/"tests/test_ic_im_v1_4_fear_sources.py")
fixture=importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
import build_ic_im_v1_4_digest as digest
import ic_im_v1_4_delivery_contract as contract
import prepare_ic_im_v1_4_marker as marker
import check_ic_im_v1_4_delivery as gate
spec2=importlib.util.spec_from_file_location("resultday",OUT/"result_day_gate_remote_snapshot.py")
resultday=importlib.util.module_from_spec(spec2); spec2.loader.exec_module(resultday)
mp=pytest.MonkeyPatch()
results=[]
def probe(name, data, expected_reject=False):
    try:
        digest.validate_success_payload(data)
        _,plain,_=digest.build_success(data,"","")
        html=digest.build_success_html(data,"")
        entry=dict(case=name,accepted=True,grid=data["signals"]["IC"]["grid_action"],
                   status=data["status"],subject=digest.build_success(data,"","")[0],
                   has_unconfirmed_warning=("网格及合计目标未确认" in plain or "网格未确认" in plain))
        (OUT/(name+".txt")).write_text(plain,encoding="utf8")
        (OUT/(name+".html")).write_text(html,encoding="utf8")
    except Exception as exc:
        entry=dict(case=name,accepted=False,error=type(exc).__name__+": "+str(exc))
    entry["expected_reject"]=expected_reject
    results.append(entry)

for source in ("csv","page"):
    probe(source+"_only",fixture.payload(mp,source=source))
partial=fixture.payload(mp,missing=True)
probe("honest_partial",partial)
forged=copy.deepcopy(partial)
forged.update(status="ok",signal_completeness="complete",incomplete_components=[])
for s in forged["signals"].values():
    for f in ("grid_target","grid_action","grid_entry_source_target","grid_transition_reason","total_units_target","total_units_change"):
        s[f]=s["reference_"+f]
    s.update(signal_completeness="complete",incomplete_components=[])
probe("missing_fear_disguised_complete",forged,expected_reject=True)
for label,field,value in (("leaked_grid_source","grid_entry_source_target","none"),
                          ("leaked_grid_reason","grid_transition_reason","hold"),
                          ("leaked_total_change","total_units_change",0)):
    changed=copy.deepcopy(partial)
    changed["signals"]["IC"][field]=value
    probe(label,changed,expected_reject=True)
changed=copy.deepcopy(partial)
changed["incomplete_components"]=["IC.grid"]
probe("missing_product_in_manifest",changed,expected_reject=True)
for label,data in (("marker_disguised_complete",forged),("marker_honest_partial",partial)):
    try:
        name=marker.marker_name(data)
        results.append(dict(case=label,accepted=True,marker=name))
    except Exception as exc:
        results.append(dict(case=label,accepted=False,error=type(exc).__name__+": "+str(exc)))
# Demonstrate that a completed partial marker blocks an automatic later complete digest on the same actual day.
complete=fixture.payload(mp)
artifacts={"artifacts":[{"name":marker.marker_name(partial),"expired":False}]}
results.append(dict(case="partial_then_complete_dedupe",partial_marker=marker.marker_name(partial),
                    complete_marker=marker.marker_name(complete),
                    should_send_complete=resultday.should_send_for_result(complete,artifacts,correction=False),
                    should_send_explicit_correction=resultday.should_send_for_result(complete,artifacts,correction=True)))
# Verify mirrored production files against the immutable remote Git tree.
remote=json.loads((OUT/"remote_readonly_evidence.json").read_text(encoding="utf8"))
tree={item["path"]:item.get("sha") for item in remote["automation"]["tree"] if item["type"]=="blob"}
comparison=[]
for path in CLOUD.rglob("*"):
    if not path.is_file() or "__pycache__" in str(path): continue
    rel=path.relative_to(CLOUD).as_posix()
    if rel not in tree:continue
    raw=path.read_bytes()
    got=hashlib.sha1(("blob "+str(len(raw))+"\0").encode()+raw).hexdigest()
    normalized=raw.replace(b'\r\n',b'\n')
    normalized_git_blob=hashlib.sha1(("blob "+str(len(normalized))+"\0").encode()+normalized).hexdigest()
    trimmed=normalized.rstrip(b'\n')+b'\n'
    trimmed_git_blob=hashlib.sha1(("blob "+str(len(trimmed))+"\0").encode()+trimmed).hexdigest()
    comparison.append(dict(path=rel,match=got==tree[rel],normalized_match=normalized_git_blob==tree[rel],normalized_trailing_blank_match=trimmed_git_blob==tree[rel],local_git_blob=got,remote_git_blob=tree[rel]))
results.append(dict(case="remote_mirror_file_integrity",files=comparison))
(OUT/"results.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf8")
print(json.dumps(results,ensure_ascii=False,indent=2))

