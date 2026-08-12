"""One bridge to the full engine, shared by the tool runner and the gates.

The full AlphaLab engine (broker quotes, streams, options, dealer gamma)
lives in its own repository with its own Python; this module runs the
harness's adapter (engine/adapter.py — harness bytes, carrying the
operation allowlist) with that Python. One hop: harness → Agent API.
Every caller gets the same honesty: an unreachable engine is a named
failure, never a silent gap.

Machine binding lives OUTSIDE the harness bytes, resolved in order:

  1. ~/.alphalab/engine.json (or $ALPHALAB_ENGINE_CONFIG):
     {"python": ..., "package_src": ..., "home": ...}
     — written by tools/install_engine.py.
  2. Legacy environment: ALPHALAB_AGENTS_REPO (+ ALPHALAB_HOME).

No binding, no engine — inline tools still answer, and capabilities
names the installer.
"""

import json
import os
import subprocess


def _config_path() -> str:
    return os.environ.get(
        "ALPHALAB_ENGINE_CONFIG", os.path.expanduser("~/.alphalab/engine.json")
    )


def binding():
    """The engine binding for this machine, or None."""

    path = _config_path()
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as handle:
                stored = json.load(handle)
            if isinstance(stored, dict) and stored.get("python"):
                return {
                    "python": str(stored["python"]),
                    "package_src": str(stored.get("package_src") or ""),
                    "home": str(stored.get("home") or ""),
                }
        except (json.JSONDecodeError, OSError):
            pass
    repo = os.environ.get("ALPHALAB_AGENTS_REPO", "").strip()
    if repo:
        return {
            "python": f"{repo}/.venv/bin/python",
            "package_src": f"{repo}/packages/alphalab-agents/src",
            "home": os.environ.get("ALPHALAB_HOME", "").strip(),
        }
    return None


def available() -> bool:
    bound = binding()
    return bool(
        bound
        and os.path.isfile(bound["python"])
        and (not bound["package_src"] or os.path.isdir(bound["package_src"]))
    )


def invoke(operation, arguments, timeout=110):
    """Run one full-engine operation; always answers a dict, never raises."""

    bound = binding()
    if not bound:
        return {"ok": False,
                "error": "no engine binding — run tools/install_engine.py"}
    adapter = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adapter.py")
    environment = dict(os.environ)
    if bound["package_src"]:
        environment["PYTHONPATH"] = bound["package_src"]
        environment["ALPHALAB_PACKAGE_SRC"] = bound["package_src"]
    if bound["home"]:
        environment["ALPHALAB_HOME"] = bound["home"]
    try:
        result = subprocess.run(
            [bound["python"], adapter],
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
