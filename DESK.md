# The desk contract (this harness's own conventions)

Rendered natively from their own keys — no widget needed: `watchlist`
(live closes strip), `quotes/SYMBOL` (quote chips), `findings/<id>`
(evidence feed), `cases/<id>` (case cards, the positions table, and
every gate verdict). The desk header shows sync age, the autopilot's
last action, `desk/next_check`, and the `desk/audit` verdict.

Cards are context entries under `widgets/`. Kinds this desk's UI renders:

- candle/line shorthand: {"kind":"candle"|"line","title",
  "chart":{"symbol","days"?}} — the desk fetches EOD data itself and
  stamps the clock; preferred for standard charts.
- metric: {"kind":"metric","title","value","label"?,"detail"?}
- table:  {"kind":"table","title","columns":[...],"rows":[[...]]}
- text:   {"kind":"text","title","body"}
- line:   {"kind":"line","title","series":[{"name","points":[[x,y],...]}]}
- bar:    {"kind":"bar","title","bars":[{"label","value"}]}
- candle: {"kind":"candle","title","candles":[[date,o,h,l,c],...],
  "zones"?:[{"label","zone_type":"supply"|"demand","lower","upper"},...]} —
  zones draw as translucent bands behind the candles (≤16)
- scatter:{"kind":"scatter","title","points":[{"x","y","label"?,"size"?}]}
- relative_range: {"kind":"relative_range","title",
  "reference":{"label","value"},"rows":[{"label","value"},...]} —
  each row as % distance from the reference (strikes vs spot, zones vs close)
- connected_scatter: {"kind":"connected_scatter","title",
  "points":[{"x","y","label"?},...]} — a path through x/y in order;
  the newest point is emphasized
- streamgraph: {"kind":"streamgraph","title",
  "series":[{"name","points":[[x,y],...]},...]} — composition over time
- ladder: {"kind":"ladder","title","rungs":[{"label","value"}]}
- flow:   {"kind":"flow","title","nodes":[...],"links":[[i,j,value],...]}
- treemap:{"kind":"treemap","title","leaves":[{"label","value"}]}
- ask:    {"kind":"ask","title","question","options"?:["a","b"]} — a card
  that requests the member's input. Their answer lands at
  `answers/<card-id>` and triggers a revision turn: read it, adjust the
  desk, remove or update the ask card, and ask the next question if one
  matters. **Lead the way**: when direction is unclear or a fork
  matters, put an ask card up rather than guessing — the desk is a
  dialogue, not a delivery.

Shared fields: "pinned":true first; "size" compact|standard|wide|full;
"as_of" ISO clock on every data card you fill yourself. Explicit
refresh programs remain for full-engine data: "refresh" with a
value_path into the receipt — every tool answers at
`result.data.<field>` (bridged tools also keep the raw provider
envelope at `result.payload_json` if you need more than the answer).

Fill confirmations are program-backed ask cards with a fixed contract:
the card lives at `widgets/fill-<case-id>` and carries a `refresh`
program running `fill_check` (`value_path` `result.data`, `into`
`check`). This client renders the question and the
["Confirm fill", "Stand down"] buttons from `check.ask_card` **only**
while `check.verdict` is `fill-supported`; a refused or absent receipt
renders its reasons and no controls — a hand-composed fill card can
never collect an answer here. On the member's click this client records
the outcome itself: it writes the answer at `answers/fill-<case-id>` as
`{"choice", "receipt_observed_at"}` (binding the answer to the exact
receipt shown), on "Confirm fill" copies `check.fill` into the case
with `confirmed` set to the answer key and advances the state
(`-exit` cards write `exit` and close), and retires the card with a
null write. The answer key persists — a recorded fill's `confirmed`
field points at it, and the audit checks the clocks match.

`desk/next_check`, `desk/focus`, and `desk/autopilot` are the unassisted
loop's keys: the agent schedules itself with the first two; the
autopilot reports its last action in the third.

Cases render from `cases/<id>` directly (they are not widgets): state
badge, thesis, invalidation, evidence, and the fill with its receipted
market, clock, and confirmation. Gate verdicts come from `case_check`
receipts — a case that breaks the contract renders its violations in
red, and the autopilot writes the same audit to `desk/audit`.

House rules: one pinned `widgets/brief` Today card; curate toward ~12
widget cards and ~25 entries overall; clocks on data; failures named in
titles; the member's answers visible.
