from pathlib import Path
import re
import json

source = (Path(__file__).parent / "raw/sina_quotes_literal.bin").read_bytes().decode("gbk")
result = []
for security, payload in re.findall(r'hq_str_CON_OP_(\d+)="([^"]*)"', source):
    fields = payload.split(",")
    result.append(dict(security_id=security, field_count=len(fields), fields={
        str(k):fields[k] for k in [1,2,3,7,8,32,35,36,37,39,40,41,43,44,45,46,47,48,49,50]}))
print(json.dumps(result, ensure_ascii=False, indent=2))
