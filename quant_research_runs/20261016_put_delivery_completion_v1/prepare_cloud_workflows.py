from pathlib import Path
import json
import sys

HERE = Path(__file__).resolve().parent
cloud = HERE/'cloud'
sha = sys.argv[1]
assert len(sha)==40 and all(c in '0123456789abcdef' for c in sha)
daily=cloud/'.github/workflows/ic-im-v1-4-daily-digest.yml'
text=daily.read_text(encoding='utf-8')
assert text.count('435bbfdcf1f412f2542b3d5dbfeb90404b0e7261') >= 1
daily.write_text(text.replace('435bbfdcf1f412f2542b3d5dbfeb90404b0e7261',sha),encoding='utf-8')
gate=cloud/'.github/workflows/delivery-regression.yml'
text=gate.read_text(encoding='utf-8')
needle='            --junitxml=regression.xml'
assert needle in text
text=text.replace(needle,'            test_ic_im_put_execution_repairs_20261016.py test_ic_im_option_maintenance_notice_20261016.py \\\n            test_ic_im_put_delivery_completion_20261016.py \\\n'+needle)
gate.write_text(text,encoding='utf-8')
ready=cloud/'.github/workflows/ic-im-v1-4-delivery-readiness.yml'
text=ready.read_text(encoding='utf-8')
text=text.replace('  workflow_dispatch:\n','''  workflow_dispatch:
    inputs:
      publication_mode:
        type: choice
        options: [close, realtime]
        default: close
  push:
    branches: [main]
    paths: [docs/icim-readiness-requests/20261016-put-delivery-completion.json]
''',1)
needle='      - name: Resolve the exact production strategy pin'
step='''      - name: Resolve the bounded no-send validation request
        id: request
        env:
          INPUT_MODE: ${{ inputs.publication_mode }}
        run: |
          python - <<'PY'
          import json, os
          from datetime import datetime
          from pathlib import Path
          from zoneinfo import ZoneInfo
          export = False
          if os.environ['GITHUB_EVENT_NAME'] == 'push':
              req = json.loads(Path('automation/docs/icim-readiness-requests/20261016-put-delivery-completion.json').read_text())
              assert req['date'] == datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
              assert req['scope'] == 'verified_research_handoff_no_send'
              mode, export = req['mode'], req.get('export_handoff') is True
          else:
              mode = os.environ.get('INPUT_MODE') or 'close'
          assert mode in {'close','realtime'}
          with open(os.environ['GITHUB_OUTPUT'],'a') as f:
              f.write(f'mode={mode}\\nexport_handoff={str(export).lower()}\\n')
          PY
'''
assert needle in text
text=text.replace(needle,step+needle,1)
text=text.replace('      - name: Restore a verified production ledger to an isolated volume\n','      - name: Restore a verified production ledger to an isolated volume\n        id: restore\n',1)
text=text.replace("          clock = datetime.now(ZoneInfo('Asia/Shanghai'))", "          clock = datetime.now(ZoneInfo('Asia/Shanghai'))\n          mode = '${{ steps.request.outputs.mode }}'\n          expected_market = clock.date() if mode == 'realtime' else bot._latest_completed_exchange_day(clock)\n          if mode == 'realtime':\n              assert bot._market_phase(clock) == '盘中'")
text=text.replace("              env.write('COMPLETED_DAY=' + completed.isoformat() + '\\n')", "              env.write('COMPLETED_DAY=' + completed.isoformat() + '\\n')\n              env.write('EXPECTED_MARKET_DAY=' + expected_market.isoformat() + '\\n')")
text=text.replace('--out-dir readiness-artifacts --mode close \\', '--out-dir readiness-artifacts --mode "${{ steps.request.outputs.mode }}" \\')
text=text.replace('--expected-market-date "$COMPLETED_DAY"','--expected-market-date "$EXPECTED_MARKET_DAY"')
needle='      - uses: actions/upload-artifact@v7\n'
step='''      - name: Export verified research handoff when explicitly requested
        if: steps.request.outputs.export_handoff == 'true'
        env:
          ICIM_STATE_DIR: readiness-state
          ICIM_REQUIRE_MIGRATION: '1'
        run: >-
          python automation/scripts/ic_im_readiness_handoff.py
          --strategy-root strategy --strategy-sha "${{ steps.pin.outputs.sha }}"
          --source-artifact "${{ steps.restore.outputs.artifact_id }}"
          --state-dir readiness-state --artifact-dir readiness-artifacts
'''
assert needle in text
text=text.replace(needle,step+needle,1)
ready.write_text(text,encoding='utf-8')
req=cloud/'docs/icim-readiness-requests/20261016-put-delivery-completion.json'
req.parent.mkdir(parents=True,exist_ok=True)
req.write_text(json.dumps(dict(date='2026-10-16',mode='close',scope='verified_research_handoff_no_send',export_handoff=True),indent=2)+'\n',encoding='utf-8')
manifest=json.loads((cloud/'source_manifest.json').read_text(encoding='utf-8'))
manifest['publish_files'].update({str(p.relative_to(cloud)).replace('\\','/'):None for p in [daily,gate,ready,req,cloud/'scripts/ic_im_readiness_handoff.py']})
(cloud/'publish_paths.json').write_text(json.dumps(list(manifest['publish_files']),indent=2),encoding='utf-8')
print(json.dumps(dict(strategy_sha=sha,publish_paths=list(manifest['publish_files']))))
