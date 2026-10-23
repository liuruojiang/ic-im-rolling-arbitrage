from pathlib import Path
from datetime import date, datetime
import hashlib,json,subprocess,sys
ROOT=Path(__file__).resolve().parents[3]
D=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import ic_im_signal_evidence_v1 as evidence
pin=D/'capture_source_pin.json'
launch=json.loads((D/'launch_receipt.json').read_text(encoding='utf-8-sig'))
pid=int(launch['pid'])
check=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',f'Get-Process -Id {pid} -ErrorAction Stop | Select-Object Id,ProcessName,StartTime,CPU | ConvertTo-Json'],capture_output=True,encoding='utf-8')
assert check.returncode==0,check.stderr
process=json.loads(check.stdout)
assert process['ProcessName']=='python' and process['Id']==pid
blocked=subprocess.run([sys.executable,'-X','utf8','-B',str(ROOT/'capture_ic_im_v1_4_inputs.py'),'--once','--state-dir',launch['state_read_only'],'--evidence-dir',launch['evidence_dir'],'--source-pin',str(pin)],capture_output=True,encoding='utf-8')
assert blocked.returncode==3 and json.loads(blocked.stdout)['status']=='already_running', (blocked.returncode,blocked.stdout,blocked.stderr)
folder=Path(launch['evidence_dir'])/'capture_receipts'
files=sorted(folder.rglob('*.json'))
assert files, 'first real receipt still pending'
latest=files[-1]; receipt=json.loads(latest.read_text(encoding='utf-8-sig'))
assert receipt['pid']==pid and receipt['ledger_paths_and_bytes_unchanged']
assert receipt['source_pin_sha256']==hashlib.sha256(pin.read_bytes()).hexdigest()
products=receipt.get('capture',{}).get('products',{})
assert products.get('IC',{}).get('status')=='captured', 'IC deployment capture incomplete'
verified_packages={}
for product,value in products.items():
    path=Path(value['manifest']); manifest=json.loads(path.read_text(encoding='utf-8'))
    canonical=json.dumps(manifest['body'],ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
    assert hashlib.sha256(canonical).hexdigest()==manifest['sha256']==value['sha256']
    if value['status']=='captured':
        reader=evidence._load_one(Path(launch['evidence_dir']),path,date.fromisoformat(manifest['body']['day']))
        assert reader.manifest_sha256==value['sha256'] and reader.metadata['product']==product
        verified_packages[product]={'typed_inputs':len(reader.context['entries']),'raw_responses':len(reader.context['raw']),'all_blobs_verified':True}
prior=[json.loads(f.read_text(encoding='utf-8-sig')) for f in files]
previous=[r for r in prior if r.get('pid')==pid and r.get('status')=='incomplete']
retry_verified=bool(previous and receipt['status']=='captured' and (datetime.fromisoformat(receipt['started_at'])-datetime.fromisoformat(previous[-1]['finished_at'])).total_seconds()>=300)
result={'verified_packages':verified_packages,'capture_rounds':len(files),'automatic_five_minute_retry_verified':retry_verified,'checked_at':datetime.now().astimezone().isoformat(),'status':'RUNNING_CAPTURE_COMPLETE' if products and all(x.get('status')=='captured' for x in products.values()) else 'RUNNING_IC_COMPLETE_IM_RETRYING','process':process,'source_pin_sha256':receipt['source_pin_sha256'],'latest_receipt':str(latest),'receipt_sha256':hashlib.sha256(latest.read_bytes()).hexdigest(),'capture_status':receipt['status'],'products':{k:v['status'] for k,v in products.items()},'duplicate_collector':{'exit_code':blocked.returncode,'result':json.loads(blocked.stdout)},'formal_ledger_unchanged':True,'restart_autostart_established':False,'remote_poe_deployment':False,'environment_file':str(D/'collector_environment.env')}
(D/'deployment_acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
