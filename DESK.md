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

Working paper orders are program-backed cards with a fixed contract:
`widgets/fill-<case-id>` (or `…-exit`), kind "order", carrying a
`refresh` program running `fill_check` (`value_path` `result.data`,
`into` `check`). This client renders the order's intent from the
program args and its live status from `check`: armed (no receipt yet),
supported (green — the live market contains the price), or refused
(the reasons, in red). No buttons — paper fills take no member
confirmation. The autopilot records a fresh supported order
mechanically: `check.fill` lands in the case verbatim, the state
advances, the card retires, and the recording is announced in the
conversation. Asks (kind "ask") are reserved for the member's own
decisions: direction, risk, what to pursue or drop.

`desk/next_check`, `desk/focus`, and `desk/autopilot` are the unassisted
loop's keys: the agent schedules itself with the first two; the
autopilot reports its last action in the third.

Every submit control carries a model selector (the platform's model
router, GET /models): the member picks which model runs that task — a
deep one to build the desk, an inexpensive one for a quick question.
The autopilot's scheduled turns default to sonnet (--model). Register
more models in ~/.manifold/models.json — any terminal-invocable
command.

Cases render from `cases/<id>` directly (they are not widgets): state
badge, thesis, invalidation, evidence, and the fill with its receipted
market, clock, and confirmation. Gate verdicts come from `case_check`
receipts — a case that breaks the contract renders its violations in
red, and the autopilot writes the same audit to `desk/audit`.

House rules: one pinned `widgets/brief` Today card; curate toward ~12
widget cards and ~25 entries overall; clocks on data; failures named in
titles; the member's answers visible.
