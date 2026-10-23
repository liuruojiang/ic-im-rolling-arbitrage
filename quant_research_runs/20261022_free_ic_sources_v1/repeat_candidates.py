"""Repeat promising public routes with ordinary longer timeouts, then small batches."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
from pathlib import Path

import survey


def main():
    run = survey.HERE/'probe_02'
    run.mkdir(exist_ok=False)
    (run/'raw').mkdir()
    original_get = survey.requests.get
    def get(url,**kwargs):
        kwargs['timeout'] = (8,10)
        return original_get(url,**kwargs)
    survey.requests.get = get
    names = {'em_option_list','em_core_quote','em_etf_quote','sse_master_https','tencent_etf','sina_etf','sina_listed_months','sse_chain_primary'}
    chosen = [s for s in survey.specs(datetime.now(survey.BEIJING).date()) if s[0] in names]
    before = {p.relative_to(survey.ROOT).as_posix():survey.sha(p) for p in (survey.ROOT/'runtime').rglob('*') if p.is_file()}
    survey.save(run/'baseline.json',{'runtime':before,'timeout':[8,10], 'workers':2,
        'source_sha256':survey.sha(Path(__file__)),'survey_source_sha256':survey.sha(Path(survey.__file__))})
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = [future.result() for future in as_completed([pool.submit(survey.probe,run,s) for s in chosen])]
    # Batch the actual IDs observed in the original listing, never invent a code.
    listed = json.loads((survey.HERE/'probe_01/sina_december_put_ids.json').read_text(encoding='utf-8'))['summary']['listed_security_ids']
    batches = [listed[i:i+6] for i in range(0,len(listed),6)]
    specs = [('sina_dec_put_batch_'+str(i),'Sina','https://hq.sinajs.cn/',
              {'list':','.join('CON_OP_'+sid for sid in group)}) for i,group in enumerate(batches)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows += [future.result() for future in as_completed([pool.submit(survey.probe,run,s) for s in specs])]
    after = {p.relative_to(survey.ROOT).as_posix():survey.sha(p) for p in (survey.ROOT/'runtime').rglob('*') if p.is_file()}
    assert before==after
    survey.save(run/'receipt.json',{'rows':rows,'formal_runtime_unchanged':True,
                                  'tested_ids':listed,'batch_sizes':list(map(len,batches)),
                                  'timeout':[8,10],'warning':'Quotes and identity/timestamps still need validation.'})


if __name__=='__main__':
    main()
