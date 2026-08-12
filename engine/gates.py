"""The paper-trading gates: trade cases and the simulated-fill discipline.

A case (``cases/<id>``) is the desk's unit of research: contract, thesis,
evidence keys, invalidation, and an honest state — idea, watching,
open-simulated, closed. A simulated fill enters a case only through two
gates:

  (a) a live receipted regular-session bid/ask from the engine supports
      the price — ``fill_check`` fetches that quote itself, so there is
      no receipt an agent can fabricate; no receipt, no confirmation
      offered;
  (b) the member explicitly confirms on the ask card built from that
      receipt — no confirmation, no fill.

These rules are ported from the old desk's validators, which once
rejected a fabricated $6.05 fill because the live market was 3.90 × 4.00.
The tests pin that catch.
"""

import datetime
import json
import math
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

CASE_STATES = ("idea", "watching", "open-simulated", "closed")
CASE_FIELDS = {
    "contract", "thesis", "evidence", "invalidation", "state",
    "fill", "exit", "as_of",
}
CASE_REQUIRED = {"contract", "thesis", "evidence", "invalidation", "state"}
FILL_FIELDS = {"price", "bid", "ask", "quantity", "observed_at", "confirmed"}

QUOTE_MAX_AGE_SECONDS = 120
CONFIRM_OPTION = "Confirm fill"
DECLINE_OPTION = "Stand down"


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
            f"the {label} carries exactly price, bid, ask, quantity, "
            "observed_at, confirmed — nothing else, nothing missing"
        ]
    violations = []
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
    confirmed = fill["confirmed"]
    if not (isinstance(confirmed, str) and confirmed.startswith("answers/")):
        violations.append(
            f"the {label} is recorded only after the member confirms — "
            "confirmed names the answers/ key that carries their answer"
        )
    return violations


def case_violations(case):
    """Every way one cases/<id> value breaks the case contract, by name."""

    if not isinstance(case, dict):
        return ["a case is one JSON object"]
    violations = []
    unknown = set(case) - CASE_FIELDS
    if unknown:
        violations.append(f"unknown field(s): {', '.join(sorted(unknown))}")
    missing = CASE_REQUIRED - set(case)
    if missing:
        violations.append(f"missing field(s): {', '.join(sorted(missing))}")
    for field, limit in (("contract", 240), ("thesis", 2000), ("invalidation", 2000)):
        if field in case:
            text = str(case[field] or "").strip()
            if not text:
                violations.append(
                    "a case always writes what would prove it wrong"
                    if field == "invalidation"
                    else f"a case needs a non-empty {field}"
                )
            elif len(text) > limit:
                violations.append(f"{field} runs past {limit} characters")
    state = str(case.get("state", "")).strip()
    if state not in CASE_STATES:
        violations.append(
            f"state {state!r} is not one of {' | '.join(CASE_STATES)}"
        )
    evidence = case.get("evidence")
    if "evidence" in case:
        if not isinstance(evidence, list) or len(evidence) > 24 or any(
            not (isinstance(key, str) and key.strip() and len(key) <= 240)
            for key in evidence
        ):
            violations.append(
                "evidence is a list of up to 24 context keys "
                "(findings/, quotes/, widgets/)"
            )
    fill = case.get("fill")
    exit_fill = case.get("exit")
    if state in ("open-simulated", "closed") and fill is None:
        violations.append(
            f"an {state} case needs its receipted, confirmed fill"
        )
    if state in ("idea", "watching") and fill is not None:
        violations.append("a fill cannot exist before open-simulated")
    if exit_fill is not None and state != "closed":
        violations.append("an exit belongs only on a closed case")
    if fill is not None:
        violations.extend(fill_violations(fill, label="fill"))
    if exit_fill is not None:
        violations.extend(fill_violations(exit_fill, label="exit"))
    if case.get("as_of") is not None and parse_clock(case["as_of"]) is None:
        violations.append("as_of must be a timezone-bearing ISO-8601 clock")
    return violations


def case_check(arguments):
    """Validate one cases/<id> value; every violation is named, none hidden."""

    case = arguments.get("case")
    case_id = str(arguments.get("id") or "").strip() or "case"
    violations = case_violations(case)
    if violations:
        return receipt(
            f"{case_id} breaks {len(violations)} gate(s)",
            {"violations": violations},
            gaps=violations,
            ok=False,
        )
    return receipt(
        f"{case_id} holds: state {case['state']}, invalidation written, "
        f"{len(case.get('evidence') or [])} evidence key(s)",
        {"violations": [], "state": case["state"]},
    )


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


def fill_check(arguments, fetch_quote=None, now=None):
    """Gate (a): may this simulated fill be offered for confirmation at all?

    Fetches its own live quote (the agent supplies no prices to trust),
    verifies regular session, freshness, and that the price sits inside
    the receipted bid/ask. An ok answer carries the exact ask card to put
    up and the fill block to record once — and only once — the member
    confirms. Any failure offers no card: no receipt, no confirmation.
    """

    now = now or _now()
    symbol = str(arguments.get("symbol") or "").strip().upper()
    if not symbol:
        return receipt("fill_check needs a symbol", ok=False,
                       gaps=["no symbol given"])
    action = str(arguments.get("action") or "buy").strip().lower()
    if action not in ("buy", "sell"):
        return receipt("a simulated fill is a buy or a sell", ok=False,
                       gaps=[f"action {action!r} is neither"])
    price = _number(arguments.get("price"))
    if price is None or price < 0:
        return receipt("fill_check needs a finite non-negative price", ok=False,
                       gaps=["no usable price given"])
    try:
        quantity = int(arguments.get("quantity") or 0)
    except (TypeError, ValueError):
        quantity = 0
    if not 1 <= quantity <= 100:
        return receipt("a simulated fill is 1 through 100 whole contracts",
                       ok=False, gaps=[f"quantity {arguments.get('quantity')!r}"])
    contract = str(arguments.get("contract") or "").strip() or " ".join(
        str(arguments.get(field) or "").strip()
        for field in ("symbol", "expiration", "strike", "right")
    ).strip() or symbol

    if fetch_quote is None:
        import bridge

        if not bridge.available():
            return receipt(
                "no live quote lane on this machine — a simulated fill "
                "cannot be verified here",
                ok=False,
                gaps=[
                    "the full engine (broker quotes) is not installed; "
                    "no receipt, no confirmation may be offered"
                ],
            )

        def fetch_quote(request):
            return bridge.invoke("ibkr.quote.snapshot", request)

    request = {"symbol": symbol, "max_age_seconds": 30}
    for field in ("sec_type", "expiration", "strike", "right"):
        if arguments.get(field) not in (None, ""):
            request[field] = arguments[field]
    quote, gaps = _quote_from_receipt(fetch_quote(request))
    if quote is None:
        return receipt(f"{contract}: no live receipted quote — no fill",
                       ok=False, gaps=gaps)

    bid, ask = _number(quote.get("bid")), _number(quote.get("ask"))
    observed_at = str(
        quote.get("observed_at") or quote.get("source_time") or ""
    ).strip()
    clock = parse_clock(observed_at)
    if bid is None or ask is None or ask <= 0 or ask < bid or bid < 0:
        return receipt(
            f"{contract}: the receipt carries no live two-sided market",
            ok=False,
            gaps=[f"bid/ask in the receipt: {quote.get('bid')!r} × "
                  f"{quote.get('ask')!r} — no market, no fill"],
        )
    if clock is None:
        return receipt(
            f"{contract}: the quote receipt carries no usable clock",
            ok=False,
            gaps=["a fill needs the quote's exact timezone-bearing "
                  "observed_at; none was receipted"],
        )
    age = (now - clock).total_seconds()
    if abs(age) > QUOTE_MAX_AGE_SECONDS:
        return receipt(
            f"{contract}: the receipted quote is not live",
            ok=False,
            gaps=[f"quote clock {observed_at} is {abs(age):.0f}s "
                  f"{'ahead' if age < 0 else 'old'} — a fill needs a market "
                  f"no older than {QUOTE_MAX_AGE_SECONDS}s"],
        )
    if not in_regular_session(clock):
        return receipt(
            f"{contract}: the quote is outside the regular session",
            ok=False,
            gaps=[f"quote clock {observed_at} falls outside 9:30–16:00 ET "
                  "Mon–Fri; regular-session markets only"],
        )
    if not bid <= price <= ask:
        return receipt(
            f"{contract}: fill ${price:g} sits outside the live market "
            f"{bid:g} × {ask:g}",
            ok=False,
            gaps=[f"the live receipted market is {bid:g} × {ask:g} as of "
                  f"{observed_at}; ${price:g} is not in it"],
        )

    clock_text = clock.astimezone(ET).strftime("%H:%M:%S ET · %Y-%m-%d")
    return receipt(
        f"{contract}: {action} {quantity} @ ${price:g} is supported by the "
        f"live market {bid:g} × {ask:g} — awaiting the member's confirmation",
        {
            "verdict": "fill-supported",
            "contract": contract,
            "action": action,
            "price": price,
            "quantity": quantity,
            "bid": bid,
            "ask": ask,
            "observed_at": observed_at,
            "fill": {
                "price": price, "bid": bid, "ask": ask,
                "quantity": quantity, "observed_at": observed_at,
                "confirmed": None,
            },
            "ask_card": {
                "kind": "ask",
                "title": f"Confirm simulated fill — {contract}",
                "question": (
                    f"Simulated fill: {action} {quantity} × {contract} @ "
                    f"${price:g}. Live market {bid:g} × {ask:g}, receipted "
                    f"{clock_text}. Record this paper fill?"
                ),
                "options": [CONFIRM_OPTION, DECLINE_OPTION],
                "as_of": observed_at,
            },
        },
        gaps=["a confirmation older than the market is not a fill — re-run "
              "fill_check if the member answers late"],
    )
