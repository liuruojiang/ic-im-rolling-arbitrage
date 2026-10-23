from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
r = ROOT / 'quant_research_runs/20261023_multiround_adversarial_v1'
t = (r / 'run_checks.py').read_text(encoding='utf-8')
t = t.replace('ROOT = Path(__file__).resolve().parents[2]', 'ROOT = Path(__file__).resolve().parent')
t = t.replace('HERE = Path(__file__).resolve().parent / os.environ.get("ICIM_AUDIT_STAGE", "cumulative")',
              'HERE = Path(os.environ["ICIM_TEST_OUTPUT_DIR"])')
t = t.replace("Path(__file__).resolve().parent/'legacy_fixture_adaptations.json'",
              "ROOT/'docs/icim_delivery_legacy_fixtures_20261023.json'")
t = t.replace("Path(__file__).resolve().parent/'pytest.ini'", "ROOT/'docs/icim_delivery_pytest.ini'")
(ROOT / '_icim_delivery_test_worker.py').write_text(t, encoding='utf-8')
for source, dest in [('evidence_gate.py', 'icim_delivery_test_evidence.py'),
                     ('pytest.ini', 'docs/icim_delivery_pytest.ini'),
                     ('legacy_fixture_adaptations.json', 'docs/icim_delivery_legacy_fixtures_20261023.json'),
                     ('exclusions.json', 'docs/icim_historical_deselections_20261023.json')]:
    shutil.copyfile(r / source, ROOT / dest)
print('delivery worker and unchanged exclusion registry prepared')
