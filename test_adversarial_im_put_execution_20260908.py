"""Read-only adversarial probes; synthetic cases are not performance evidence.

Run: python -B -X utf8 -m pytest -q test_adversarial_im_put_execution_20260908.py
The real-row adapter probe uses an existing frozen CFFEX research cache.  It
stubs transport and whole-chain structure only; the price adapter is real.
"""
from datetime import date, datetime
import io
from pathlib import Path
import zipfile
import json
import shutil
from copy import deepcopy
from datetime import timedelta

import pandas as pd
import pytest

import poe_ic_im_mainline_v1_3_bot as bot


@pytest.mark.parametrize(
    "selector", [bot.select_im_put_for_reset, bot.select_independent_im_put_for_reset]
)
def test_new_put_admission_and_existing_put_exit_accept_same_valid_quote(selector):
    """An admitted quoted contract must not become unclosable solely due to OI.

    Synthetic post-policy boundary: zero exchange OI and zero traded volume,
    but a finite last price and live two-sided maker quotes.
    """
    quotes = pd.DataFrame([
        dict(instrument="MO2612-P-8200", lastprice=350.0, volume=0,
             position=0, bprice=349.0, sprice=351.0)
    ])
    chosen = selector(quotes, date(2026, 9, 8), 8000.0)
    assert chosen.instrument == "MO2612-P-8200"
    # This is the same guard used by both Put legs when target quantity falls
    # to zero or the monthly maintenance changes the held strike.
    bot._require_existing_leg_quote(
        "IM动量Put", chosen.instrument, chosen, "RESIZE_OR_ROLL"
    )


def test_zero_volume_official_adapter_uses_current_settlement_not_stale_close(monkeypatch):
    """Reproduce the discrepancy using a real untouched 2026-09-03 CFFEX row."""
    source = Path(__file__).parent / (
        "quant_param_scan_runs/20260908_im_put_revalidation_layer1_v1/real_options.csv.gz"
    )
    fixture = Path(__file__).parent / "tests/fixtures/icim_adversarial/real_zero_volume_put.csv"
    extracted = pd.read_csv(fixture)
    assert len(extracted) == 1
    if source.exists():
        data = pd.read_csv(source)
        original = data.loc[data.date.eq("2026-09-03") & data.contract.eq("MO2612-P-8600")]
        pd.testing.assert_frame_equal(extracted, original.reset_index(drop=True))
    row = extracted.iloc[0]
    assert row.volume == 0
    assert row.close == 1215.0
    assert row.settle == 1323.6
    # Reconstruct official transport column names from that real row.  The
    # fixture is a single-contract slice, not an invented market observation.
    daily = pd.DataFrame([{
        "合约代码": row.contract, "今开盘": row.open, "最高价": row.high,
        "最低价": row.low, "成交量": row.volume, "持仓量": row.open_interest,
        "今收盘": row.close, "今结算": row.settle,
    }])
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("20260903_1.csv", daily.to_csv(index=False).encode("gbk"))
    monkeypatch.setattr(bot, "_cffex_month_archive", lambda month: archive.getvalue())
    # A sliced quote cannot have all six listed months; this isolation does not
    # bypass source-day, numeric-price, volume or open-interest validation.
    monkeypatch.setattr(bot, "_validate_complete_mo_chain", lambda *args: None)
    frame = bot._cffex_historical_quote_frame(
        "MO", date(2026, 9, 3), datetime(2026, 9, 3, 18, tzinfo=bot.BEIJING)
    )
    assert frame.iloc[0].lastprice == pytest.approx(float(row.settle)), (
        "The formal adapter discarded the official settlement and exposed the "
        "unchanged zero-volume close as today's option price; research uses settlement."
    )


def test_zero_volume_with_positive_open_interest_is_not_itself_an_exit_block():
    """Control: the failure above is an OI gate, not merely zero volume."""
    quote = pd.Series(dict(lastprice=350.0, volume=0, position=2,
                           bprice=349.0, sprice=351.0))
    bot._require_existing_leg_quote(
        "IM核心Put", "MO2612-P-8200", quote, "RESIZE_OR_ROLL"
    )


@pytest.mark.parametrize("momentum_reentry", [False, True])
def test_monthly_reset_is_not_persisted_twice_on_tminus1_and_expiry(monkeypatch, tmp_path, momentum_reentry):
    """Synthetic future-day prices through the real builder and temp ledger.

    Real Sept 4 accepted portable ledger seeds the scenario. All later prices and signals
    are explicit fault-injection inputs, not actual Sept 17/18 observations.
    """
    import poe_ic_im_v1_3_state as state
    root = Path(__file__).parent
    # These two unchanged accepted records are tracked fixtures, so this key
    # event/state test runs in CI without ignored outputs or research caches.
    fixture_dir = root / "tests/fixtures/icim_adversarial"
    accepted = json.loads((fixture_dir / "confirmed.json").read_text(encoding="utf-8"))
    template = accepted["signals"]
    ledger = tmp_path / "ledger"
    (ledger / "journal").mkdir(parents=True)
    for fixture_name in ("previous.json", "confirmed.json"):
        fixture_path = fixture_dir / fixture_name
        record = json.loads(fixture_path.read_text(encoding="utf-8"))
        shutil.copyfile(fixture_path, ledger / "journal" / f"{record['sequence']:06d}-{record['verified_day']}.json")
    shutil.copyfile(fixture_dir / "confirmed.json", ledger / "latest.json")
    store = state.StateStore(tmp_path / "ledger")
    current = store.load_latest()
    saved_anchors = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    observed = []

    def live_fixture(product, clock):
        live = deepcopy(template["IM"])
        day = clock.date()
        live.update(history_date=day, price=8000.0, momentum_120=-0.1,
                    momentum_next_weight=0.0 if momentum_reentry and day == date(2026, 9, 17) else 1.0,
                    momentum_signal_date=day,
                    v13_parent_puts_per_full_core=3,
                    v13_put_target_qty_normalized=1.5,
                    grid_current_units=0.0, grid_target_units=0.0,
                    valuation_provenance={})
        return live

    def quotes_fixture(product, clock):
        day = clock.date()
        price = 8000.0 if day < date(2026, 9, 18) else 8200.0
        contracts = ([f"IM{m}" for m in ["2609", "2610", "2612", "2703"]]
                     if product == "IM" else
                     [f"MO2612-P-{k}" for k in [7200, 7600, 7800, 8000, 8200, 8400, 8600]]
                     + ["MO2609-P-7200"])
        frame = pd.DataFrame([dict(instrument=c, lastprice=price if product == "IM" else 350.0,
                                   volume=10.0, position=20.0, bprice=349.0, sprice=351.0,
                                   source_date=day, source_time="15:15:00") for c in contracts])
        frame.attrs.update(source="adversarial_synthetic", source_date=day,
                           listed_instruments=contracts)
        return frame

    monkeypatch.setattr(bot, "live_proxy", live_fixture)
    monkeypatch.setattr(bot, "fetch_cffex_quotes", quotes_fixture)
    monkeypatch.setattr(bot, "select_im_call_d10", lambda *a: None)
    monkeypatch.setattr(bot, "_gov10y_for_day", lambda *a: 0.02)
    try:
        day = date(2026, 9, 7)
        while day <= date(2026, 9, 18):
            anchors = state.anchors_from_record(current)
            bot.install_runtime_anchors(anchors)
            im = bot.build_live_trade_signal("IM", datetime.combine(day, datetime.min.time(), tzinfo=bot.BEIJING).replace(hour=18), mode="close")
            # IC is an independent unchanged control sleeve. Preserve its
            # audited quantities, but compute its actual quarter-roll plan.
            ic = deepcopy(template["IC"])
            held = anchors["IC"]["post_core_contract"]
            plan = bot.quarter_roll.roll_state("IC", held, ["IC2609", "IC2612", "IC2703"], day, True,
                         lambda c: bot._third_friday(*bot._contract_month(c)), bot._is_exchange_trading_day)
            ic.update(plan)
            ic.update(market_date=day, close_confirmed=True, market_phase="收盘后",
                      state_anchor_day=anchors["IC"]["last_verified_day"],
                      next_trade_date=bot._roll_forward_exchange_day(day + timedelta(days=1)),
                      momentum_current_weight=anchors["IC"]["verified_next_momentum_weight"],
                      momentum_next_weight=anchors["IC"]["verified_next_momentum_weight"],
                      grid_current=anchors["IC"]["verified_next_grid_units"],
                      grid_target=anchors["IC"]["verified_next_grid_units"])
            if day == date(2026, 9, 18):
                assert str(anchors["IM"]["verified_put_monthly_reset_date"]) == "2026-09-18"
                assert im["put_monthly_reset_already_recorded"] is True
                assert im["option_calendar_reset_due"] is True
                assert im["option_monthly_reset_due"] is False
                # Separate boundary: an expiring legacy leg must still be
                # replaced even when this month's regular event was recorded.
                expiring_anchors = deepcopy(anchors)
                expiring_anchors["IM"].update(post_core_put_contract="MO2609-P-7200",
                                               post_put_contract="MO2609-P-7200")
                bot.install_runtime_anchors(expiring_anchors)
                replacement = bot.build_live_trade_signal(
                    "IM", datetime(2026, 9, 18, 18, tzinfo=bot.BEIJING), mode="close"
                )
                assert replacement["option_monthly_reset_due"] is False
                assert replacement["core_put_action"] == "RESIZE_OR_ROLL"
                assert replacement["core_put_target_contract"] == "MO2612-P-8400"
                bot.install_runtime_anchors(anchors)
                for bogus_date, message in ((date(2026, 9, 18), "禁止重复维护"),
                                            (date(2026, 10, 16), "该月到期日")):
                    broken = deepcopy(im)
                    broken.update(option_monthly_reset_due=True,
                                  put_monthly_reset_execution_date=bogus_date,
                                  core_put_action="RESIZE_OR_ROLL", momentum_put_action="RESIZE_OR_ROLL")
                    with pytest.raises(RuntimeError, match=message):
                        store.append_confirmed_signals(current, {"IC": ic, "IM": broken})
                    assert store.load_latest()["digest"] == current["digest"]
            current = store.append_confirmed_signals(current, {"IC": ic, "IM": im})
            reloaded = store.load_latest()
            assert reloaded["digest"] == current["digest"]
            observed.append(dict(day=str(day), option_due=im["option_monthly_reset_due"],
                                 execution_date=str(im["put_monthly_reset_execution_date"]),
                                 action=im["core_put_action"],
                                 current=im["core_put_current_contract"], target=im["core_put_target_contract"],
                                 persisted=current["products"]["IM"]["post_core_put_contract"],
                                 momentum_action=im["momentum_put_action"],
                                 momentum_current=im["momentum_put_current_contract"],
                                 momentum_target=im["momentum_put_target_contract"],
                                 momentum_persisted=current["products"]["IM"]["post_momentum_put_contract"]))
            day = bot._roll_forward_exchange_day(day + timedelta(days=1))
        (tmp_path / "monthly_reset_trace.json").write_text(json.dumps(observed, indent=2), encoding="utf-8")
        last_two = observed[-2:]
        # Normal non-monthly days do not reselect the core Put; quantity and
        # valuation/MOM conditions are deliberately fixed throughout.
        for ordinary in observed[:-2]:
            assert ordinary["option_due"] is False
            assert ordinary["action"] == "HOLD"
            assert ordinary["current"] == ordinary["target"] == ordinary["persisted"] == "MO2612-P-7200"
        assert last_two[0]["execution_date"] == last_two[1]["execution_date"] == "2026-09-18"
        assert last_two[0]["persisted"] != last_two[0]["current"], last_two
        assert last_two[1]["persisted"] == last_two[0]["persisted"], (
            "Same monthly reset date caused two persisted strike replacements: " + json.dumps(last_two)
        )
        assert last_two[1]["action"] == "HOLD"
        if momentum_reentry:
            assert last_two[0]["momentum_persisted"] is None
            assert last_two[1]["momentum_action"] == "RESIZE_OR_ROLL"
            assert last_two[1]["momentum_persisted"] == "MO2612-P-8400"
        else:
            assert last_two[1]["momentum_action"] == "HOLD"
            assert last_two[1]["momentum_persisted"] == last_two[0]["momentum_persisted"]
    finally:
        bot.install_runtime_anchors(saved_anchors)
