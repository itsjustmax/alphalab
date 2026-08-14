Stage ONE trade idea through the forms lane. The task arguments carry
`symbol` and optionally `driver` (the receipted reason this idea exists
now). Your slice holds the symbol's quotes, findings, and news.

Write exactly one card: `forms/trade/<short-id>` with
`{"contracts": ["<SYMBOL> <YYYYMMDD> <strike><C|P>"], "thesis": "...",
"invalidation": "..."}`.

Rules: the contract must come from a receipt in your slice (a chain
scan finding, a quoted contract) — never invent a strike. The thesis
names the driver and its clock. The invalidation is a price or event
that would prove the idea wrong, stated so a program could check it.
If your slice holds no receipt that supports a contract, write no card
and say exactly what receipt is missing.
