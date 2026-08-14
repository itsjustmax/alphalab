Pick candidate CONTRACTS for one trade from receipts. The arguments
carry `trade_id` and optionally `premium_band` (e.g. "1-2") and
`expiry_hint`. The trade's thesis and invalidation are in your slice.

Do:
1. Read the chain and quote receipts in your slice for the trade's
   symbol. No receipt → write no candidate; say exactly which receipt
   is missing (a chain scan card the member can run, a market-hours
   constraint).
2. Choose 1-3 candidates that fit the band and the desk's premium
   ceiling: near-dated enough to move, liquid enough to exit.
3. Write the comparison as a visible cell:
   `cockpit/<trade_id>/scan` — a table of the candidates: strike,
   last/close premium, vs-spot distance, verdict.
4. A premium-history candle per serious candidate:
   `cockpit/<trade_id>/scan-<strike>` {"kind": "candle", "chart":
   {"contract": "...", "bar_size": "5 mins", "duration": "2 D"}}.
5. Update `trades/<trade_id>.contracts` with the chosen candidates —
   receipts only, never invented strikes.

HANDOFF: the member reviews candidates in the cockpit; stage-trade or
the member's own press advances one to entry work. Your say names the
pick and the runner-up in one sentence each.
