# The desk contract (this harness's own conventions)

Cards are context entries under `widgets/`. Kinds this desk's UI renders:

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
"as_of" ISO clock on every data card. Live cards: "refresh" with
value_path into this desk's receipts — inline tools
`result.data.<field>` (a line card: value_path `result.data.points`,
into `series.0.points`; a candle card: value_path
`result.data.candles`, into `candles`); full-engine tools
`result.payload_json.answer.<field>`.

Fill confirmations are ask cards with a fixed contract: the card lives at
`widgets/fill-<case-id>`, its body is the `ask_card` a passing
`fill_check` receipt answered with (receipted market and clock included,
options exactly ["Confirm fill", "Stand down"]), and the member's answer
lands at `answers/fill-<case-id>`. Never compose one by hand — the only
sanctioned card is the one the receipt produced. After acting on the
answer, remove the card; the answer key stays, because a recorded fill's
`confirmed` field points at it.

Cases render from `cases/<id>` directly (they are not widgets): state
badge, thesis, invalidation, evidence, and the fill with its receipted
market, clock, and confirmation. A case that breaks the contract renders
its violations in red — run `case_check` before writing to keep the desk
clean.

House rules: one pinned `widgets/brief` Today card; 6-10 cards; clocks
on data; failures named in titles; the member's answers visible.
