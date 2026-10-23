"""Keep the accepted 10-22 original manifests and their verified blobs intact."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
RUN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import ic_im_signal_evidence_v1 as evidence

original = ROOT / 'data/icim_v14_original_inputs'
selected = json.loads((ROOT / 'quant_research_runs/20261022_evidence_http_date_v1/real_recovery_01/receipt.json').read_text(encoding='utf-8'))['selected_packages']
target = RUN / 'selected_original_bundle'
target.mkdir(exist_ok=False)
items = []
for product, value in selected.items():
    path = Path(value['manifest'])
    packet = json.loads(path.read_text(encoding='utf-8'))
    body = packet['body']
    assert hashlib.sha256(evidence._dumps(body)).hexdigest() == value['sha256'] == packet['sha256']
    dest = target / path.relative_to(original)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, dest)
    for row in [*body['entries'].values(), *body['raw']]:
        evidence._verified_blob(original, row)
        source = evidence._blob_path(original, row)
        dest = target / source.relative_to(original)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copyfile(source, dest)
        assert hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(dest.read_bytes()).digest()
    items.append({'product': product, 'sha256': packet['sha256'], 'manifest': str(path)})
(RUN / 'delivery/selected_bundle.json').write_text(json.dumps(items, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(items, ensure_ascii=False))
