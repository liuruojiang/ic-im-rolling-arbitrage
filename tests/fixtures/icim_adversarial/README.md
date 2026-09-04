# Real accepted ledger fixtures

Source: successful GitHub run 33850626309, artifact `ic-im-v1-3-r7-ledger`, downloaded during the 2026-09-04 acceptance.

- `previous.json`: immutable migration genesis for 2026-09-03.
- `confirmed.json`: committed 2026-09-04 complete IC/IM record.

These are copied without changing fields or digest. Tests mutate deep copies only and never write the production ledger. Research signal data, not account holdings or order authorization.
