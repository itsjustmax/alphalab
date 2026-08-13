#!/usr/bin/env python3
"""The tick relay: Timescale's tick stream, pushed to the desk at 10Hz.

The tool lane spawns a process per call — right for receipts, hopeless
for a live tape. This relay keeps the engine loaded (one import, then
in-process reads), polls the persisted tick store (source_pool.
quote_ticks — every tick every stream prints) ten times a second, and
pushes NEW ticks to the browser as Server-Sent Events.

Localhost is the boundary: the relay binds 127.0.0.1 only, and every
request must carry the Manifold service token. Run it with the ENGINE
python (the AlphalabAgents venv):

  ~/.alphalab engine python tools/tick_relay.py [--port 8643]

GET /tape?symbol=NVDA&sec_type=OPT&expiration=20260821&strike=235
    &right=C&token=<service token>
  → SSE stream, one JSON tick per event: {bid, ask, last, quote_time}
"""

import json
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

POLL_SECONDS = 0.1
BATCH = 40

_HOME = None
_RUN = None
_LOCK = threading.Lock()


def _engine():
    """Import the engine once; every later read is in-process."""

    global _HOME, _RUN
    if _RUN is not None:
        return _RUN, _HOME
    from alphalab_agents.api.operations import run_api_operation
    from alphalab_agents.paths import resolve_alphalab_home

    raw = os.environ.get("ALPHALAB_HOME", "").strip()
    _HOME = resolve_alphalab_home(Path(raw).resolve(),
                                  allow_internal=True) if raw else None
    _RUN = run_api_operation
    return _RUN, _HOME


def _service_token():
    record = Path("~/.manifold/service.json").expanduser()
    try:
        return json.loads(record.read_text())["token"]
    except Exception:
        return None


def latest_ticks(arguments, limit=BATCH):
    run, home = _engine()
    with _LOCK:  # the engine's DB session is not proven thread-safe
        reply = run("ibkr.market_stream",
                    {**arguments, "action": "latest", "limit": limit},
                    home=home, surface="tick-relay")
    rows = reply.get("rows") or []
    if not rows and isinstance(reply.get("answer"), dict):
        rows = reply["answer"].get("rows") or []
    return rows


def tick_key(row):
    return (str(row.get("quote_time")), row.get("bid"), row.get("ask"),
            row.get("last"))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/tape":
            self.send_response(404)
            self.end_headers()
            return
        query = {k: v[0] for k, v in
                 urllib.parse.parse_qs(parsed.query).items()}
        expected = _service_token()
        if not expected or query.get("token") != expected:
            self.send_response(401)
            self.end_headers()
            return
        arguments = {k: query[k] for k in
                     ("symbol", "sec_type", "expiration", "strike", "right")
                     if query.get(k)}
        if "strike" in arguments:
            arguments["strike"] = float(arguments["strike"])
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        seen = set()
        # first frame: the recent window, chronological, so the tape
        # starts full instead of empty
        try:
            backlog = list(reversed(latest_ticks(arguments, limit=200)))
            for row in backlog:
                seen.add(tick_key(row))
            self.wfile.write(
                ("data: " + json.dumps({"backlog": [
                    {k: row.get(k) for k in
                     ("bid", "ask", "last", "quote_time")}
                    for row in backlog]}) + "\n\n").encode())
            self.wfile.flush()
            while True:
                for row in reversed(latest_ticks(arguments)):
                    key = tick_key(row)
                    if key in seen:
                        continue
                    seen.add(key)
                    if len(seen) > 4000:
                        seen.clear()  # bounded memory; dupes re-dedupe
                        seen.add(key)
                    self.wfile.write(
                        ("data: " + json.dumps({k: row.get(k) for k in
                         ("bid", "ask", "last", "quote_time")})
                         + "\n\n").encode())
                self.wfile.flush()
                time.sleep(POLL_SECONDS)
        except (BrokenPipeError, ConnectionResetError):
            return


# ---- The executor: brackets acted on at tick speed ------------------
# The bot publishes the market (stop/target on the bracket card); this
# thread watches the freshest persisted tick against those levels twice
# a second, and the moment a leg is crossed it runs the GATE in-process
# — capturing the execution at the crossing tick, not thirty seconds
# later — and writes the receipt onto the card. The autopilot then
# transcribes the recording exactly as it does for every fill.

import urllib.request

SERVICE = "http://localhost:8642"
EXECUTOR_SECONDS = 0.5
BRACKET_REFRESH_SECONDS = 3.0


def _service_api(method, path, body=None):
    token = _service_token()
    request = urllib.request.Request(
        SERVICE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Manifold-Token": token or "",
                 "Content-Type": "application/json"},
        method=method)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read())


def _harness_gates():
    import sys
    engine_dir = str(Path(__file__).resolve().parent.parent / "engine")
    if engine_dir not in sys.path:
        sys.path.insert(0, engine_dir)
    import gates
    return gates


def _in_process_invoke(operation, arguments, timeout=None):
    run, home = _engine()
    with _LOCK:
        return run(operation, dict(arguments), home=home,
                   surface="tick-relay-executor")


def executor():
    gates = _harness_gates()
    brackets = {}
    last_refresh = 0.0
    fired = set()
    while True:
        try:
            now = time.time()
            if now - last_refresh >= BRACKET_REFRESH_SECONDS:
                last_refresh = now
                found = {}
                for env in _service_api("GET", "/environments"):
                    if env.get("harness") != "AlphaLab":
                        continue
                    view = _service_api(
                        "GET", f"/environments/{env['environment_id']}")
                    for key, card in (view.get("context") or {}).items():
                        if not (key.startswith("widgets/fill-")
                                and key.endswith("-exit")
                                and isinstance(card, dict)):
                            continue
                        args = ((card.get("refresh") or {})
                                .get("args")) or {}
                        if args.get("stop") is None:
                            continue
                        found[(env["environment_id"], key)] = args
                fired &= set(found)
                brackets = found
            for (env_id, key), args in list(brackets.items()):
                if (env_id, key) in fired:
                    continue
                ticks = latest_ticks(
                    {k: args[k] for k in ("symbol", "sec_type",
                     "expiration", "strike", "right") if k in args},
                    limit=1)
                if not ticks:
                    continue
                bid = ticks[0].get("bid")
                if bid is None:
                    continue
                if bid <= float(args["stop"]) \
                        or bid >= float(args["price"]):
                    # crossed: the gate verdicts at THIS tick
                    receipt = gates.fill_watch(
                        dict(args), invoke=_in_process_invoke)
                    check = receipt.get("data") or {}
                    if check.get("verdict") == "fill-supported":
                        view = _service_api(
                            "GET", f"/environments/{env_id}")
                        card = (view.get("context") or {}).get(key)
                        if isinstance(card, dict):
                            card = dict(card)
                            card["check"] = check
                            _service_api(
                                "POST",
                                f"/environments/{env_id}/context",
                                {"key": key, "value": card})
                            fired.add((env_id, key))
                            print(f"executor: {key} {check.get('leg')} "
                                  f"leg crossed — fill captured at "
                                  f"{check['fill']['price']}",
                                  flush=True)
        except Exception as error:
            print(f"executor blip: {str(error)[:160]}", flush=True)
        time.sleep(EXECUTOR_SECONDS)


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8643)
    arguments = parser.parse_args()
    _engine()  # pay the import once, up front
    threading.Thread(target=executor, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", arguments.port), Handler)
    print(f"tick relay ready on 127.0.0.1:{arguments.port} "
          f"(poll {POLL_SECONDS}s, batch {BATCH}, executor "
          f"{EXECUTOR_SECONDS}s)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
