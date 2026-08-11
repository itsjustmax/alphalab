#!/usr/bin/env python3
"""Bridge one AlphaLab operation into the Manifold tool contract.

Manifold runs this with the operation name as argv[1] and the arguments
as JSON on stdin; the AlphaLab provider (enrolled as exact local bytes)
validates and executes, and its bounded receipt is printed as JSON.

Machine binding comes from the environment, with defaults for this Mac:
  ALPHALAB_AGENTS_REPO   the AlphalabAgents checkout
  ALPHALAB_BRIDGE_REPO   the manifold-harness-alphalab checkout
  ALPHALAB_HOME          the configured AlphaLab home (credentials, cache)
"""

import json
import os
import subprocess
import sys

AGENTS = os.environ.get("ALPHALAB_AGENTS_REPO", "/Users/max/Bots/AlphalabAgents")
BRIDGE = os.environ.get("ALPHALAB_BRIDGE_REPO", "/Users/max/Bots/manifold-harness-alphalab")
HOME = os.environ.get(
    "ALPHALAB_HOME", "/Users/max/Bots/manifold-dash/.manifold-dash/alphalab"
)

operation = sys.argv[1]
arguments = json.load(sys.stdin)

INLINE = {"daily_bars", "price_summary", "capabilities"}
if operation in INLINE:
    engine_home = os.environ.get(
        "ALPHALAB_INLINE_ENGINE",
        os.path.join(os.path.dirname(__file__), "..", "engine"),
    )
    sys.path.insert(0, os.path.abspath(engine_home))
    import operations
    print(json.dumps(operations.run(operation, arguments)))
    sys.exit(0)
request = {
    "tool_id": f"external.alphalab.{operation}",
    "operation": operation,
    "arguments": arguments,
}
environment = dict(os.environ)
environment["PYTHONPATH"] = f"{AGENTS}/packages/alphalab-agents/src"
environment["ALPHALAB_HOME"] = HOME
result = subprocess.run(
    [f"{AGENTS}/.venv/bin/python", f"{BRIDGE}/provider/alphalab_manifold_provider.py", "invoke"],
    input=json.dumps(request),
    capture_output=True,
    text=True,
    timeout=110,
    env=environment,
)
if result.returncode != 0:
    print(json.dumps({"ok": False, "error": result.stderr.strip()[:1500]}))
    sys.exit(0)
print(result.stdout)
