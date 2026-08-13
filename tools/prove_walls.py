#!/usr/bin/env python3
"""Prove the live-order walls, on demand — run before any live session.

Fires hostile probes at every layer and reports PASS only when every
one is refused. Nothing this script does can place an order: every
probe is designed to be blocked, and a probe that is NOT blocked is a
loud failure that must stop the session.

Run with the ENGINE python:
  PYTHONPATH=<pkg> ALPHALAB_HOME=<home> <engine python> tools/prove_walls.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "engine"))


def engine_probes():
    from alphalab_agents.api.operations import run_api_operation
    from alphalab_agents.paths import resolve_alphalab_home

    home = resolve_alphalab_home(
        Path(os.environ["ALPHALAB_HOME"]).resolve(), allow_internal=True)
    failures = []

    def expect_refusal(name, contract, extra=None):
        reply = run_api_operation("ibkr.orders.submit_bracket", {
            "mode": "live", "contract": contract, "bid": 1, "ask": 1.1,
            "quantity": 1, "live_confirm": "SUBMIT_REAL_OPTION_ORDER",
            "option_only_ack": True, **(extra or {}),
            "owner": "prove-walls"}, home=home, surface="tick-relay-live")
        answer = reply.get("answer") or {}
        submitted = bool(answer.get("broker_order_submitted"))
        refused = (not reply.get("ok")) and not submitted
        status = "refused" if refused else "!!! NOT REFUSED !!!"
        print(f"  {name:<28} {status}")
        if not refused:
            failures.append(name)

    print("engine walls (live mode, hostile contracts):")
    expect_refusal("AAPL equity order", {"symbol": "AAPL", "sec_type": "STK"})
    expect_refusal("JPM equity order", {"symbol": "JPM", "sec_type": "STK"})
    expect_refusal("SPCX equity order", {"symbol": "SPCX", "sec_type": "STK"})
    expect_refusal("missing sec_type", {"symbol": "AAPL"})
    expect_refusal("index order", {"symbol": "SPX", "sec_type": "IND"})
    expect_refusal("cash order", {"symbol": "EUR", "sec_type": "CASH"})
    expect_refusal("equity override flag",
                   {"symbol": "AAPL", "sec_type": "STK"},
                   {"allow_equity_orders": True})
    # NEVER a submittable probe: this one violates the premium cap, so
    # it is refused whether the live gates are on or off. A probe that
    # could pass when gates are on would BE an order — forbidden here.
    expect_refusal("option above premium cap",
                   {"symbol": "SPY", "sec_type": "OPT",
                    "expiration": "20260814", "strike": 783, "right": "C"},
                   {"bid": 99.0, "ask": 99.5,
                    "entry_price": 99.25, "max_contract_premium": 6.0})
    return failures


def risk_probes():
    import risk

    failures = []
    standing = risk.load_risk()
    armed = risk.load_armed()

    def expect(name, order, journal=None):
        refusals = risk.order_refusals(order, standing, armed, journal or [])
        status = "refused" if refusals else "!!! NOT REFUSED !!!"
        print(f"  {name:<28} {status}")
        if not refusals:
            failures.append(name)

    print("desk risk layer (current risk.json/arming):")
    expect("equity through risk layer",
           {"trade_id": "x", "kind": "entry", "symbol": "AAPL",
            "sec_type": "STK", "price": 1, "quantity": 1})
    expect("non-whitelisted option",
           {"trade_id": "x", "kind": "entry", "symbol": "TSLA",
            "sec_type": "OPT", "price": 1, "quantity": 1})
    expect("unarmed trade",
           {"trade_id": "never-armed", "kind": "entry", "symbol": "SPY",
            "sec_type": "OPT", "price": 1, "quantity": 1})
    expect("oversized debit",
           {"trade_id": "x", "kind": "entry", "symbol": "SPY",
            "sec_type": "OPT", "price": 99, "quantity": 1})
    return failures


def main():
    failures = risk_probes()
    try:
        failures += engine_probes()
    except ImportError:
        print("engine probes skipped — run with the engine python for "
              "the full proof")
        failures.append("engine probes did not run")
    print()
    if failures:
        print(f"WALLS FAILED: {failures} — DO NOT GO LIVE")
        raise SystemExit(1)
    print("ALL WALLS HOLD — every hostile probe refused")


if __name__ == "__main__":
    main()
