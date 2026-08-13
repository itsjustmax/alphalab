#!/usr/bin/env python3
"""Bridge one AlphaLab operation into the Manifold tool contract.

Manifold runs this with the operation name as argv[1] and the arguments
as JSON on stdin; the AlphaLab provider (enrolled as exact local bytes)
validates and executes, and its bounded receipt is printed as JSON.

Inline operations (public data and the paper gates) run from engine/;
everything else crosses engine/bridge.py to the full local stack, whose
machine binding and environment variables are documented there.
"""

import json
import os
import sys

operation = sys.argv[1]
arguments = json.load(sys.stdin)

engine_home = os.environ.get(
    "ALPHALAB_INLINE_ENGINE",
    os.path.join(os.path.dirname(__file__), "..", "engine"),
)
sys.path.insert(0, os.path.abspath(engine_home))

INLINE = {"daily_bars", "price_summary", "capabilities", "trade_check",
          "case_check", "fill_check", "fill_watch", "live_quote", "live_quotes", "web_fetch", "form_check",
          "plan_check", "plan_library"}

# Manifest tool names alias their engine operations, so a direct caller
# using either name reaches the same place (agents tripped on this).
ALIASES = {
    "market_stream": "ibkr.market_stream",
    "quote_snapshot": "ibkr.quote.snapshot",
    "options_chain": "options.chain.snapshot",
    "spx_gamma": "spx.gamma.latest",
    "market_context": "market.context",
    "short_volume": "market.short_volume",
    "implied_move": "market.implied_move",
    "symbol_research": "market.symbol.research",
    "symbol_zones": "market.symbol.zones",
    "system_health": "system.health",
}

if operation in INLINE:
    import operations
    print(json.dumps(operations.run(operation, arguments)))
else:
    import bridge
    print(json.dumps(bridge.invoke(ALIASES.get(operation, operation), arguments)))
