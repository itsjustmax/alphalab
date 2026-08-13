#!/usr/bin/env python3
"""Arm one trade for live execution — the member's own terminal act.

Arming is deliberately NOT reachable from the web UI or by any agent:
you run this yourself, in your own terminal, per trade. Even then,
nothing goes live until every layer above and below agrees:

  1. this arming record                      (per trade, this tool)
  2. ~/.alphalab/risk.json live_enabled      (your standing parameters)
  3. ALPHALAB_LIVE_OPTION_ORDERS_ENABLED=1   (the engine's master env)
  4. ALPHALAB_LIVE_ACCOUNT_ALLOWLIST         (dedicated account only)
  5. the engine's per-call gate              (options-only, caps,
                                              confirmation phrase,
                                              fail-closed daily budget)

Usage:
  python3 tools/arm_live.py spy-wall-break --max-debit 400
  python3 tools/arm_live.py spy-wall-break --disarm
  python3 tools/arm_live.py --status
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "engine"))
import risk  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("trade_id", nargs="?")
    parser.add_argument("--max-debit", type=float, default=None,
                        help="per-order debit cap for this trade (USD)")
    parser.add_argument("--disarm", action="store_true")
    parser.add_argument("--status", action="store_true")
    arguments = parser.parse_args()

    armed = risk.load_armed()
    standing = risk.load_risk()
    if arguments.status or not arguments.trade_id:
        print(f"live_enabled: {standing.get('live_enabled')} · "
              f"kill: {standing.get('kill')} · whitelist: "
              f"{standing.get('symbol_whitelist')}")
        print(f"armed trades: {json.dumps(armed, indent=1) or '{}'}")
        return

    if arguments.disarm:
        armed.pop(arguments.trade_id, None)
        print(f"{arguments.trade_id}: DISARMED")
    else:
        cap = arguments.max_debit or standing.get("max_debit_per_order")
        confirm = input(
            f"Arm {arguments.trade_id!r} for LIVE orders up to "
            f"${cap:.2f} per order? Type the trade id to confirm: ")
        if confirm.strip() != arguments.trade_id:
            print("not confirmed — nothing armed")
            return
        armed[arguments.trade_id] = {
            "armed_at": datetime.datetime.now(datetime.timezone.utc)
                        .isoformat(timespec="seconds"),
            "max_debit": float(cap),
        }
        print(f"{arguments.trade_id}: ARMED (max ${cap:.2f}/order). "
              f"live_enabled={standing.get('live_enabled')} — orders "
              f"stay dry-run until risk.json and the engine env agree.")
    os.makedirs(os.path.dirname(risk.ARMED_PATH), exist_ok=True)
    with open(risk.ARMED_PATH, "w", encoding="utf-8") as handle:
        json.dump(armed, handle, indent=1)


if __name__ == "__main__":
    main()
