"""The paper-trading gates: trade cases and the simulated-fill discipline.

A trade (``trades/<id>``) is the desk's unit of research: one idea,
one through five exact contracts, thesis, evidence keys, invalidation,
and an honest state — idea, watching, open-simulated, closed. A
simulated fill enters a trade only through the market gate:

  a live receipted regular-session bid/ask from the engine contains the
  price — ``fill_check`` fetches that quote itself, so there is no
  receipt an agent can fabricate. No receipt, no fill.

Paper fills are the desk's own work and take no human confirmation; the
member's asks are reserved for direction and risk — and a real-money
order would take explicit confirmation, but no order route exists here
by structure. These rules are ported from the old desk's validators,
which once rejected a fabricated $6.05 fill because the live market was
3.90 × 4.00. The tests pin that catch.
"""

import datetime
import json
import math
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

TRADE_STATES = ("idea", "watching", "open-simulated", "closed")
TRADE_FIELDS = {
    "contracts", "thesis", "evidence", "invalidation", "state",
    "fill", "exit", "as_of",
}
TRADE_REQUIRED = {"contracts", "thesis", "evidence", "invalidation", "state"}
FILL_FIELDS = {"contract", "price", "bid", "ask", "quantity", "observed_at"}

QUOTE_MAX_AGE_SECONDS = 120


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def receipt(summary, data=None, gaps=None, ok=True):
    return {"ok": ok, "summary": summary, "data": data or {},
            "as_of": _now().isoformat(timespec="seconds"), "gaps": gaps or []}


def parse_clock(value):
    """A fill clock must be exact and timezone-bearing; anything else is None."""

    try:
        clock = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return clock if clock.tzinfo is not None else None


def in_regular_session(clock) -> bool:
    """9:30–16:00 ET on a weekday — the only market a fill may cite."""

    local = clock.astimezone(ET)
    minutes = local.hour * 60 + local.minute
    return local.weekday() < 5 and 9 * 60 + 30 <= minutes < 16 * 60


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def fill_violations(fill, label="fill"):
    """Every way one recorded fill breaks the discipline, by name."""

    if not isinstance(fill, dict):
        return [f"the {label} is not an object"]
    if set(fill) != FILL_FIELDS:
        return [
            f"the {label} carries exactly contract, price, bid, ask, "
            "quantity, observed_at — nothing else, nothing missing"
        ]
    violations = []
    if not str(fill.get("contract") or "").strip():
        violations.append(f"the {label} names the exact contract it filled")
    price, bid, ask = (_number(fill[k]) for k in ("price", "bid", "ask"))
    if any(v is None or v < 0 for v in (price, bid, ask)):
        violations.append(f"the {label} needs finite non-negative price, bid, ask")
    else:
        if ask < bid:
            violations.append(f"the {label} market is inverted: {bid:g} × {ask:g}")
        elif not bid <= price <= ask:
            violations.append(
                f"the {label} price ${price:g} sits outside its receipted "
                f"market {bid:g} × {ask:g}"
            )
    quantity = fill["quantity"]
    if isinstance(quantity, bool) or not isinstance(quantity, int) \
            or not 1 <= quantity <= 100:
        violations.append(f"the {label} quantity is 1 through 100 whole contracts")
    clock = parse_clock(fill["observed_at"])
    if clock is None:
        violations.append(
            f"the {label} clock must be timezone-bearing ISO-8601 "
            "(the receipted quote's observed_at)"
        )
    elif not in_regular_session(clock):
        violations.append(
            f"the {label} quote clock {fill['observed_at']} is outside the "
            "regular session (9:30–16:00 ET, Mon–Fri)"
        )
    return violations


def trade_violations(trade):
    """Every way one trades/<id> value breaks the trade contract, by name."""

    if not isinstance(trade, dict):
        return ["a trade is one JSON object"]
    violations = []
    unknown = set(trade) - TRADE_FIELDS
    if unknown:
        violations.append(f"unknown field(s): {', '.join(sorted(unknown))}")
    missing = TRADE_REQUIRED - set(trade)
    if missing:
        violations.append(f"missing field(s): {', '.join(sorted(missing))}")
    contracts = trade.get("contracts")
    if "contracts" in trade:
        if (not isinstance(contracts, list) or not 1 <= len(contracts) <= 5
                or any(not (isinstance(item, str) and item.strip()
                            and len(item) <= 120)
                       for item in contracts)):
            violations.append(
                "a trade names one through five exact contracts")
            contracts = []
    else:
        contracts = []
    for field, limit in (("thesis", 2000), ("invalidation", 2000)):
        if field in trade:
            text = str(trade[field] or "").strip()
            if not text:
                violations.append(
                    "a trade always writes what would prove it wrong"
                    if field == "invalidation"
                    else f"a trade needs a non-empty {field}"
                )
            elif len(text) > limit:
                violations.append(f"{field} runs past {limit} characters")
    state = str(trade.get("state", "")).strip()
    if state not in TRADE_STATES:
        violations.append(
            f"state {state!r} is not one of {' | '.join(TRADE_STATES)}"
        )
    evidence = trade.get("evidence")
    if "evidence" in trade:
        if not isinstance(evidence, list) or len(evidence) > 24 or any(
            not (isinstance(key, str) and key.strip() and len(key) <= 240)
            for key in evidence
        ):
            violations.append(
                "evidence is a list of up to 24 context keys "
                "(findings/, quotes/, widgets/)"
            )
    fill = trade.get("fill")
    exit_fill = trade.get("exit")
    if state in ("open-simulated", "closed") and fill is None:
        violations.append(
            f"an {state} trade needs its receipted fill"
        )
    if state in ("idea", "watching") and fill is not None:
        violations.append("a fill cannot exist before open-simulated")
    if exit_fill is not None and state != "closed":
        violations.append("an exit belongs only on a closed trade")
    for label, block in (("fill", fill), ("exit", exit_fill)):
        if block is None:
            continue
        violations.extend(fill_violations(block, label=label))
        filled_contract = str((block or {}).get("contract") or "").strip() \
            if isinstance(block, dict) else ""
        if filled_contract and contracts and filled_contract not in contracts:
            violations.append(
                f"the {label}'s contract {filled_contract!r} is not one of "
                "this trade's contracts")
    if trade.get("as_of") is not None and parse_clock(trade["as_of"]) is None:
        violations.append("as_of must be a timezone-bearing ISO-8601 clock")
    return violations


# The old name remains callable while anything still says "case".
case_violations = trade_violations


def trade_check(arguments):
    """Validate one trades/<id> value; every violation is named, none hidden."""

    trade = arguments.get("trade", arguments.get("case"))
    trade_id = str(arguments.get("id") or "").strip() or "trade"
    violations = trade_violations(trade)
    if violations:
        return receipt(
            f"{trade_id} breaks {len(violations)} gate(s)",
            {"violations": violations},
            gaps=violations,
            ok=False,
        )
    return receipt(
        f"{trade_id} holds: state {trade['state']}, invalidation written, "
        f"{len(trade.get('contracts') or [])} contract(s), "
        f"{len(trade.get('evidence') or [])} evidence key(s)",
        {"violations": [], "state": trade["state"]},
    )


case_check = trade_check


def _refused(summary, reasons):
    """A fill_check refusal: the verdict travels in data so a program-backed
    card (refresh value_path result.data) always carries why it refused."""

    return receipt(summary, {"verdict": "refused", "reasons": reasons},
                   gaps=reasons, ok=False)


def _quote_from_receipt(raw):
    """Pull {bid, ask, observed_at} out of an engine quote receipt.

    Understands the bridged provider envelope (payload_json → answer.quote)
    and a plain {bid, ask, observed_at} dict; anything else is no quote.
    """

    if not isinstance(raw, dict):
        return None, ["the quote lane answered with no receipt"]
    if raw.get("ok") is False:
        detail = raw.get("error") or "; ".join(
            str(gap) for gap in raw.get("gaps") or []
        ) or raw.get("summary") or "unnamed failure"
        return None, [f"the quote lane failed: {str(detail)[:300]}"]
    payload = raw.get("payload_json")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    if not isinstance(payload, dict):
        payload = {}
    answer = payload.get("answer")
    answer = answer if isinstance(answer, dict) else {}
    quote = answer.get("quote")
    if not isinstance(quote, dict) or not quote:
        rows = answer.get("rows") or payload.get("rows") or []
        quote = rows[0] if rows and isinstance(rows[0], dict) else {}
    if not quote and {"bid", "ask"} <= set(raw):
        quote = raw
    if not quote:
        return None, ["the quote receipt carries no bid/ask — no market, no fill"]
    return quote, []


def _parse_order(arguments, tool):
    """Validate an order's own fields; (refusal, None) or (None, parsed)."""

    symbol = str(arguments.get("symbol") or "").strip().upper()
    if not symbol:
        return _refused(f"{tool} needs a symbol", ["no symbol given"]), None
    action = str(arguments.get("action") or "buy").strip().lower()
    if action not in ("buy", "sell"):
        return _refused("a simulated fill is a buy or a sell",
                        [f"action {action!r} is neither"]), None
    price = _number(arguments.get("price"))
    if price is None or price < 0:
        return _refused(f"{tool} needs a finite non-negative price",
                        ["no usable price given"]), None
    try:
        quantity = int(arguments.get("quantity") or 0)
    except (TypeError, ValueError):
        quantity = 0
    if not 1 <= quantity <= 100:
        return _refused("a simulated fill is 1 through 100 whole contracts",
                        [f"quantity {arguments.get('quantity')!r}"]), None
    contract = str(arguments.get("contract") or "").strip() or " ".join(
        str(arguments.get(field) or "").strip()
        for field in ("symbol", "expiration", "strike", "right")
    ).strip() or symbol
    return None, {"symbol": symbol, "action": action, "price": price,
                  "quantity": quantity, "contract": contract}


def _contract_request(arguments):
    request = {"symbol": str(arguments.get("symbol") or "").strip().upper()}
    for field in ("sec_type", "expiration", "strike", "right"):
        if arguments.get(field) not in (None, ""):
            request[field] = arguments[field]
    return request


def _gate_quote(parsed, quote, now, extra=None):
    """The one rulebook for any observed market, snapshot or stream tick."""

    contract, action = parsed["contract"], parsed["action"]
    price, quantity = parsed["price"], parsed["quantity"]
    bid, ask = _number(quote.get("bid")), _number(quote.get("ask"))
    observed_at = str(
        quote.get("observed_at") or quote.get("quote_time")
        or quote.get("source_time") or ""
    ).strip()
    clock = parse_clock(observed_at)
    if bid is None or ask is None or ask <= 0 or ask < bid or bid < 0:
        return _refused(
            f"{contract}: the receipt carries no live two-sided market",
            [f"bid/ask in the receipt: {quote.get('bid')!r} × "
             f"{quote.get('ask')!r} — no market, no fill"],
        )
    if clock is None:
        return _refused(
            f"{contract}: the quote receipt carries no usable clock",
            ["a fill needs the quote's exact timezone-bearing "
             "observed_at; none was receipted"],
        )
    age = (now - clock).total_seconds()
    if abs(age) > QUOTE_MAX_AGE_SECONDS:
        return _refused(
            f"{contract}: the receipted quote is not live",
            [f"quote clock {observed_at} is {abs(age):.0f}s "
             f"{'ahead' if age < 0 else 'old'} — a fill needs a market "
             f"no older than {QUOTE_MAX_AGE_SECONDS}s"],
        )
    if not in_regular_session(clock):
        return _refused(
            f"{contract}: the quote is outside the regular session",
            [f"quote clock {observed_at} falls outside 9:30–16:00 ET "
             "Mon–Fri; regular-session markets only"],
        )
    if not bid <= price <= ask:
        return _refused(
            f"{contract}: fill ${price:g} sits outside the live market "
            f"{bid:g} × {ask:g}",
            [f"the live receipted market is {bid:g} × {ask:g} as of "
             f"{observed_at}; ${price:g} is not in it"],
        )
    clock_text = clock.astimezone(ET).strftime("%H:%M:%S ET · %Y-%m-%d")
    data = {
        # One canonical fill block, ready to record verbatim.
        "verdict": "fill-supported",
        "contract": contract,
        "action": action,
        "fill": {
            "contract": contract, "price": price, "bid": bid, "ask": ask,
            "quantity": quantity, "observed_at": observed_at,
        },
    }
    if extra:
        data.update(extra)
    return receipt(
        f"{contract}: {action} {quantity} @ ${price:g} is supported by the "
        f"live market {bid:g} × {ask:g} (receipted {clock_text}) — "
        "the fill may be recorded",
        data,
    )


def fill_check(arguments, fetch_quote=None, now=None):
    """The market gate, snapshot lane: one leased broker observation.

    Fetches its own quote (the agent supplies no prices to trust) and
    applies the one rulebook. Prefer fill_watch for working orders — it
    rides the standing tick stream; this lane suits one-off checks.
    """

    now = now or _now()
    refusal, parsed = _parse_order(arguments, "fill_check")
    if refusal:
        return refusal
    if fetch_quote is None:
        import bridge

        if not bridge.available():
            return _refused(
                "no live quote lane on this machine — a simulated fill "
                "cannot be verified here",
                ["the full engine (broker quotes) is not installed; "
                 "no receipt, no confirmation may be offered"],
            )

        def fetch_quote(request):
            return bridge.invoke("ibkr.quote.snapshot", request)

    request = {**_contract_request(arguments), "max_age_seconds": 30}
    quote, gaps = _quote_from_receipt(fetch_quote(request))
    if quote is None:
        return _refused(f"{parsed['contract']}: no live receipted quote — "
                        "no fill", gaps)
    return _gate_quote(parsed, quote, now)


def _stream_tick(arguments, invoke, owner="alphalab-desk"):
    """Ensure a reqMktData stream for the contract; answer its freshest tick.

    One leased broker connection per contract, held by the engine's
    managed worker — no snapshot lease cycle per read. Returns
    (tick, stream_info, gaps); tick is None when no market answered.
    """

    request = _contract_request(arguments)
    started = invoke("ibkr.market_stream",
                     {**request, "action": "start", "owner": owner})
    stream_info = {}
    if isinstance(started, dict):
        data = started.get("data") or {}
        stream_info = {"stream_id": data.get("stream_id"),
                       "started_ok": bool(started.get("ok"))}
    latest = invoke("ibkr.market_stream",
                    {**request, "action": "latest", "limit": 1})
    if not isinstance(latest, dict) or latest.get("ok") is False:
        detail = (latest or {}).get("error") or (latest or {}).get("summary") \
            or "unnamed failure"
        return None, stream_info, [f"the stream lane failed: {str(detail)[:300]}"]
    rows = ((latest.get("data") or {}).get("rows")) or []
    if not rows or not isinstance(rows[0], dict):
        return None, stream_info, [
            "the stream has no persisted ticks yet — the subscription may "
            "still be warming, or the market is not printing"]
    return rows[0], stream_info, []


def fill_watch(arguments, invoke=None, now=None):
    """The market gate, stream lane: the freshest reqMktData tick.

    Ensures a managed live subscription for the contract (idempotent),
    reads the newest persisted tick, and applies the one rulebook. This
    is the lane for working orders: tick-level truth without a snapshot
    lease cycle per check. No tick, no fill.
    """

    now = now or _now()
    refusal, parsed = _parse_order(arguments, "fill_watch")
    if refusal:
        return refusal
    if invoke is None:
        import bridge

        if not bridge.available():
            return _refused(
                "no live quote lane on this machine — a simulated fill "
                "cannot be verified here",
                ["the full engine (broker quotes) is not installed; "
                 "no receipt, no confirmation may be offered"],
            )
        invoke = bridge.invoke
    tick, stream_info, gaps = _stream_tick(arguments, invoke)
    if tick is None:
        return _refused(f"{parsed['contract']}: no live tick — no fill", gaps)
    return _gate_quote(parsed, tick, now, extra={"stream": stream_info})


def live_quote(arguments, invoke=None):
    """A continuously current quote from the standing tick stream.

    Ensures the subscription and answers the freshest persisted tick with
    its own clock — the tool for any card whose price should update
    consistently. Snapshots remain for one-off or slow-cadence reads.
    """

    symbol = str(arguments.get("symbol") or "").strip().upper()
    if not symbol:
        return receipt("live_quote needs a symbol", ok=False,
                       gaps=["no symbol given"])
    if invoke is None:
        import bridge

        if not bridge.available():
            return receipt(
                "no live quote lane on this machine", ok=False,
                gaps=["the full engine (broker quotes) is not installed"])
        invoke = bridge.invoke
    tick, stream_info, gaps = _stream_tick(arguments, invoke)
    if tick is None:
        return receipt(f"{symbol}: no live tick yet", ok=False, gaps=gaps)
    observed_at = str(tick.get("quote_time") or tick.get("observed_at") or "")
    quote = {
        "bid": tick.get("bid"), "ask": tick.get("ask"),
        "last": tick.get("last"), "close": tick.get("close"),
        "bid_size": tick.get("bid_size"), "ask_size": tick.get("ask_size"),
        "model_iv": tick.get("model_iv"), "delta": tick.get("delta"),
        "observed_at": observed_at,
    }
    return receipt(
        f"{tick.get('contract_key') or symbol}: {tick.get('bid')} × "
        f"{tick.get('ask')} (last {tick.get('last')}) streaming as of "
        f"{observed_at}",
        {"quote": quote, "stream": stream_info},
    )
