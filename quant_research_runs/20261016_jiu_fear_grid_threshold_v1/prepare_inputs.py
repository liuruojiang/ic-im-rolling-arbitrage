"""Decode a lossless extract of the observed origin response; no network or fills."""
import base64
import csv
import hashlib
import json
import struct
import zlib
from datetime import date, timedelta, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
packed = base64.b64decode((HERE / 'inputs/jiu_history_u16be.b64').read_text().strip(), validate=True)
assert len(packed) == 6008 and zlib.crc32(packed) == 1227626056
rows = [{'date': (date(2020,1,1) + timedelta(days=d)).isoformat(), 'fear_greed_index': v/100}
        for d,v in struct.iter_unpack('>HH', packed)]
assert len(rows) == 1502
assert rows == sorted(rows, key=lambda r:r['date'])
assert len({r['date'] for r in rows}) == len(rows)
assert all(0 <= r['fear_greed_index'] <= 100 for r in rows)
assert rows[0] == {'date':'2020-08-03','fear_greed_index':65.44}
assert rows[-1] == {'date':'2026-10-15','fear_greed_index':2.28}
csv_path = HERE/'inputs/jiu_fear_greed.csv'
with csv_path.open('x', newline='', encoding='utf-8') as f:
    writer=csv.DictWriter(f, fieldnames=['date','fear_greed_index']);writer.writeheader();writer.writerows(rows)
extraction = HERE/'inputs/jiu_history_extracted.json'
extraction.write_text(json.dumps(rows,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
spec=HERE/'preregistered_spec.md'
(HERE/'preregistered_spec.md.sha256').write_text(hashlib.sha256(spec.read_bytes()).hexdigest()+'\n',encoding='ascii')
meta={'source_url':'https://ai2.jiucaishuo.com/api/fear-greed','page_url':'https://jiucaishuo.com/indicators?tab=fg',
      'method':'observed Network.responseReceived + Network.getResponseBody; lossless date/value extract',
      'extraction_timestamp':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
      'response_character_count':139451,'full_response_archived':False,'rows':len(rows),
      'first':rows[0],'last':rows[-1],'transport_crc32':1227626056,
      'point_in_time_certified':False,'decimal_precision':2,
      'current_factor_names':['50ETF期权波动率','股债回报差','沪深300股指期货升贴水','两市成交额','创新高个股占比'],
      'sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [csv_path,extraction,spec]},
      'scope':'research_only; extracted history is not the complete raw HTTP response'}
(HERE/'inputs/source_manifest.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(meta,ensure_ascii=False,indent=2))
