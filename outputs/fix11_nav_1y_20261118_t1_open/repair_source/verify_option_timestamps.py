"""Exercise the old and corrected collectors against frozen Sina response bytes."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pandas as pd


REPAIR = Path(__file__).resolve().parent
CANDIDATE = REPAIR.parent
FROZEN = CANDIDATE.parent / 'fix11_nav_1y_20261118_close'
POST = FROZEN / 'fresh_sources/post_20261105_20261118'
RAW = POST / 'raw_close'
DAY = '2026-11-18'
SID = '10012149'
CONTRACT = '510500P2612M06500'
OLD_STAMP = f'{DAY} 00:00:00'
QUOTE_FILE = 'sina_510500_puts_20261118_close.csv.gz'
MASTER_FILE = 'sina_510500_puts_20261118_close_master.csv.gz'
GREEKS_FILE = 'sina_510500_puts_20261118_close_greeks.csv'
FILES = [QUOTE_FILE, MASTER_FILE, GREEKS_FILE]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_collector(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def warm_fixture(module, destination, alternative_stamp=None):
    destination.mkdir()
    module.POST = destination
    module.EXT = destination

    def fixture_response(name, _url, **_params):
        content = (RAW / name).read_bytes()
        if alternative_stamp is not None and name.startswith('sina_510500_details_'):
            pattern = rb'(hq_str_CON_OP_10012149=")([^"]+)(")'

            def replace(match):
                tokens = match.group(2).split(b',')
                if tokens[32] != OLD_STAMP.encode('ascii'):
                    raise RuntimeError('frozen targeted response timestamp changed')
                tokens[32] = alternative_stamp.encode('ascii')
                return match.group(1) + b','.join(tokens) + match.group(3)

            content = re.sub(pattern, replace, content)
        return SimpleNamespace(content=content)

    module.raw_get = fixture_response
    summary = module.fetch_etf_options()
    frames = {
        name: pd.read_csv(destination / name, dtype={'security_id': str}).set_index(
            'security_id', verify_integrity=True
        ) for name in FILES
    }
    return summary, frames


def untouched_rows(old_frames, fixed_frames):
    for name in FILES:
        pd.testing.assert_frame_equal(
            old_frames[name].drop(index=SID), fixed_frames[name].drop(index=SID), check_exact=True
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('scratch', type=Path)
    args = parser.parse_args()
    os.environ['ICIM_STATE_DIR'] = str(args.scratch / 'isolated_state')
    os.environ['ICIM_REQUIRE_MIGRATION'] = '0'
    source_old = FROZEN / 'capture_nov18_close.py'
    source_fixed = CANDIDATE / 'capture_nov18_close.py'
    original = load_collector(source_old, 'original_nov18_capture')
    fixed = load_collector(source_fixed, 'fixed_nov18_capture')
    raw_hashes = {
        p.name: sha(p) for p in RAW.glob('sina_510500_*.txt')
    }
    old_stats, old_frames = warm_fixture(original, args.scratch / 'original')
    fixed_dir = args.scratch / 'repaired'
    fixed_stats, fixed_frames = warm_fixture(fixed, fixed_dir)
    for name in FILES:
        frozen = pd.read_csv(POST / name, dtype={'security_id': str}).set_index('security_id')
        pd.testing.assert_frame_equal(old_frames[name], frozen, check_exact=True)
    assert old_stats['rows'] == fixed_stats['rows'] == 60
    assert old_stats['fresh_rows'] == old_stats['greek_available_rows'] == 54
    assert fixed_stats['fresh_rows'] == fixed_stats['greek_available_rows'] == 53
    assert old_frames[QUOTE_FILE].loc[SID, 'close'] == 0.3927
    untouched_rows(old_frames, fixed_frames)
    q = fixed_frames[QUOTE_FILE].loc[SID]
    assert q[['open','high','low','close']].isna().all()
    assert q.raw_close == 0.3927 and q.volume == 17073 and q.source_stamp == DAY + 'T00:00:00'
    assert fixed_frames[MASTER_FILE].loc[SID, 'contract_id'] == CONTRACT
    assert fixed_frames[MASTER_FILE].loc[SID, ['delta','implied_volatility']].isna().all()
    assert not fixed_frames[GREEKS_FILE].loc[SID,'same_day_positive_volume_closing_print']

    tests = []
    for i, (stamp, admitted) in enumerate([
        (f'{DAY} 09:14:59', False),
        (f'{DAY} 09:15:00', True),
        (f'{DAY} 15:15:00', True),
        (f'{DAY} 15:15:01', False),
        ('2026-11-17 15:00:00', False),
    ]):
        _, rows = warm_fixture(fixed, args.scratch / f'boundary_{i}', stamp)
        untouched_rows(fixed_frames, rows)
        assert pd.notna(rows[QUOTE_FILE].loc[SID, 'close']) == admitted
        assert pd.notna(rows[MASTER_FILE].loc[SID, 'delta']) == admitted
        tests.append({'timestamp':stamp, 'quote_and_greeks_admitted':admitted})

    previous = CANDIDATE.parent / 'fix11_nav_1y_20261118_redteam/source'
    retained_sina = POST / 'raw' / f'sina_510500_{SID}.json'
    retained_em = previous / f'eastmoney_10.{SID}_daily.raw'
    sina = [r for r in json.loads(retained_sina.read_text(encoding='utf-8')) if r['d'] == DAY]
    second = json.loads(retained_em.read_text(encoding='utf-8'))['data']
    em = [r.split(',') for r in second['klines'] if r.split(',')[0] == DAY]
    assert len(sina) == 1 and second['code'] == SID and second['market'] == 10 and len(em) == 1
    assert [float(sina[0][k]) for k in ('o','c','h','l')] == [float(v) for v in em[0][1:5]]
    assert float(sina[0]['c']) == q.raw_close
    assert raw_hashes == {p.name:sha(p) for p in RAW.glob('sina_510500_*.txt')}

    rebuilt = REPAIR / 'terminal_market_repro'
    rebuilt.mkdir(exist_ok=True)
    for name in FILES:
        saved = rebuilt / name
        if saved.exists():
            # gzip headers can differ between runs. Keep the frozen bytes and
            # verify the reconstructed records before reusing their hash.
            previous_rows = pd.read_csv(saved, dtype={'security_id': str}).set_index('security_id')
            pd.testing.assert_frame_equal(previous_rows, fixed_frames[name], check_exact=True)
        else:
            shutil.copy2(fixed_dir / name, saved)
    report = {
        'status':'PASS',
        'original_capture_sha256':sha(source_old),
        'candidate_capture_sha256':sha(source_fixed),
        'frozen_raw_sha256':raw_hashes,
        'old_stats':old_stats,
        'fixed_stats':fixed_stats,
        'only_changed_security_id':SID,
        'contract':CONTRACT,
        'rejected_tick_timestamp':OLD_STAMP,
        'preserved_unqualified_raw_close':float(q.raw_close),
        'candidate_outputs_sha256':{n:sha(rebuilt/n) for n in FILES},
        'boundaries':tests,
        'independent_daily_close_confirmation': {
            'sina_raw_sha256':sha(retained_sina),
            'eastmoney_raw_sha256':sha(retained_em),
            'date':DAY,
            'ohlc_in_open_close_high_low_order':[float(v) for v in em[0][1:5]],
            'classified_as_valid_session_tick':False,
        }
    }
    (REPAIR / 'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__':
    main()
