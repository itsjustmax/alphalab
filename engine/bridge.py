"""One bridge to the full engine, shared by the tool runner and the gates.

The full AlphaLab engine (broker quotes, streams, options, dealer gamma)
lives in its own repository with its own Python; this module runs the
harness's adapter (engine/adapter.py — harness bytes, carrying the
operation allowlist) with that Python. One hop: harness → Agent API.
Every caller gets the same honesty: an unreachable engine is a named
failure, never a silent gap. Machine binding comes from the environment,
with defaults for this Mac:

  ALPHALAB_AGENTS_REPO   the AlphalabAgents checkout (engine + venv)
  ALPHALAB_HOME          the configured AlphaLab home (credentials, cache)
"""

import json
import os
import subprocess


def _agents() -> str:
    return os.environ.get("ALPHALAB_AGENTS_REPO", "/Users/max/Bots/AlphalabAgents")


def _home() -> str:
    return os.environ.get(
        "ALPHALAB_HOME", "/Users/max/Bots/manifold-dash/.manifold-dash/alphalab"
    )


def _engine_python() -> str:
    return f"{_agents()}/.venv/bin/python"


def available() -> bool:
    return (
        os.path.isfile(_engine_python())
        and os.path.isdir(f"{_agents()}/packages/alphalab-agents/src/alphalab_agents")
    )


def invoke(operation, arguments, timeout=110):
    """Run one full-engine operation; always answers a dict, never raises."""

    adapter = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adapter.py")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = f"{_agents()}/packages/alphalab-agents/src"
    environment["ALPHALAB_HOME"] = _home()
    try:
        result = subprocess.run(
            [_engine_python(), adapter],
            input=json.dumps({"operation": operation, "arguments": arguments}),
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
        return normalize(json.loads(result.stdout))
    except json.JSONDecodeError:
        return {"ok": False, "error": "the engine answered with non-JSON"}


def normalize(reply):
    """One receipt shape for every lane.

    The Agent API answers a native envelope (answer + rows); the old
    provider wrapped the same thing in a payload_json string. Lift either
    to `data` so `result.data.<field>` works on every tool — value_path
    guessing was the single biggest agent friction in trials.
    """

    if not isinstance(reply, dict) or "data" in reply:
        return reply
    answer = reply.get("answer")
    if isinstance(answer, dict):
        reply["data"] = dict(answer)
        rows = reply.get("rows")
        if isinstance(rows, list) and "rows" not in reply["data"]:
            reply["data"]["rows"] = rows
        return reply
    try:
        payload = json.loads(reply.get("payload_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        payload = {}
    answer = payload.get("answer") if isinstance(payload, dict) else None
    if isinstance(answer, dict):
        reply["data"] = dict(answer)
        rows = payload.get("rows")
        if isinstance(rows, list) and "rows" not in reply["data"]:
            reply["data"]["rows"] = rows
    return reply
