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
- candle: {"kind":"candle","title","candles":[[date,o,h,l,c],...]}
- scatter:{"kind":"scatter","title","points":[{"x","y","label"?,"size"?}]}
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
value_path into the receipt — inline tools `result.data.<field>`,
full-engine tools `result.payload_json.answer.<field>`.

Fill confirmations are program-backed ask cards with a fixed contract:
the card lives at `widgets/fill-<case-id>` and carries a `refresh`
program running `fill_check` (`value_path` `result.data`, `into`
`check`). This client renders the question and the
["Confirm fill", "Stand down"] buttons from `check.ask_card` **only**
while `check.verdict` is `fill-supported`; a refused or absent receipt
renders its reasons and no controls — a hand-composed fill card can
never collect an answer here. The member's answer lands at
`answers/fill-<case-id>`. After acting on the answer, remove the card;
the answer key stays, because a recorded fill's `confirmed` field
points at it.

`desk/next_check`, `desk/focus`, and `desk/autopilot` are the unassisted
loop's keys: the agent schedules itself with the first two; the
autopilot reports its last action in the third.

Cases render from `cases/<id>` directly (they are not widgets): state
badge, thesis, invalidation, evidence, and the fill with its receipted
market, clock, and confirmation. Gate verdicts come from `case_check`
receipts — a case that breaks the contract renders its violations in
red, and the autopilot writes the same audit to `desk/audit`.

House rules: one pinned `widgets/brief` Today card; 6-10 cards; clocks
on data; failures named in titles; the member's answers visible.
