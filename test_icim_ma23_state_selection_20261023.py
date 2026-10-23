"""Original-reader routing tests; synthetic values are never market facts."""
from datetime import datetime, timedelta

import pytest

import ic_im_evidence_adapter_v1 as adapter
import ic_im_signal_evidence_v1 as evidence
import poe_ic_im_mainline_v1_4_bot as bot


def _packet(monkeypatch, root, product, decision, finish):
    current = [decision]
    monkeypatch.setattr(evidence, '_now', lambda: current[0])
    metadata = {'product': product, 'original_close': True,
                'input_revision': adapter.INPUT_REVISION}
    with evidence.capture_session(root, decision.date(), decision, metadata) as session:
        for role in sorted(adapter._original_required_roles(product)):
            session.call(role, {}, lambda: {'synthetic_routing_test_only': True})
        current[0] = finish
    return session


@pytest.mark.parametrize('product', ['IC', 'IM'])
def test_newer_cross_midnight_packet_cannot_hide_valid_original(monkeypatch, tmp_path, product):
    day = bot.date(2026, 10, 22)
    first = datetime(2026, 10, 22, 16, 0, tzinfo=bot.BEIJING)
    good = _packet(monkeypatch, tmp_path, product, first, first + timedelta(seconds=5))
    late = first.replace(hour=23, minute=59, second=50)
    _packet(monkeypatch, tmp_path, product, late, late + timedelta(seconds=20))
    status = adapter.capture_day_status(tmp_path, day)
    assert status['products'][product]['complete_attempts'] == 1
    reader, closed = adapter.load_original(bot, tmp_path, product, day)
    assert reader.decision_at == first
    assert closed == first + timedelta(seconds=5)
    assert reader.manifest_path == good.manifest_path


@pytest.mark.parametrize('product', ['IC', 'IM'])
def test_original_selection_still_chooses_latest_eligible_packet(monkeypatch, tmp_path, product):
    first = datetime(2026, 10, 22, 16, 0, tzinfo=bot.BEIJING)
    _packet(monkeypatch, tmp_path, product, first, first + timedelta(seconds=5))
    latest = first + timedelta(hours=1)
    good = _packet(monkeypatch, tmp_path, product, latest, latest + timedelta(seconds=5))
    late = first.replace(hour=23, minute=59, second=50)
    _packet(monkeypatch, tmp_path, product, late, late + timedelta(seconds=20))
    reader, closed = adapter.load_original(bot, tmp_path, product, first.date())
    assert reader.decision_at == latest
    assert closed == latest + timedelta(seconds=5)
    assert reader.manifest_path == good.manifest_path
