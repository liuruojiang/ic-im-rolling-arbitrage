import pytest
import requests
import poe_ic_im_mainline_v1_3_bot as bot


@pytest.mark.parametrize("mode", ["intraday", "close"])
def test_transport_retry_only_failed_product(monkeypatch, mode):
    calls = []
    def build(product, mode):
        calls.append(product)
        if len(calls) == 1:
            raise requests.ConnectionError("remote disconnected")
        return {"product": product, "data_notes": []}
    monkeypatch.setattr(bot, "build_live_trade_signal", build)
    result = bot.build_live_signal_with_transport_retry("IC", mode, 45.0)
    assert calls == ["IC", "IC"]
    assert result["data_notes"]


@pytest.mark.parametrize("error,count", [(ValueError("stale date"), 1),
                                          (requests.Timeout("offline"), 2)])
def test_invalid_data_never_retried_and_transport_bounded(monkeypatch, error, count):
    calls = []
    def build(product, mode):
        calls.append(product)
        raise error
    monkeypatch.setattr(bot, "build_live_trade_signal", build)
    with pytest.raises(type(error)):
        bot.build_live_signal_with_transport_retry("IM", "intraday", 22.5)
    assert len(calls) == count


@pytest.mark.parametrize("mode", ["realtime", "close"])
def test_expected_date_guard_precedes_ledger_catchup(monkeypatch, tmp_path, mode):
    from datetime import datetime
    import run_ic_im_v1_3_github_digest as runner
    class Coordinator:
        def catch_up_until_current(self, *a, **kw):
            pytest.fail("must reject mismatched date before ledger mutation")
    monkeypatch.setattr(runner, "StateStore", lambda root: object())
    monkeypatch.setattr(runner, "LedgerCoordinator", lambda store: Coordinator())
    with pytest.raises(RuntimeError, match="日报日期不匹配"):
        runner.build_artifacts(
            state_dir=tmp_path, out_dir=tmp_path,
            clock=datetime(2026, 9, 4, 14, 30, tzinfo=runner.strategy.BEIJING),
            max_sessions=20, mode=mode, expected_market_date="2026-09-07",
        )


def test_incomplete_artifact_keeps_product_diagnostic(monkeypatch, tmp_path):
    from datetime import datetime
    import run_ic_im_v1_3_github_digest as runner
    record = {"verified_day": "2026-09-03", "sequence": 1, "digest": "same"}
    class Store:
        def load_latest(self):
            return dict(record)
    class Coordinator:
        def catch_up_until_current(self, *a, **kw):
            return 0
        def health(self, *a):
            return {"status": "ok", **record}
        def execute_query(self, *a, **kw):
            assert kw["persist_confirmed"] is False
            return "IC 完整信号失败：ConnectionError: disconnected\n", [], {"IM": {}}
    monkeypatch.setattr(runner, "StateStore", lambda root: Store())
    monkeypatch.setattr(runner, "LedgerCoordinator", lambda store: Coordinator())
    with pytest.raises(RuntimeError, match="已取得=IM.*ConnectionError"):
        runner.build_artifacts(state_dir=tmp_path, out_dir=tmp_path,
            clock=datetime(2026,9,4,14,30,tzinfo=runner.strategy.BEIJING),
            max_sessions=20, mode="realtime")
    assert "disconnected" in (tmp_path / "diagnostic_report.md").read_text(encoding="utf-8")
    assert not (tmp_path / "ic_im_v1_3_realtime_signal.md").exists()
