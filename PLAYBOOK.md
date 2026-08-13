# The AlphaLab playbook: idea → durable live trade

The atomic trade is one thesis carried by primitives at every step.
This is the canonical path; the audit enforces the links.

## 1. Idea
Say it to the desk (ask-the-desk, or the watchlist composer for
name/news-driven ideas). The desk stages `trades/<id>` through the
forms lane: thesis, invalidation written at birth, evidence keys, and
one to five CANDIDATE CONTRACTS — the ways the thesis can be
expressed (different strikes, sides, even underlyings).

## 2. Data
The trade's cockpit (click its sparkline, or the create button) is its
micro-dashboard: the tape (every tick, persisted in Timescale, pushed
live), premium and underlying candles, cockpit/<id>/ cells for
candidates and comparisons, overlays (drawn or COMPUTED — agent code
with a bounded mouth) — each cell an atomic program refreshing its own
data on its own clock. Stream health is watched and healed; staleness
is always visible, never silent.

## 3. Plan
The member describes management in plain words (create button →
cockpit composer). The desk compiles the BOT: manage(inputs, state) —
declared read-only inputs (ticks, gamma, bars, quotes), entry arming
(arm_entry names the candidate), and the bot contract: while holding,
it MUST publish market {stop, target}. Tests ship with the program.
A requested plan forces the desk's next turn.

## 4. Activate
The member inspects (explanation, code, live test verdicts) and
presses activate — the only hand that can. The runner refuses
agent-written actives.

## 5. Execute (paper, always)
Deterministic code, never an agent in the loop: the runner maintains
the bracket order from the bot's market; the tick executor checks the
levels against every persisted tick twice a second and fills through
the market gate at the crossing tick. Entries and exits append to the
trade's EXECUTIONS history; with the bot live, an exit returns the
thesis to watching and the bot re-arms. Retire closes the thesis.

## 6. Execute (live, when armed)
The same bracket flows to Interactive Brokers through the live rail —
but only when EVERY layer agrees, each one deterministic and
member-held:
  1. per-trade arming        — tools/arm_live.py, your terminal only
  2. ~/.alphalab/risk.json   — live_enabled, caps, whitelist, kill
  3. engine env master gate  — ALPHALAB_LIVE_OPTION_ORDERS_ENABLED,
                               dedicated-account ALLOWLIST
  4. the engine's per-call gate — options-only, premium/quantity caps,
                               confirmation phrase, FAIL-CLOSED daily
                               budget (failed submits count until
                               reconciled)
No duplicate orders (the journal refuses), no equities (rejected at
two layers), stop moves are ibkr.orders.modify_stop — a real stop,
really moved, same client lease. Anything refused is journaled with
its reasons; anything below full agreement runs as dry_run, so the
whole chain rehearses without money.

## 7. Watch
The cockpit is the instrument: the tape crawls, the bot's market draws
on it, the plan strip shows state and the runner's narration, every
surface has a fold button and a textual twin so agents reason over
exactly what the member sees.

## 8. Verdict
Cycles accumulate in `executions`; realized PnL per cycle; the plan
library archives every activated plan with its judged outcome. Kill or
promote explicitly — a named death teaches.
