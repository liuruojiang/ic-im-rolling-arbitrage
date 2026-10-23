"""Independent tests of other authors' market/evidence fixes; all HTTP is fake."""
from datetime import timedelta, timezone
import pytest
import requests
import pandas as pd
import poe_ic_im_mainline_v1_4_bot as bot
import ic_im_signal_evidence_v1 as evidence
from test_icim_redteam_market_repairs_20261023 import CLOCK, vendor, qt, em_quote
from test_icim_redteam_evidence_repairs_20261023 import (
    packet, response, price, opening_price, old_archive, ONE, TWO, URL, DAY,
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **k: pytest.fail("network forbidden"))


@pytest.mark.parametrize("provider", ["qt", "em"])
@pytest.mark.parametrize("future", [False, True])
def test_utc_entry_observation_uses_same_instant_not_wall_hour(monkeypatch, provider, future):
    stamp = CLOCK + timedelta(seconds=int(future))
    vendor(monkeypatch, qt(stamp=stamp) if provider == "qt" else None,
           em_quote(stamp=stamp) if provider == "em" else None)
    monkeypatch.setattr(bot, "_now_beijing", lambda: CLOCK.astimezone(timezone.utc))
    monkeypatch.setattr(bot.time_module, "monotonic", lambda: 100.)
    if future:
        with pytest.raises(RuntimeError):
            bot.fetch_live_price_quote("IC")
    else:
        assert bot.fetch_live_price_quote("IC")["source_time"] == "14:30:00"


@pytest.mark.parametrize("provider", ["qt", "em"])
def test_direct_provider_receipt_time_progresses_without_outer_collection(monkeypatch, provider):
    vendor(monkeypatch, qt(stamp=CLOCK + timedelta(seconds=1)) if provider == "qt" else None,
           em_quote(stamp=CLOCK + timedelta(seconds=1)) if provider == "em" else None)
    ticks = [100.]
    monkeypatch.setattr(bot.time_module, "monotonic", lambda: ticks[0])
    original = bot._evidence_http_get

    def receive(url, **kwargs):
        result = original(url, **kwargs)
        ticks[0] += 2.
        return result

    monkeypatch.setattr(bot, "_evidence_http_get", receive)
    assert bot.fetch_live_price_quote("IC")["source_time"] == "14:30:01"


@pytest.mark.parametrize("provider", ["qt", "em"])
def test_after_session_index_update_rejected_even_if_already_received(monkeypatch, provider):
    stamp = CLOCK.replace(hour=15, minute=0, second=1)
    vendor(monkeypatch, qt(stamp=stamp) if provider == "qt" else None,
           em_quote(stamp=stamp) if provider == "em" else None)
    monkeypatch.setattr(bot, "_now_beijing", lambda: CLOCK.replace(hour=16))
    with pytest.raises(RuntimeError):
        bot.fetch_live_price_quote("IC")


def test_original_close_ignores_unusable_live_quote_and_uses_real_daily_history(monkeypatch):
    vendor(monkeypatch, qt(symbol="sh000852", code="000852", price=3000), history_today=True)
    monkeypatch.setattr(bot, "fetch_live_price_quote", lambda *a: pytest.fail("close consumed intraday quote"))
    out = bot.live_proxy("IC", CLOCK.replace(hour=18))
    assert out["live_price_source"] == "指数公开日线历史收盘"
    assert out["history_date"] == DAY


def test_capture_named_market_cannot_cache_future_tick_as_valid_result(monkeypatch, tmp_path):
    vendor(monkeypatch, qt(stamp=CLOCK.replace(hour=15, minute=0)))
    monkeypatch.setattr(bot.time_module, "monotonic", lambda: 100.)
    monkeypatch.setattr(evidence, "_now", lambda: CLOCK)
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        with pytest.raises(RuntimeError):
            bot.live_proxy("IC", CLOCK)
    assert not session.context["complete"]
    assert not any(row["role"] == "market.live" for row in session.context["entries"].values())
    with pytest.raises(evidence.EvidenceUnavailable):
        evidence.load_session(tmp_path, DAY)


@pytest.mark.parametrize("suffix", [b"\x00", b"//ignored", b"/*ignored*/", b";", b"junk", b"\xff"])
def test_projection_and_direct_open_do_not_ignore_trailing_non_packet_bytes(monkeypatch, tmp_path, suffix):
    body = packet() + packet(TWO) + suffix
    monkeypatch.setattr(evidence, "_now", lambda: CLOCK.replace(hour=18))
    reader = old_archive(tmp_path, [([ONE, TWO], body)])
    with pytest.raises(evidence.EvidenceUnavailable):
        reader.http_response(URL, {"list": ONE})
    monkeypatch.setattr(bot, "_evidence_http_get", lambda *a, **k: response(packet() + suffix))
    with pytest.raises(RuntimeError):
        bot.verify_sina_option_open("MO2611-P-6000", DAY, opening_price(), CLOCK.replace(hour=18))


@pytest.mark.parametrize("separator,accepted", [(b"\n", True), (b"\r\n", True), (b" ", False), (b"", False), (b"\t", False)])
def test_semicolonless_packets_need_real_line_separator(separator, accepted):
    first = packet().removesuffix(b";")
    body = first + separator + packet(TWO)
    if accepted:
        values = evidence.sina_mo_quote_variables(body, [ONE, TWO])
        assert set(values) == {ONE, TWO}
        assert values[ONE][1] == first
    else:
        with pytest.raises(evidence.EvidenceUnavailable):
            evidence.sina_mo_quote_variables(body, [ONE, TWO])


@pytest.mark.parametrize("prefix", [b"var var ", b"this.", b"\xef\xbb\xbf", b"/*x*/", b"var\n", b"var_", b"\\u0068"])
def test_strict_quote_open_and_batch_prefetch_agree_on_lexical_prefix(monkeypatch, prefix):
    first = packet().removeprefix(b"var ")
    body = prefix + first
    monkeypatch.setattr(bot, "_evidence_http_get", lambda *a, **k: response(body))
    clock = CLOCK.replace(hour=18)
    with pytest.raises(RuntimeError):
        bot.verify_sina_option_quote("MO2611-P-6000", DAY, price(), clock)
    with pytest.raises(RuntimeError):
        bot.verify_sina_option_open("MO2611-P-6000", DAY, opening_price(), clock)
    monkeypatch.setattr(bot, "_evidence_http_get", lambda *a, **k: response(body + packet(TWO)))
    with pytest.raises(RuntimeError):
        bot._prefetch_mo_replay_verification(pd.DataFrame({"instrument": ["MO2611-P-6000", "MO2611-P-6100"]}), clock)


def test_identical_overlapping_valid_batches_project_same_original_packet(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "_now", lambda: CLOCK.replace(hour=18))
    third = "P_OP_mo2611P6200"
    reader = old_archive(tmp_path, [([ONE, TWO], packet() + packet(TWO)),
                                    ([ONE, third], packet() + packet(third))])
    result = reader.http_response(URL, {"list": ONE})
    assert result["body"] == packet()
    assert result["matching_recordings"] == 2
