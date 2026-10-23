# MKT-01 / MKT-02 repair results

Implemented exact whole-response Tencent variable identity, unique response and embedded-code check. EastMoney now requests f57/f58 and requires the exact f57 code; malformed identity, timestamp and numeric data fail closed. Invalid Tencent responses may fall back to valid EastMoney; cancellation remains propagated.

Both providers validate full timestamps on the requested signal day against advancing receipt time. Existing 09:15 cash quote lower bound and 15:00 close bound apply, with no added quote-age cutoff. Lunch observations can retain11:30. live_proxy validates timestamps again before either append or replacement of current-day history, while explicit clock overrides now govern the quote request. Completed daily/replay paths retain their OHLCV source. A preclose query may finish after15:00 when the timestamp was actually received by then.

83 new regression tests plus the existing EastMoney timestamp test pass: run03, 84 passed, 0 failed/skipped, 17.70 seconds. Quote packets/clocks are fault injections; original saved IC OHLCV is consumed in the public live_proxy tests. Wrong identity no longer changes momentum because it is rejected before calculation.

The old timestamp fixture was missing newly required f57 and had no fixed runtime. It now provides f57=000905 and runtime_clock(stamp), with both original assertions unchanged. Backup and precise diff are retained. run01 records the original fixture failure. run02 records two known historical deselections and an expiry-candidate status mismatch sent to the strategy agent/parent; no test was excluded by this task.

No network or formal state mutation. Independent audit and final source-bound cumulative/daily/real-replay verification remain parent responsibilities. Existing named market.live archive replay bypasses the original live_proxy by design; this patch does not claim re-verifying a fabricated historical intraday archive's original variable identity.
