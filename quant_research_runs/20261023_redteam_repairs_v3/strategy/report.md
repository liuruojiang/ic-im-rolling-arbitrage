# STRAT-01 repair evidence

Required fresh decay observations now fail closed when absent or invalid. The adapter binds price to held contract and signal day; new ledger writes independently check the projected pre-close holding, so forged roll-pending or target-action fields cannot evade the guard. Historical hash-verified records retain their original read contract. Entry/roll target fields are not mistaken for an old held leg.

IC historical-close fetch can require a day and fall back when the primary series is valid but late. The installed evidence wrapper forwards this requirement and continues to use existing original chain keys. Saved real 2026-10-22 option 10012149 returns 0.2614 under both API forms with identical evidence lookup calls; wrong requested day rejects.

Final targeted run06: 227 passed, 4 original historical deselections, 0 failures. Includes 58 new cases. Earlier run05: 407 passed. Original failures and compatibility corrections remain in run01–06 and repair_report.json. Shared synthetic fixtures now explicitly supply unchanged held prices with date/identity; original randomized missing inputs remain and now assert rejection. No new exclusions.

Independent audit and final bound cumulative gate belong to the parent task.
