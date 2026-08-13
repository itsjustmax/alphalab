"""The deterministic risk layer: every live order passes or it does not.

This module is pure decision logic — no broker, no network — so every
rule is pinned by tests. It reads two member-owned files that live
OUTSIDE the desk (no tool exposes them, no agent can write them):

  ~/.alphalab/risk.json     the standing parameters
      {"live_enabled": false, "max_debit_per_order": 400,
       "max_contracts": 1, "max_open_trades_live": 1,
       "symbol_whitelist": ["SPY"], "max_daily_debit": 800,
       "kill": false}
  ~/.alphalab/live-armed.json   per-trade arming, written ONLY by the
      member's own terminal act (tools/arm_live.py):
      {"<trade-id>": {"armed_at": ..., "max_debit": 400}}

Below this layer sit the engine's own guards (options-only, account
allowlist, env master switch, confirmation phrase, fail-closed daily
stats) — defense in depth, every layer deterministic.
"""

import json
import os

RISK_PATH = os.path.expanduser("~/.alphalab/risk.json")
ARMED_PATH = os.path.expanduser("~/.alphalab/live-armed.json")
ORDER_JOURNAL = os.path.expanduser("~/.alphalab/live-orders.jsonl")

DEFAULT_RISK = {
    "live_enabled": False,
    "max_debit_per_order": 400.0,
    "max_contracts": 1,
    "max_open_trades_live": 1,
    "symbol_whitelist": ["SPY"],
    "max_daily_debit": 800.0,
    "kill": False,
}


def load_risk(path=None):
    try:
        with open(path or RISK_PATH, encoding="utf-8") as handle:
            stored = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_RISK)
    merged = dict(DEFAULT_RISK)
    if isinstance(stored, dict):
        merged.update(stored)
    return merged


def load_armed(path=None):
    try:
        with open(path or ARMED_PATH, encoding="utf-8") as handle:
            stored = json.load(handle)
        return stored if isinstance(stored, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def order_refusals(order, risk, armed, journal_today):
    """Every reason this live order must NOT be placed, by name.

    `order`: {trade_id, symbol, sec_type, right, price, quantity}
    `journal_today`: today's already-journaled live orders
      [{trade_id, debit, status}] — duplicates and budgets read this.
    An empty list means the order may proceed to the ENGINE's gate,
    which applies its own independent checks. Fail-closed everywhere.
    """

    refusals = []
    if risk.get("kill"):
        refusals.append("the kill switch is on")
    if not risk.get("live_enabled"):
        refusals.append("live trading is not enabled (risk.json)")
    arm = armed.get(str(order.get("trade_id") or ""))
    if not isinstance(arm, dict):
        refusals.append(
            f"trade {order.get('trade_id')!r} is not armed — arming is "
            f"the member's own terminal act (tools/arm_live.py)")
        arm = {}
    if str(order.get("sec_type") or "OPT").upper() != "OPT":
        refusals.append("live orders are OPTIONS ONLY")
    symbol = str(order.get("symbol") or "").upper()
    whitelist = [str(s).upper() for s in risk.get("symbol_whitelist") or []]
    if symbol not in whitelist:
        refusals.append(
            f"{symbol} is not in the symbol whitelist {whitelist}")
    try:
        price = float(order.get("price"))
        quantity = int(order.get("quantity") or 0)
    except (TypeError, ValueError):
        return refusals + ["order price/quantity are not numeric"]
    if quantity < 1 or quantity > int(risk.get("max_contracts") or 1):
        refusals.append(
            f"quantity {quantity} outside 1..{risk.get('max_contracts')}")
    debit = price * quantity * 100
    per_order = float(arm.get("max_debit")
                      or risk.get("max_debit_per_order") or 0)
    if debit > min(per_order,
                   float(risk.get("max_debit_per_order") or 0)):
        refusals.append(
            f"debit ${debit:.2f} exceeds the per-order cap "
            f"(${per_order:.2f} armed / "
            f"${risk.get('max_debit_per_order')} standing)")
    day_debit = sum(float(row.get("debit") or 0) for row in journal_today
                    if row.get("status") in ("submitted", "submit_failed",
                                             "dry_run"))
    if day_debit + debit > float(risk.get("max_daily_debit") or 0):
        refusals.append(
            f"daily debit budget: ${day_debit:.2f} used + ${debit:.2f} "
            f"> ${risk.get('max_daily_debit')} (failed submits count "
            f"until reconciled — fail closed)")
    open_entries = {row.get("trade_id") for row in journal_today
                    if row.get("kind") == "entry"
                    and row.get("status") in ("submitted", "dry_run")}
    if order.get("kind") == "entry":
        if order.get("trade_id") in open_entries:
            refusals.append(
                "an entry for this trade is already working — no "
                "duplicate orders, ever")
        elif len(open_entries) >= int(risk.get("max_open_trades_live") or 1):
            refusals.append(
                f"{len(open_entries)} live trade(s) already open — the "
                f"cap is {risk.get('max_open_trades_live')}")
    return refusals


def journal_append(record, path=None):
    target = path or ORDER_JOURNAL
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def journal_today(now_iso_date, path=None):
    rows = []
    try:
        with open(path or ORDER_JOURNAL, encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(row.get("at", "")).startswith(now_iso_date):
                    rows.append(row)
    except OSError:
        pass
    return rows
