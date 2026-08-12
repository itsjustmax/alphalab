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

INLINE = {"daily_bars", "price_summary", "capabilities", "case_check", "fill_check"}
if operation in INLINE:
    import operations
    print(json.dumps(operations.run(operation, arguments)))
else:
    import bridge
    print(json.dumps(bridge.invoke(operation, arguments)))
