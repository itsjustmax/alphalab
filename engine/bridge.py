"""One bridge to the full engine, shared by the tool runner and the gates.

The full AlphaLab engine (broker quotes, options chains, dealer gamma)
lives outside this folder; this module is the single place that knows how
to reach it. Every caller gets the same honesty: an unreachable engine is
a named failure, never a silent gap. Machine binding comes from the
environment, with defaults for this Mac:

  ALPHALAB_AGENTS_REPO   the AlphalabAgents checkout
  ALPHALAB_BRIDGE_REPO   the manifold-harness-alphalab checkout
  ALPHALAB_HOME          the configured AlphaLab home (credentials, cache)
"""

import json
import os
import subprocess


def _agents() -> str:
    return os.environ.get("ALPHALAB_AGENTS_REPO", "/Users/max/Bots/AlphalabAgents")


def _bridge() -> str:
    return os.environ.get(
        "ALPHALAB_BRIDGE_REPO", "/Users/max/Bots/manifold-harness-alphalab"
    )


def _home() -> str:
    return os.environ.get(
        "ALPHALAB_HOME", "/Users/max/Bots/manifold-dash/.manifold-dash/alphalab"
    )


def available() -> bool:
    return (
        os.path.isfile(f"{_agents()}/.venv/bin/python")
        and os.path.isfile(f"{_bridge()}/provider/alphalab_manifold_provider.py")
    )


def invoke(operation, arguments, timeout=110):
    """Run one full-engine operation; always answers a dict, never raises."""

    request = {
        "tool_id": f"external.alphalab.{operation}",
        "operation": operation,
        "arguments": arguments,
    }
    environment = dict(os.environ)
    environment["PYTHONPATH"] = f"{_agents()}/packages/alphalab-agents/src"
    environment["ALPHALAB_HOME"] = _home()
    try:
        result = subprocess.run(
            [
                f"{_agents()}/.venv/bin/python",
                f"{_bridge()}/provider/alphalab_manifold_provider.py",
                "invoke",
            ],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
        )
    except Exception as error:
        return {"ok": False, "error": str(error)[:500]}
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip()[:1500]}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "the provider answered with non-JSON"}
