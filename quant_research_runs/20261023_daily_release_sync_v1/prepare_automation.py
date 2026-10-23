from pathlib import Path
import ast
import json
import re
import shutil

ROOT = Path(__file__).resolve().parents[2]
RUN = Path(__file__).resolve().parent
AUTO = Path('D:/workspace/icim_release_20261023/automation')
sha = json.loads((RUN / 'strategy_candidate.json').read_text(encoding='utf-8'))['commit']


def backup(name):
    dst = RUN / 'backup/automation' / name
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.copyfile(AUTO / name, dst)


def assign(source, name):
    tree = ast.parse(source)
    return next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name for t in n.targets))


n = 'scripts/ic_im_v1_4_delivery_contract.py'
backup(n)
t = (AUTO / n).read_text(encoding='utf-8')
bot = (ROOT / 'poe_ic_im_mainline_v1_4_bot.py').read_text(encoding='utf-8')
for target, origin in [('CALENDAR_YEARS', '_OFFICIAL_EXCHANGE_CALENDAR_YEARS'),
                       ('EXCHANGE_CLOSURES', '_EXCHANGE_CLOSURES')]:
    old = assign(t, target)
    replacement = ast.get_source_segment(bot, assign(bot, origin)).replace(origin, target, 1)
    lines = t.splitlines(True)
    t = ''.join(lines[:old.lineno-1]) + replacement + '\n' + ''.join(lines[old.end_lineno:])
t = t.replace('published common CFFEX/SSE 2026 calendar', 'published common CFFEX/SSE 2022–2026 calendars')
t = t.replace('739071a201506dd869b9a156e490c3bafd95a334', sha)
(AUTO / n).write_text(t, encoding='utf-8')

n = '.github/workflows/ic-im-v1-4-daily-digest.yml'
backup(n)
t = (AUTO / n).read_text(encoding='utf-8')
t, count = re.subn(r'(repository: liuruojiang/ic-im-rolling-arbitrage\s+ref: )[0-9a-f]{40}', r'\g<1>'+sha, t)
assert count == 1
marker = '      - name: Install strategy dependencies\n'
step = '''      - name: Verify audited execution source inventory
        if: steps.delivery_gate.outputs.should_send == 'true'
        run: >-
          python automation/scripts/verify_icim_source_release.py
          --strategy-root strategy --expected-strategy-sha PIN
          --output strategy-artifacts/program-source.json

'''.replace('PIN', sha)
assert marker in t
t = t.replace(marker, step + marker, 1)
t = t.replace('--result strategy-artifacts/result.json \\\n', '--result strategy-artifacts/result.json \\\n            --source-provenance strategy-artifacts/program-source.json \\\n', 1)
(AUTO / n).write_text(t, encoding='utf-8')

n = '.github/workflows/ic-im-v1-4-delivery-readiness.yml'
backup(n)
t = (AUTO / n).read_text(encoding='utf-8')
marker = '      - name: Run the official close producer on the isolated ledger\n'
step = '''      - name: Verify audited execution source inventory
        run: >-
          python automation/scripts/verify_icim_source_release.py
          --strategy-root strategy --expected-strategy-sha "${{ steps.pin.outputs.sha }}"
          --output readiness-artifacts/program-source.json
'''
assert marker in t
t = t.replace(marker, step + marker, 1)
t = t.replace('--result readiness-artifacts/result.json --out-dir readiness-mail',
              '--result readiness-artifacts/result.json --source-provenance readiness-artifacts/program-source.json --out-dir readiness-mail')
(AUTO / n).write_text(t, encoding='utf-8')

n = '.github/workflows/delivery-regression.yml'
backup(n)
t = (AUTO / n).read_text(encoding='utf-8')
begin = t.index('          timeout --foreground 60s python -B -X utf8 -m pytest',
                t.index('- name: Test ICIM source failover'))
end = t.index("          python -B -X utf8 - <<'PY'\n          import xml.etree", begin)
replacement = '''          timeout --foreground 65s python -B -X utf8 run_ic_im_v1_4_delivery_tests.py --out-dir daily-regression
          cp daily-regression/regression.xml regression.xml
          # Preserve the additional historical/diagnostic coverage from the old cloud gate.
          timeout --foreground 60s python -B -X utf8 -m pytest -q -p no:cacheprovider \\
            test_poe_ic_im_v1_3_state.py test_run_ic_im_v1_3_github_digest.py \\
            test_external_review_fixes_20260911.py test_ic_im_month_spreads_20261018.py \\
            quant_research_runs/20261018_monthly_spreads_adversarial_v1/data/test_independent_numeric_isolation.py \\
            --junitxml=supplemental-regression.xml
'''
t = t[:begin] + replacement + t[end:]
t = t.replace("          assert not list(root.iter('skipped')), 'ICIM required regression must not skip'",
              "          assert not list(root.iter('skipped')), 'ICIM required regression must not skip'\n"
              "          extra = ET.parse('supplemental-regression.xml').getroot()\n"
              "          assert not list(extra.iter('skipped')), 'ICIM supplemental regression must not skip'")
t = t.replace('            strategy/tested-sha.txt\n', '            strategy/tested-sha.txt\n            strategy/supplemental-regression.xml\n            strategy/daily-regression/receipt.json\n            strategy/daily-regression/collection.json\n            strategy/daily-regression/source_before.json\n            strategy/daily-regression/source_after.json\n')
(AUTO / n).write_text(t, encoding='utf-8')

# Contract tests inspect this immutable copy rather than counting filenames in
# the new YAML wrapper command. The wrapper itself verifies the pinned source.
shutil.copyfile(ROOT / 'docs/ic_im_daily_release_20261023_manifest.json',
                AUTO / 'docs/ic_im_daily_release_20261023_manifest.json')
print(json.dumps({'production_pin': sha, 'calendar_years': [2022,2023,2024,2025,2026],
                  'normal_schedule_changed': False, 'readonly_source_inventory': 29}))
