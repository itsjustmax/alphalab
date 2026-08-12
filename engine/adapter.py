#!/usr/bin/env python3
"""The harness's own engine contract: one hop into the AlphalabAgents API.

This file is harness bytes — portable, digest-stable — executed with the
engine's Python (the AlphalabAgents venv) by the bridge. It reads
{"operation", "arguments"} on stdin, runs the operation through the
Agent API's front door, bounds the receipt to entry-sized honesty, and
prints it as JSON.

The allowlist below IS the capability boundary that travels with the
harness: only research and market-data operations exist here. The engine
also knows how to submit broker orders — those operations are not on the
list, so this desk has no order route, by structure.
"""

import json
import sys

ALLOWED_OPERATIONS = {
    "ibkr.contract",
    "ibkr.quote.snapshot",
    "ibkr.historical_bars",
    "ibkr.market_stream",
    "ibkr.option_contracts.top_volume",
    "options.chain.snapshot",
    "spx.gamma.latest",
    "market.context",
    "market.short_volume",
    "market.implied_move",
    "market.symbol.research",
    "market.symbol.zones",
    "options.contract.zones",
    "system.health",
}

MAX_ROWS = 60
MAX_RECEIPT_BYTES = 240_000  # entries cap at 256KB; leave headroom


def screen(operation):
    """None when the operation may run; otherwise the named refusal."""

    name = str(operation or "").strip()
    if not name:
        return "no operation named"
    if name not in ALLOWED_OPERATIONS:
        return (f"operation {name!r} is not in this harness's grant — "
                "research and market data only; there is no order route")
    return None


def redact(value, roots):
    """Scrub local filesystem roots out of receipt text.

    Engine errors love to quote absolute paths; the shared board is not
    the place for them. Longest root first so nested paths scrub clean.
    """

    replacements = [(root, alias) for root, alias in roots if root]
    replacements.sort(key=lambda pair: len(pair[0]), reverse=True)
    if isinstance(value, str):
        for root, alias in replacements:
            value = value.replace(root, alias)
        return value
    if isinstance(value, list):
        return [redact(item, roots) for item in value]
    if isinstance(value, dict):
        return {key: redact(item, roots) for key, item in value.items()}
    return value


def bound(receipt):
    """Cap rows and total size so a receipt always fits an entry, honestly."""

    if not isinstance(receipt, dict):
        return {"ok": False, "summary": "the engine answered a non-receipt",
                "gaps": ["non-dict answer from the Agent API"]}
    rows = receipt.get("rows")
    if isinstance(rows, list) and len(rows) > MAX_ROWS:
        receipt["rows"] = rows[:MAX_ROWS]
        receipt.setdefault("warnings", []).append(
            f"rows bounded to {MAX_ROWS} of {len(rows)} for the shared board")
    encoded = json.dumps(receipt, ensure_ascii=False, default=str)
    if len(encoded.encode("utf-8")) > MAX_RECEIPT_BYTES:
        for heavy in ("rows", "answer"):
            value = receipt.get(heavy)
            if value:
                receipt[heavy] = (
                    [] if isinstance(value, list)
                    else {"bounded": True})
                receipt.setdefault("warnings", []).append(
                    f"{heavy} bounded for the shared board — re-run with "
                    "tighter limits for the full answer")
            encoded = json.dumps(receipt, ensure_ascii=False, default=str)
            if len(encoded.encode("utf-8")) <= MAX_RECEIPT_BYTES:
                break
    return receipt


def main():
    import contextlib
    import io
    import os
    from pathlib import Path

    request = json.load(sys.stdin)
    # One spawn, one or many operations: batching exists so a client can
    # read several live quotes in a single engine start-up.
    batch = request.get("operations")
    calls = ([{"operation": item.get("operation"),
               "arguments": item.get("arguments") or {}}
              for item in batch] if isinstance(batch, list)
             else [{"operation": request.get("operation"),
                    "arguments": request.get("arguments") or {}}])

    from alphalab_agents.api.operations import run_api_operation
    from alphalab_agents.paths import resolve_alphalab_home

    home = None
    raw_home = os.environ.get("ALPHALAB_HOME", "").strip()
    if raw_home:
        home = resolve_alphalab_home(Path(raw_home).resolve(),
                                     allow_internal=True)
    roots = (
        (str(home.root) if home is not None else raw_home, "~ALPHALAB_HOME"),
        (os.environ.get("ALPHALAB_PACKAGE_SRC", ""), "~ENGINE"),
        (os.path.expanduser("~"), "~"),
    )
    receipts = []
    for call in calls:
        refusal = screen(call["operation"])
        if refusal:
            receipts.append({"ok": False, "summary": f"refused: {refusal}",
                             "gaps": [refusal]})
            continue
        # The engine may chat on stdout; only receipts may reach ours.
        with contextlib.redirect_stdout(io.StringIO()):
            receipt = run_api_operation(
                str(call["operation"]),
                dict(call["arguments"]),
                home=home,
                surface="agent-runtime",
            )
        receipts.append(redact(bound(receipt), roots))
    if isinstance(batch, list):
        print(json.dumps({"ok": True, "receipts": receipts},
                         ensure_ascii=False, default=str))
    else:
        print(json.dumps(receipts[0], ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
