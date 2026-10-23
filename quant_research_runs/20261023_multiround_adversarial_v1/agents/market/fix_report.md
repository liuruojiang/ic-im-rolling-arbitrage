# Market and consumed-row timestamp fix

Only `poe_ic_im_mainline_v1_4_bot.py` was edited by this agent. The implementation is directly in the v1.4 module, not delegated to v1.3. The initial frozen backup SHA-256 is `698d0c5f6637d90deb18996c69c009151ea697d2bacda769fc5d5f4e21a3c96b`. This agent's completed fixed revision is `b390575a41fe11da6da68caee551b7827f9cee28fb8ceba19432ec661e775222`. No other production module, existing test, existing fixture, calendar, strategy parameter or formal ledger was changed by this agent.

## Changes

1. SSE observed expiry months now share Sina's existing current-listing shape check. A rejected SSE set can reach the verified fallback; no contract month is manufactured. The safe expiry-day rejection when a source prematurely removes the expiring month remains.
2. Exact quote-variable extraction requires an identifier left boundary, rejecting a namespace prefix embedded in another variable name.
3. SSE JSON uses streaming bounded bytes before JSON decoding and closes the response in `finally`. The same size budget and collection deadline helper apply as to existing bounded quote readers.
4. MO Sina price cross-check extracts exactly one requested variable and rejects unrequested/ambiguous symbols before field parsing.
5. Every MO cross-check price basis rejects a timestamp after the observation clock. The existing historical source-date allowance remains; ordinary last-trade quotes have not acquired a new 20-minute freshness rule.
6. The Sina typed-chain timestamp issue found by the clocks agent is fixed at consumption, with an explicit source passed into the two selected-row checks and three IC `_quote_row` calls. A confirmed Sina row without `source_stamp` cannot be consumed. SSE aggregate rows retain their established lack-of-row-time compatibility. No adapter or DataFrame-attribute change is needed, and unused stale rows remain present rather than being dropped or rejecting the whole chain.

Exact patch against the frozen original: `agents/market/market_fix.patch`.

## Verification

`market_after_01`: **55 passed, 0 failed/errors/skipped** (the same frozen market 36 and clocks 19 cases). All 28 production modules and both tests were bound before and after; all 30 hashes were identical during the run. No network or formal ledger writes were used. See `market_after_01/market_after_binding_receipt.json`, `regression.xml` and `regression.txt`.

The 11 original market counterexamples and two clocks counterexamples now pass. The unchanged real MO identity control, SSE aggregate compatibility, unused stale row, no skipping a stale nearest strike, exact active-minute freshness boundaries, cache reread guards, typed evidence/raw replay controls, and exact wire parameter semantics continue to pass.

`run_after_market.py` independently checks that the test hashes equal the original `market_03` and `agents/clocks/before_02` hashes, as well as recording the current production revision before/after. The original-code runner and receipts remain unchanged.

## Remaining validation boundaries

This agent has not adapted any old fixtures. Older SSE mocks that implement only `.json()` and no raw bytes may need a declared test-fixture update for the bounded wire contract; do not add a production bypass merely to satisfy such mocks. The coordinator is responsible for full-suite compatibility and real original-package equivalence after all agents' patches are combined.

The 55-case verification proves only the tested acquisition and timing contracts. No external market event, actual stale SSE response, malicious upstream response, or historical portfolio discrepancy is asserted. No additional acquisition optimization is included.
