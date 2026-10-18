"""Pin the originally imported policy while leaving other workspace changes alone."""
import importlib.util,runpy,sys,json,hashlib
from pathlib import Path
HERE=Path(__file__).resolve().parent
p=HERE/'inputs/source_backups/ic_im_v1_4_policy.py'
ev=json.loads((HERE/'outputs/concurrent_dependency_change.json').read_text(encoding='utf-8'))
assert hashlib.sha256(p.read_bytes()).hexdigest()==ev['first_pass_imported_sha256']
sys.path.insert(0,str(HERE.parents[1]));sys.path.insert(0,str(HERE))
spec=importlib.util.spec_from_file_location('ic_im_v1_4_policy',p)
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
target='run_timing_v2.py' if '--timing' in sys.argv else 'run_study.py'
sys.argv=[str(HERE/target)]
runpy.run_path(str(HERE/target),run_name='__main__')
