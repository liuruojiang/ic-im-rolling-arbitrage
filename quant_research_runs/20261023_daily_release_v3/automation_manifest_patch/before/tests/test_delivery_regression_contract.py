"""The recurring production paths must not bypass the no-secrets regression gate."""
from pathlib import Path
from datetime import datetime, timedelta
import sys
import hashlib
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / '.github' / 'workflows'


def read(name):
    return yaml.load((WORKFLOWS / name).read_text(encoding='utf-8'), Loader=yaml.BaseLoader)


def icim_inventory():
    return json.loads((ROOT / 'docs/ic_im_daily_release_20261023_manifest.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('name', ['microcap-realtime-digest.yml', 'ic-im-v1-4-daily-digest.yml'])
def test_production_depends_on_current_revision_regressions(name):
    jobs = read(name)['jobs']
    assert jobs['regression']['uses'] == './.github/workflows/delivery-regression.yml'
    assert 'secrets' not in jobs['regression']
    assert 'continue-on-error' not in jobs['regression']
    assert jobs['regression']['with']['family'] == ('microcap' if name.startswith('microcap') else 'icim')
    assert 'regression' in jobs['send']['needs']
    if 'always()' in jobs['send'].get('if', ''):
        assert "needs.regression.result == 'success'" in jobs['send']['if']


def test_regressions_run_on_changes_and_every_reusable_call_without_delivery():
    workflow = read('delivery-regression.yml')
    assert set(workflow['on']) == {'push', 'pull_request', 'workflow_dispatch', 'workflow_call'}
    assert workflow['permissions'] == {'contents': 'read'}
    text = (WORKFLOWS / 'delivery-regression.yml').read_text(encoding='utf-8')
    assert 'secrets.' not in text
    assert 'send_report.py' not in text
    assert 'workflow_dispatch' in text
    for required in ('test_realtime_preflight.py', 'test_top100_cloud_delivery.py',
                     'test_top100_delivery.py', 'test_ohlcv_provider_validation.py',
                     'test_delivery_transport_retry.py', 'test_poe_ic_im_v1_3_state.py',
                     'test_run_ic_im_v1_3_github_digest.py', 'test_adversarial_delivery.py',
                     'test_ic_im_v1_4_policy.py',
                     'test_poe_ic_im_v1_4_state.py',
                     'test_ic_im_v1_4_ic_csi500_forward.py',
                     'test_ic_im_v1_4_integration_guards.py', 'test_run_ic_im_v1_4_github_digest.py',
                     'test_migrate_ic_im_v1_3_r7_to_v1_4_r1_state.py',
                     'test_adversarial_microcap_delivery.py', 'test_adversarial_icim_delivery.py',
                     'test_realtime_exchange_calendar.py', 'test_exchange_calendar_provider.py',
                     'test_adversarial_cffex_transport_20261009.py',
                     'test_adversarial_cffex_close_runner_20261009.py',
                     'test_tencent_ohlcv_pagination_20261012.py',
                     'test_ic_im_v1_4_repeat_roll_20260925.py',
                     'test_ic_im_v1_4_market_maker_quotes_20260925.py',
                     'test_ic_im_quarter_roll_v1_3.py',
                     'test_ic_im_ordinary_put_route_fix_20261013.py',
                     'test_ic_im_quote_snapshot_guard_20261013.py',
                     'test_ic_im_split_put_summary_20261013.py',
                     'test_panel_exchange_session_guard_20261012.py',
                     'test_security_meta_current_st_history_resolution_20261012.py',
                     'test_security_meta_preserve_20260917.py',
                     'test_adversarial_recovery_boundaries_20261013.py'):
        assert required in text or required in icim_inventory()['required_local_tests']
    assert 'ref: ${{ steps.pin.outputs.sha }}' in text
    assert 'tested-sha.txt' in text
    assert '--junitxml=' in text
    assert workflow['on']['workflow_call']['inputs']['family']['default'] == 'all'
    assert 'fromJSON(inputs.family' in workflow['jobs']['strategy-tests']['strategy']['matrix']['family']


def test_icim_regression_preserves_isolation_cache_and_hard_timeout_contract():
    workflow = read('delivery-regression.yml')
    steps = workflow['jobs']['strategy-tests']['steps']
    step = next(s for s in steps if s.get('name') == 'Test ICIM source failover and persistent ledger invariants')
    assert step['env']['ICIM_REQUIRE_MIGRATION'] == '0'
    assert step['env']['PYTHONDONTWRITEBYTECODE'] == '1'
    assert step['env']['ICIM_STATE_DIR'].startswith('${{ runner.temp }}/')
    assert 'timeout --foreground 65s python -B -X utf8 run_ic_im_v1_4_delivery_tests.py' in step['run']
    assert icim_inventory()['daily_gate_outer_timeout_seconds'] == 60
    assert len(icim_inventory()['required_local_tests']) == 43
    registry = icim_inventory()['historical_exclusion_registry']
    assert len(registry) == 22
    assert set(icim_inventory()['daily_gate_deselections']) <= set(registry)
    assert len(icim_inventory()['daily_gate_deselections']) == 5
    assert 'v14_ledger_20261008 runtime/ic_im_v1_4_r1' in step['run']
    assert 'quarter_roll_20261013/prepare_fixture.py --strategy-root .' in step['run']
    assert "assert not list(root.iter('skipped'))" in step['run']
    # The stable state entrypoint imports the guard cases. Running both files
    # collects the same behavior twice and inflates the reported gate count.
    assert 'test_poe_ic_im_v1_4_state.py' in icim_inventory()['required_local_tests']
    assert 'test_ic_im_v1_4_state_guards.py' not in icim_inventory()['required_local_tests']


def test_microcap_regression_covers_required_files_with_isolated_zero_skip_gate():
    steps = read('delivery-regression.yml')['jobs']['strategy-tests']['steps']
    step = next(s for s in steps if s.get('name') == 'Test Microcap state and cloud transport invariants')
    required = (
        'test_adversarial_delivery_plain_20260904.py',
        'test_adversarial_delivery_state_consistency.py',
        'test_adversarial_microcap_delivery.py',
        'test_adversarial_v20_plain_20260904.py',
        'test_adversarial_v23_plain_20260904.py',
        'test_exchange_calendar_provider.py',
        'test_index_history_preflight.py',
        'test_realtime_exchange_calendar.py',
        'test_realtime_preflight.py',
        'test_realtime_state_guard_20260915.py',
        'test_script_delivery_adversarial.py',
        'test_top100_cloud_delivery.py',
        'test_top100_delivery.py',
        'test_v23_identity_adversarial.py',
        'test_v25_plain_promotion.py',
        'test_v25_realtime_artifact_invalidation.py',
        'test_realtime_publication_deadline.py',
        'test_adversarial_recovery_boundaries_20261013.py',
        'test_cninfo_history_fail_closed_20261015.py',
        'test_exchange_session_completeness_20261015.py',
        'test_return_integrity_20261015.py',
        'test_security_meta_fail_closed_20261015.py',
    )
    assert step['env']['MICROCAP_DIGEST_SOURCE'] == '${{ github.workspace }}/automation/scripts/build_microcap_realtime_digest.py'
    assert step['env']['PYTHONDONTWRITEBYTECODE'] == '1'
    assert 'working-directory' not in step or step['working-directory'] == 'strategy'
    assert 'timeout --foreground 120s python -B -X utf8 -m pytest -q -p no:cacheprovider' in step['run']
    assert '--junitxml=regression.xml' in step['run']
    assert 'assert not list(root.iter(\'skipped\'))' in step['run'] or "root.iter('skipped')" in step['run']
    assert "Path('../automation')" in step['run']
    assert "tests/fixtures/microcap/v23_identity_20261009" in step['run']
    assert "Path(entry['target_path'])" in step['run']
    assert "if destination.exists():" in step['run']
    for name in required:
        assert name in step['run'], f'missing mandatory microcap test: {name}'


def test_external_dispatch_repairs_are_tested_on_changes_and_each_family():
    workflow = read('delivery-regression.yml')
    for event in ('push', 'pull_request'):
        assert 'cloudflare-workers/**' in workflow['on'][event]['paths']
    step = next(s for s in workflow['jobs']['automation-tests']['steps']
                if s.get('name') == 'Check delivery contracts and all automation regressions')
    for line in step['run'].splitlines():
        if line.strip().startswith(('microcap)', 'icim)')):
            assert 'tests/test_cloudflare_dispatch_retry.py' in line
        if line.strip().startswith('microcap)'):
            assert 'tests/test_restore_approved_microcap_state.py' in line


def test_microcap_v23_identity_fixture_matches_frozen_real_whole_delivery():
    import csv
    import io
    import json

    fixture = ROOT / 'tests/fixtures/microcap/v23_identity_20261009'
    provenance_bytes = (fixture / 'fixture_provenance.json').read_bytes()
    provenance = json.loads(provenance_bytes.decode('utf-8'))
    assert provenance['signal_date'] == '2026-10-09'
    assert provenance['strategy_revision'] == 'plain_lb25_hl2p5_r2off_vol10_26_20_20260904'
    source_manifest = provenance['source']['source_manifest']
    assert source_manifest['sha256_raw'] == 'd39a2539d47b591a4400a2f8155dfc5869d19f4ff78e247ca7d07c0b92a3e129'
    manifest_bytes = (ROOT / source_manifest['fixture_path']).read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest() == source_manifest['sha256_raw']
    manifest = json.loads(manifest_bytes.decode('utf-8'))
    assert manifest['ok'] is True
    assert manifest['scope'] == 'whole_workspace_delivery'
    assert manifest['errors'] == []
    assert manifest['expected_date'] == '2026-10-09'
    assert manifest['status'] == 'complete'
    assert manifest['release_sha'] == provenance['source']['source_head']
    assert provenance['source']['post_signal_whole_check']['exit_code'] == 0
    assert provenance['source']['post_signal_whole_check']['ok'] is True

    frozen_hashes = {
        'nav': ('c5f379c3f09cbbd7648b48f438b77040ffd51f2174259089cef07e3cf540584c',
                '81610c690e24113b4728833448b511ebf03ebecb43f6b6fd5d5b8342ff1e50ce'),
        'latest_signal': ('484a61ecc89962b637f158e3db0eb8ad0da0afbbefa7ea5beef8b1b02cf2c012',
                          '833c4e026e398c0f6bab6cde00c1bb0ef9b0d250f7dd154d6100257ea08f1296'),
    }
    assert set(provenance['files']) == set(frozen_hashes)
    for key, expected_file in provenance['files'].items():
        path = ROOT / expected_file['fixture_path']
        raw = path.read_bytes()
        assert expected_file['sha256_raw'] == frozen_hashes[key][0]
        assert expected_file['sha256_lf_normalized_manifest'] == frozen_hashes[key][1]
        assert len(raw) == expected_file['size_bytes']
        assert hashlib.sha256(raw).hexdigest() == expected_file['sha256_raw']
        normalized = raw.replace(b'\r\n', b'\n')
        assert hashlib.sha256(normalized).hexdigest() == expected_file['sha256_lf_normalized_manifest']
        entry = manifest['streams'][Path(expected_file['source_path']).name]
        assert entry['sha256'] == expected_file['sha256_lf_normalized_manifest']
        assert entry['rows'] == expected_file['manifest_rows']
        assert entry['latest_date'] == expected_file['manifest_latest_date']
        rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
        assert len(rows) == expected_file['row_count']
        assert rows[0]['date'] == expected_file['first_date']
        assert rows[-1]['date'] == expected_file['last_date']
        assert {row['strategy_revision'] for row in rows} == {provenance['strategy_revision']}
        if key == 'latest_signal':
            assert len(rows) == 1
            assert rows[0]['signal_timing'] == expected_file['signal_timing']
            assert rows[0]['official_close_confirmed_signal'].lower() == 'true'

    attributes = (ROOT / '.gitattributes').read_text(encoding='utf-8')
    assert 'tests/fixtures/microcap/v23_identity_20261009/*.csv -text' in attributes
    assert 'tests/fixtures/microcap/v23_identity_20261009/*.json -text' in attributes


def test_microcap_automation_suite_is_not_coupled_to_icim_release_fixtures():
    text = (WORKFLOWS / 'delivery-regression.yml').read_text(encoding='utf-8')
    microcap_case = text.split('microcap) suites=(', 1)[1].split(') ;;', 1)[0]
    assert 'tests/test_microcap_*.py' in microcap_case
    assert 'test_adversarial_delivery.py' not in microcap_case
    assert (ROOT / 'tests/test_microcap_adversarial_delivery.py').is_file()


@pytest.mark.parametrize('name', ['microcap-realtime-digest.yml', 'ic-im-v1-4-daily-digest.yml'])
def test_normal_smtp_requires_durable_intent_and_mode_specific_preflight(name):
    steps = read(name)['jobs']['send']['steps']
    indexed = {s.get('id'): (i, s) for i, s in enumerate(steps) if s.get('id')}
    assert indexed['publication_mode'][0] < indexed['delivery_gate'][0]
    assert '--publication-mode "${{ steps.publication_mode.outputs.mode }}"' in indexed['delivery_gate'][1]['run']
    assert indexed['send_intent'][0] < indexed['send_gmail'][0]
    intent = indexed['send_intent'][1]
    assert intent['uses'] == 'actions/upload-artifact@v7'
    marker_source = 'delivery_gate' if name.startswith('microcap') else 'prepare_marker'
    assert intent['with']['name'] == '${{ steps.' + marker_source + '.outputs.marker_name }}-send-intent'
    if marker_source == 'prepare_marker':
        assert indexed['prepare_marker'][0] < indexed['send_intent'][0]
    assert intent['with']['if-no-files-found'] == 'error'
    assert 'continue-on-error' not in intent
    assert "steps.send_intent.outcome == 'success'" in indexed['send_gmail'][1]['if']
    if name.startswith('microcap'):
        receipt = indexed['smtp_receipt'][1]
        assert indexed['send_gmail'][0] < indexed['smtp_receipt'][0]
        assert receipt['uses'] == 'actions/upload-artifact@v7'
        assert receipt['with']['name'] == '${{ steps.delivery_gate.outputs.marker_name }}-smtp-accepted'
        assert receipt['with']['if-no-files-found'] == 'error'
    completion = next(s for s in steps if s.get('name') == 'Mark digest delivered')
    assert completion['with']['if-no-files-found'] == 'error'
    if name.startswith('microcap'):
        assert "steps.smtp_receipt.outcome == 'success'" in completion['if']
        for guarded in (intent, indexed['send_gmail'][1]):
            assert "steps.whole_delivery.outputs.validated == 'true'" in guarded['if']


def test_stale_market_fixture_is_frozen_real_data_and_never_in_production_steps():
    data = (ROOT / 'tests/fixtures/icim/sina_000905_index.csv').read_bytes().replace(b'\r\n', b'\n')
    assert hashlib.sha256(data).hexdigest() == 'c5121b044133099e250fd5e5e803c447bf8811e5a4ae8cf8f19e2b8f5c2ddcfd'
    for name in ('microcap-realtime-digest.yml', 'ic-im-v1-4-daily-digest.yml'):
        assert 'tests/fixtures/' not in (WORKFLOWS / name).read_text(encoding='utf-8')


def test_real_ledger_fixture_retains_exact_artifact_bytes_and_full_chain():
    import json

    fixture = ROOT / 'tests/fixtures/icim/v14_ledger_20261008'
    provenance = json.loads((fixture / 'fixture_provenance.json').read_text(encoding='utf-8'))
    assert provenance['source_run_id'] == 37773833605
    assert provenance['source_artifact_id'] == 11549730442
    assert len(provenance['sha256']) == 13
    for name, digest in provenance['sha256'].items():
        assert hashlib.sha256((fixture / name).read_bytes()).hexdigest() == digest
    journals = sorted((fixture / 'journal').glob('*.json'))
    assert len(journals) == 11
    previous = None
    for sequence, path in enumerate(journals):
        record = json.loads(path.read_text(encoding='utf-8'))
        payload = {k: v for k, v in record.items() if k != 'digest'}
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        assert hashlib.sha256(canonical.encode('utf-8')).hexdigest() == record['digest']
        assert record['sequence'] == sequence
        assert record['schema_version'] == 4
        assert record['strategy_revision'] == 'r1'
        if previous is not None:
            assert record['previous_digest'] == previous['digest']
        previous = record
    assert json.loads((fixture / 'latest.json').read_text(encoding='utf-8')) == previous
    assert previous['verified_day'] == provenance['verified_day'] == '2026-10-08'
    assert previous['digest'] == provenance['digest']
    migration = json.loads((fixture / 'migration_record.json').read_text(encoding='utf-8'))
    assert migration['new_ledger']['digest'] == json.loads(journals[0].read_text(encoding='utf-8'))['digest']


def test_calendar_failure_cannot_be_silent_holiday_or_manual_bypass():
    jobs = read('microcap-realtime-digest.yml')['jobs']
    assert "needs.check-trading-day.result == 'success'" in jobs['send']['if']
    calendar = next(s for s in jobs['check-trading-day']['steps'] if s.get('id') == 'calendar')['run']
    failure = calendar.split('except Exception as exc:', 1)[1].split('print(f"A_SHARE_TODAY', 1)[0]
    assert 'raise RuntimeError' in failure
    assert 'should_run = False' not in failure


@pytest.mark.parametrize('case', ['trading', 'holiday', 'unavailable', 'empty', 'expired'])
def test_actual_calendar_step_distinguishes_unknown_from_holiday(monkeypatch, capsys, case):
    """Execute the actual embedded workflow code with an isolated provider fixture."""
    job = read('microcap-realtime-digest.yml')['jobs']['check-trading-day']
    command = next(s for s in job['steps'] if s.get('id') == 'calendar')['run']
    code = command.split("python - <<'PY' | tee calendar.env\n", 1)[1].split('\nPY', 1)[0]
    def fetch():
        if case == 'unavailable':
            raise ConnectionError('simulated calendar disconnect')
        today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
        dates = {'trading': [today], 'holiday': [today-timedelta(days=3), today+timedelta(days=3)],
                 'empty': [], 'expired': [today-timedelta(days=3)]}[case]
        return {'trade_date': dates}
    monkeypatch.setitem(sys.modules, 'akshare', SimpleNamespace(tool_trade_date_hist_sina=fetch))
    monkeypatch.setitem(sys.modules, 'pandas', SimpleNamespace(to_datetime=lambda values: SimpleNamespace(dt=SimpleNamespace(date=values))))
    if case in ('unavailable', 'empty', 'expired'):
        with pytest.raises(RuntimeError, match='not a confirmed holiday'):
            exec(compile(code, '<actual workflow calendar>', 'exec'), {})
        assert 'SHOULD_RUN_MICROCAP=false' not in capsys.readouterr().out
    else:
        exec(compile(code, '<actual workflow calendar>', 'exec'), {})
        assert f"SHOULD_RUN_MICROCAP={str(case == 'trading').lower()}" in capsys.readouterr().out

