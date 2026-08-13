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


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8643)
    arguments = parser.parse_args()
    _engine()  # pay the import once, up front
    server = ThreadingHTTPServer(("127.0.0.1", arguments.port), Handler)
    print(f"tick relay ready on 127.0.0.1:{arguments.port} "
          f"(poll {POLL_SECONDS}s, batch {BATCH})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
