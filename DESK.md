# The desk contract (this harness's own conventions)

Rendered natively from their own keys — no widget needed: `watchlist`
(live closes strip), `quotes/SYMBOL` (quote chips), `findings/<id>`
(evidence feed), `trades/<id>` (trade cards with contract chips, the
positions table, and every gate verdict). Every cell carries the fold
chip — a focused conversation scoped to that cell's data alone, on the
member's pick of model. The desk header shows sync age, the autopilot's
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
- custom_d3: {"kind":"custom_d3","title","data":<any JSON>,
  "render":"a JS function body receiving (d3, data, svg, width, height)",
  "height"?} — an agent-authored chart, run in a sandboxed frame (unique
  origin: no token, no desk, no parent page). When the member wants a
  chart this vocabulary lacks — even one they saw elsewhere — learn from
  their link: wire a card with refresh {tool: "web_fetch", args: {url}},
  read the landed text (script bodies included) next turn, then author
  the chart here with your own data.
- slider: {"kind":"slider","title","label"?,"min","max","step","value","unit"?}
  — interactive; the member's setting lands at `answers/<card-id>`
- choices:{"kind":"choices","title","question"?,"options":[...]} — one tap,
  same landing key
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
`widgets/fill-<trade-id>` (or `…-exit`), kind "order", carrying a
`refresh` program running `fill_watch` (`value_path` `result.data`,
`into` `check`) — a standing reqMktData stream per contract, gated
tick by tick; the autopilot also watches each armed order live and
releases the stream when the order resolves. This client renders the order's intent from the
program args and its live status from `check`: armed (no receipt yet),
supported (green — the live market contains the price), or refused
(the reasons, in red). No buttons — paper fills take no member
confirmation. The autopilot records a fresh supported order
mechanically: `check.fill` lands in the case verbatim, the state
advances, the card retires, and the recording is announced in the
conversation. Asks (kind "ask") are reserved for the member's own
decisions: direction, risk, what to pursue or drop.

The Position management section renders one row per open position from
`plans/<trade-id>`: the member's plan words, its status, and the
program's live state. The member requests a plan in their own words
(status "requested" — the desk compiles it to a draft program), then
inspects it in a modal — explanation, declared inputs, the decision
code, and `plan_check`'s live test verdicts — before the activate
button will run it. Activate/pause are member-only moves made here;
the autopilot runs active plans each pass and routes their actions
through the same order cards and market gate as everything else. The
positions table marks every open position live from its own contract's
stream: PnL from entry at the bid — the liquidation side — with the
tick's clock in the tooltip.

`desk/member_turn` is stamped by this client whenever the member fires a
turn — the autopilot yields to it. `desk/next_check`, `desk/focus`, and `desk/autopilot` are the unassisted
loop's keys: the agent schedules itself with the first two; the
autopilot reports its last action in the third.

Every submit control carries a model selector (the platform's model
router, GET /models): the member picks which model runs that task — a
deep one to build the desk, an inexpensive one for a quick question.
The autopilot's scheduled turns default to sonnet (--model). Register
more models in ~/.manifold/models.json — any terminal-invocable
command.

Trades render from `trades/<id>` directly (they are not widgets):
state badge, contract chips, thesis, invalidation, evidence, and each
fill with its receipted market and clock. Gate verdicts come from
`trade_check` receipts — a trade that breaks the contract renders its
violations in red, and the autopilot writes the same audit to
`desk/audit`.

House rules: one pinned `widgets/brief` Today card; curate toward ~12
widget cards and ~25 entries overall; clocks on data; failures named in
titles; the member's answers visible.
