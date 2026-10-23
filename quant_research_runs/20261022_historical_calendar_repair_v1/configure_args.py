"""Create new argument manifests; retain previous-stage configurations."""
from pathlib import Path
import json

HERE = Path(__file__).resolve().parent
new = ['test_icim_historical_calendar_20261022.py', 'test_icim_historical_replay_adapter_20261022.py']
for name, value in [('adapter_args.json', new),
                    ('final_args.json', json.loads((HERE / 'prior_args.json').read_text(encoding='utf-8')) + new)]:
    path = HERE / name
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('ARGS_WRITTEN', flush=True)
