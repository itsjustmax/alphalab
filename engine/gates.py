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
3.90 × 4.00 — a RECORDED fill's price must sit inside its receipted
market, always. The gate itself executes like the market would: a
marketable limit fills at the receipted ask (buys) or bid (sells), and
a limit inside the spread rests. The tests pin all of it.
"""

import datetime
import json
import math
import re
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


_STRIKE_RIGHT = re.compile(r"(\d+(?:\.\d+)?)\s*([CP])\b", re.IGNORECASE)
_DATEISH = re.compile(r"\b(\d{8})\b|\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")


def contracts_match(label_a, label_b):
    """Same contract under different notations?

    'NVDA 8/21 235C' and 'NVDA 20260821 235C' are one contract; labels
    vary by author, identity must not. Symbol + strike/right + (when both
    carry one) the month/day of expiration decide it.
    """

    a, b = str(label_a or "").upper(), str(label_b or "").upper()
    if not a or not b:
        return False
    if a.split()[0] != b.split()[0]:
        return False
    sr_a, sr_b = _STRIKE_RIGHT.search(a), _STRIKE_RIGHT.search(b)
    if bool(sr_a) != bool(sr_b):
        return False
    if sr_a and (float(sr_a.group(1)) != float(sr_b.group(1))
                 or sr_a.group(2).upper() != sr_b.group(2).upper()):
        return False

    def month_day(text):
        match = _DATEISH.search(text)
        if not match:
            return None
        if match.group(1):
            return int(match.group(1)[4:6]), int(match.group(1)[6:8])
        return int(match.group(2)), int(match.group(3))

    md_a, md_b = month_day(a), month_day(b)
    if md_a and md_b and md_a != md_b:
        return False
    return True


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
            got = ("nothing" if not isinstance(contracts, list)
                   else ", ".join(type(item).__name__ for item in contracts[:5])
                   or "an empty list")
            violations.append(
                "a trade names one through five exact contracts as plain "
                'strings — like "NVDA 20260821 235C" — but got: ' + got +
                " (write forms/trade/<id> with minimal fields and the desk "
                "assembles the cell for you)")
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
        if filled_contract and contracts and not any(
                contracts_match(filled_contract, item) for item in contracts):
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


INDEX_SYMBOLS = {"SPX", "XSP", "NDX", "VIX", "DJI", "RUT"}


def _contract_request(arguments):
    symbol = str(arguments.get("symbol") or "").strip().upper()
    request = {"symbol": symbol}
    for field in ("sec_type", "expiration", "strike", "right"):
        if arguments.get(field) not in (None, ""):
            request[field] = arguments[field]
    # An index is not a stock: without this, an SPX stream asks the broker
    # for STK:SPX, which does not exist — and never prints a tick.
    if "sec_type" not in request and symbol in INDEX_SYMBOLS:
        request["sec_type"] = "IND"
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
    # Marketable-limit semantics, like the market would actually do:
    # a buy executes AT the ask the moment the limit reaches it (price
    # improvement included); a limit inside the spread RESTS until the
    # market comes to it. Sells mirror against the bid.
    if action == "buy":
        if price < ask:
            return _refused(
                f"{contract}: buy limit ${price:g} rests below the ask "
                f"({bid:g} × {ask:g})",
                [f"the live ask is {ask:g} as of {observed_at}; a buy "
                 f"limit of ${price:g} rests until the ask comes to it"],
            )
        executed = ask
    else:
        if price > bid:
            return _refused(
                f"{contract}: sell limit ${price:g} rests above the bid "
                f"({bid:g} × {ask:g})",
                [f"the live bid is {bid:g} as of {observed_at}; a sell "
                 f"limit of ${price:g} rests until the bid comes to it"],
            )
        executed = bid
    clock_text = clock.astimezone(ET).strftime("%H:%M:%S ET · %Y-%m-%d")
    data = {
        # One canonical fill block, ready to record verbatim. The price
        # is the EXECUTION (ask for buys, bid for sells), never the limit.
        "verdict": "fill-supported",
        "contract": contract,
        "action": action,
        "limit": price,
        "fill": {
            "contract": contract, "price": executed, "bid": bid, "ask": ask,
            "quantity": quantity, "observed_at": observed_at,
        },
    }
    if extra:
        data.update(extra)
    return receipt(
        f"{contract}: {action} {quantity} filled at ${executed:g} "
        f"(limit ${price:g}; market {bid:g} × {ask:g} receipted "
        f"{clock_text})",
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


def _latest_rows(invoke, request):
    latest = invoke("ibkr.market_stream",
                    {**request, "action": "latest", "limit": 1})
    if not isinstance(latest, dict) or latest.get("ok") is False:
        detail = (latest or {}).get("error") or (latest or {}).get("summary") \
            or "unnamed failure"
        return None, [f"the stream lane failed: {str(detail)[:300]}"]
    rows = ((latest.get("data") or {}).get("rows")) or []
    return rows, []


def _stream_tick(arguments, invoke, owner="alphalab-desk"):
    """Read the contract's freshest tick; start its stream only if needed.

    Latest-first keeps the steady state to ONE engine call per read; the
    start (idempotent, one leased broker connection per contract, held by
    the engine's managed worker) happens only when no ticks exist yet.
    Returns (tick, stream_info, gaps); tick is None when no market answered.
    """

    request = _contract_request(arguments)
    rows, gaps = _latest_rows(invoke, request)
    stream_info = {}
    if not rows:
        started = invoke("ibkr.market_stream",
                         {**request, "action": "start", "owner": owner})
        if isinstance(started, dict):
            data = started.get("data") or {}
            stream_info = {"stream_id": data.get("stream_id"),
                           "started_ok": bool(started.get("ok"))}
        rows, gaps = _latest_rows(invoke, request)
    if rows is None:
        return None, stream_info, gaps
    if not rows or not isinstance(rows[0], dict):
        return None, stream_info, [
            "the stream has no persisted ticks yet — the subscription may "
            "still be warming, or the market is not printing"]
    return rows[0], stream_info, []


def live_quotes(arguments, invoke_batch=None):
    """Freshest ticks for several symbols in ONE engine start-up.

    The engine import dominates a read's latency; this is the watchlist's
    real-time lane. Symbols without ticks get their streams started and
    one retry; still-quiet symbols answer null, named in gaps.
    """

    raw = arguments.get("symbols")
    if isinstance(raw, str):
        raw = raw.replace(",", " ").split()
    symbols = [str(item).strip().upper() for item in (raw or [])
               if str(item).strip()][:10]
    if not symbols:
        return receipt("live_quotes needs symbols", ok=False,
                       gaps=["no symbols given"])
    if invoke_batch is None:
        import bridge

        if not bridge.available():
            return receipt("no live quote lane on this machine", ok=False,
                           gaps=["the full engine is not installed"])
        invoke_batch = bridge.invoke_many

    def request(symbol):
        return {**_contract_request({"symbol": symbol}),
                "action": "latest", "limit": 1}

    replies = invoke_batch([("ibkr.market_stream", request(s))
                            for s in symbols])
    quotes = {}
    quiet = []
    for symbol, reply in zip(symbols, replies):
        rows = (((reply or {}).get("data") or {}).get("rows")) or []
        if rows and isinstance(rows[0], dict):
            tick = rows[0]
            quotes[symbol] = {
                "bid": tick.get("bid"), "ask": tick.get("ask"),
                "last": tick.get("last"), "close": tick.get("close"),
                "observed_at": tick.get("quote_time") or tick.get("observed_at"),
            }
        else:
            quotes[symbol] = None
            quiet.append(symbol)
    gaps = []
    if quiet and not arguments.get("warm"):
        gaps.extend(f"{symbol}: no tick yet (pass warm=true once to start "
                    "its stream)" for symbol in quiet)
        quiet = []
    if quiet:
        starts = [("ibkr.market_stream",
                   {**_contract_request({"symbol": s}),
                    "action": "start", "owner": "alphalab-desk"})
                  for s in quiet]
        retries = [("ibkr.market_stream", request(s)) for s in quiet]
        batched = invoke_batch(starts + retries)
        for symbol, reply in zip(quiet, batched[len(starts):]):
            rows = (((reply or {}).get("data") or {}).get("rows")) or []
            if rows and isinstance(rows[0], dict):
                tick = rows[0]
                quotes[symbol] = {
                    "bid": tick.get("bid"), "ask": tick.get("ask"),
                    "last": tick.get("last"), "close": tick.get("close"),
                    "observed_at": tick.get("quote_time")
                    or tick.get("observed_at"),
                }
            else:
                gaps.append(f"{symbol}: stream warming — no tick yet")
    live = sum(1 for value in quotes.values() if value)
    return receipt(
        f"live ticks for {live}/{len(symbols)} symbol(s)",
        {"quotes": quotes},
        gaps=gaps,
    )


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
