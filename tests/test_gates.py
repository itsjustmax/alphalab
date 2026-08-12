"""The paper-trading gates, pinned.

These tests are the desk's constitution for simulated fills: a fill needs
a live receipted regular-session bid/ask that supports its price, and the
member's explicit confirmation — no receipt, no confirmation offered; no
confirmation, no fill. They pin the historical catch where a fabricated
$6.05 fill was rejected because the live market was 3.90 × 4.00.

Run with any pytest: python3 -m pytest tests/
"""

import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "engine"))

import gates


# A regular-session moment: Wednesday 2026-08-12, 14:31 ET (EDT).
LIVE_CLOCK = "2026-08-12T14:31:22-04:00"
NOW = datetime.datetime(2026, 8, 12, 18, 32, 0, tzinfo=datetime.timezone.utc)


def make_fill(**overrides):
    fill = {
        "price": 4.00, "bid": 3.90, "ask": 4.00, "quantity": 2,
        "observed_at": LIVE_CLOCK,
    }
    fill.update(overrides)
    return fill


def make_case(**overrides):
    case = {
        "contract": "NVDA 20260814 190C",
        "thesis": "long on the supply-lid break above 189",
        "evidence": ["findings/nvda-supply-lid", "quotes/NVDA"],
        "invalidation": "a daily close back under 185 kills the break",
        "state": "idea",
        "fill": None,
        "exit": None,
    }
    case.update(overrides)
    return case


def envelope(quote):
    """A quote receipt shaped like the bridged provider envelope."""

    return {
        "ok": True,
        "receipt_status": "ok",
        "payload_json": json.dumps({"answer": {"quote": quote}}),
    }


def live_quote(**overrides):
    quote = {"bid": 3.90, "ask": 4.00, "last": 3.95, "observed_at": LIVE_CLOCK}
    quote.update(overrides)
    return quote


def check_fill(arguments, quote_receipt):
    return gates.fill_check(
        arguments, fetch_quote=lambda request: quote_receipt, now=NOW
    )


FILL_ARGS = {
    "symbol": "NVDA", "sec_type": "OPT", "expiration": "20260814",
    "strike": 190, "right": "C", "price": 4.00, "quantity": 2,
    "contract": "NVDA 20260814 190C",
}


# -- the case contract -------------------------------------------------------

def test_an_idea_case_holds():
    result = gates.case_check({"case": make_case(), "id": "nvda-190c"})
    assert result["ok"] is True
    assert result["data"]["violations"] == []


def test_invalidation_is_always_written():
    violations = gates.case_violations(make_case(invalidation="  "))
    assert any("prove it wrong" in v for v in violations)


def test_states_are_the_four_honest_ones():
    violations = gates.case_violations(make_case(state="open"))
    assert any("idea | watching | open-simulated | closed" in v for v in violations)


def test_open_simulated_requires_a_fill():
    violations = gates.case_violations(make_case(state="open-simulated"))
    assert any("needs its receipted fill" in v for v in violations)


def test_no_fill_before_open_simulated():
    violations = gates.case_violations(make_case(state="watching", fill=make_fill()))
    assert any("cannot exist before open-simulated" in v for v in violations)


def test_the_605_catch_a_fill_outside_its_market_is_rejected():
    # The historical catch: a fabricated $6.05 fill against a 3.90 × 4.00
    # live market. The gates exist so this stays impossible.
    case = make_case(state="open-simulated", fill=make_fill(price=6.05))
    violations = gates.case_violations(case)
    assert any(
        "$6.05 sits outside its receipted market 3.9 × 4" in v for v in violations
    )


def test_a_fill_inside_its_market_holds():
    case = make_case(state="open-simulated", fill=make_fill())
    assert gates.case_violations(case) == []


def test_a_fill_carries_no_confirmation_field():
    # Paper fills take no human confirmation — the market receipt is the
    # gate; asks are reserved for direction and real money.
    case = make_case(
        state="open-simulated",
        fill=make_fill(confirmed="answers/fill-nvda-190c"),
    )
    assert any(
        "nothing else, nothing missing" in v
        for v in gates.case_violations(case)
    )


def test_a_fill_clock_must_carry_its_timezone():
    case = make_case(
        state="open-simulated",
        fill=make_fill(observed_at="2026-08-12 14:31:22"),
    )
    assert any(
        "timezone-bearing" in v for v in gates.case_violations(case)
    )


def test_a_fill_clock_must_sit_in_the_regular_session():
    for off_session in (
        "2026-08-15T11:00:00-04:00",  # Saturday
        "2026-08-12T17:30:00-04:00",  # after the close
        "2026-08-12T09:15:00-04:00",  # before the open
    ):
        case = make_case(
            state="open-simulated", fill=make_fill(observed_at=off_session)
        )
        assert any(
            "outside the regular session" in v
            for v in gates.case_violations(case)
        ), off_session


def test_quantity_is_whole_contracts_one_through_one_hundred():
    for quantity in (0, 101, 2.5, True, "2"):
        case = make_case(
            state="open-simulated", fill=make_fill(quantity=quantity)
        )
        assert any(
            "1 through 100 whole contracts" in v
            for v in gates.case_violations(case)
        ), quantity


def test_an_exit_belongs_only_on_a_closed_case():
    case = make_case(
        state="open-simulated", fill=make_fill(), exit=make_fill(price=3.95)
    )
    assert any(
        "exit belongs only on a closed case" in v
        for v in gates.case_violations(case)
    )


def test_a_closed_case_with_entry_and_exit_holds():
    case = make_case(
        state="closed",
        fill=make_fill(),
        exit=make_fill(price=3.95),
    )
    assert gates.case_violations(case) == []


def test_unknown_fields_are_named():
    violations = gates.case_violations(make_case(pnl=1200))
    assert any("unknown field(s): pnl" in v for v in violations)


def test_a_fill_carries_exactly_its_receipt_fields():
    fill = make_fill()
    fill.pop("observed_at")
    violations = gates.fill_violations(fill)
    assert any("nothing else, nothing missing" in v for v in violations)


# -- the fill gate (fill_check) ----------------------------------------------

def test_fill_check_supports_a_price_inside_the_live_market():
    result = check_fill(FILL_ARGS, envelope(live_quote()))
    assert result["ok"] is True
    data = result["data"]
    assert data["verdict"] == "fill-supported"
    assert data["fill"] == {
        "price": 4.00, "bid": 3.90, "ask": 4.00, "quantity": 2,
        "observed_at": LIVE_CLOCK,
    }
    assert data["contract"] == "NVDA 20260814 190C"
    assert data["action"] == "buy"
    # No confirmation machinery: the receipt is the gate, not a question.
    assert "ask_card" not in data
    assert "3.9 × 4" in result["summary"]


def test_fill_check_rejects_605_against_the_live_market():
    result = check_fill({**FILL_ARGS, "price": 6.05}, envelope(live_quote()))
    assert result["ok"] is False
    assert "ask_card" not in result["data"]
    assert any("3.9 × 4" in gap for gap in result["gaps"])


def test_refusals_carry_their_verdict_in_data():
    # A program-backed confirmation card extracts result.data — the refusal
    # and its reasons must travel there, or the card would go silently stale.
    refusals = [
        check_fill({**FILL_ARGS, "price": 6.05}, envelope(live_quote())),
        check_fill(FILL_ARGS, envelope(live_quote(observed_at="2026-08-12T14:10:00-04:00"))),
        check_fill(FILL_ARGS, {"ok": False, "error": "TWS is not running"}),
        gates.fill_check({}, now=NOW),
    ]
    for result in refusals:
        assert result["data"]["verdict"] == "refused"
        assert result["data"]["reasons"] == result["gaps"]


def test_fill_check_rejects_a_stale_quote():
    stale = live_quote(observed_at="2026-08-12T14:10:00-04:00")  # ~22m old
    result = check_fill(FILL_ARGS, envelope(stale))
    assert result["ok"] is False
    assert any("no older than 120s" in gap for gap in result["gaps"])


def test_fill_check_rejects_an_off_session_quote():
    evening = live_quote(observed_at="2026-08-12T18:31:00-04:00")
    result = gates.fill_check(
        FILL_ARGS,
        fetch_quote=lambda request: envelope(evening),
        now=datetime.datetime(2026, 8, 12, 22, 32, 0, tzinfo=datetime.timezone.utc),
    )
    assert result["ok"] is False
    assert any("outside 9:30–16:00 ET" in gap for gap in result["gaps"])


def test_fill_check_rejects_a_one_sided_market():
    result = check_fill(FILL_ARGS, envelope(live_quote(bid=None, ask=None)))
    assert result["ok"] is False
    assert any("no market, no fill" in gap for gap in result["gaps"])


def test_a_failed_receipt_offers_no_confirmation():
    result = check_fill(
        FILL_ARGS, {"ok": False, "error": "TWS is not running"}
    )
    assert result["ok"] is False
    assert "ask_card" not in result["data"]
    assert any("TWS is not running" in gap for gap in result["gaps"])


def test_without_the_engine_no_fill_can_be_verified(monkeypatch):
    monkeypatch.setenv("ALPHALAB_AGENTS_REPO", "/nonexistent")
    monkeypatch.setenv("ALPHALAB_BRIDGE_REPO", "/nonexistent")
    result = gates.fill_check(FILL_ARGS, now=NOW)
    assert result["ok"] is False
    assert any(
        "no receipt, no confirmation may be offered" in gap
        for gap in result["gaps"]
    )


def test_fill_check_reads_a_plain_quote_receipt_too():
    result = check_fill(FILL_ARGS, live_quote())
    assert result["ok"] is True


def test_case_check_is_wired_into_the_inline_engine():
    import operations

    result = operations.run("case_check", {"case": make_case(), "id": "x"})
    assert result["ok"] is True


def test_bridged_receipts_answer_at_data_like_every_other_lane():
    # The single biggest agent friction in trials: guessing value_paths.
    # normalize() lifts the provider envelope's answer to data so
    # result.data.<field> works on every tool.
    import bridge

    envelope_reply = {
        "ok": True,
        "payload_json": json.dumps({"answer": {"quote": {"bid": 3.9, "ask": 4.0}}}),
    }
    normalized = bridge.normalize(dict(envelope_reply))
    assert normalized["data"]["quote"]["bid"] == 3.9
    assert normalized["payload_json"] == envelope_reply["payload_json"]
    # Inline receipts and malformed envelopes pass through untouched.
    inline = {"ok": True, "data": {"close": 217.5}}
    assert bridge.normalize(dict(inline)) == inline
    assert bridge.normalize({"ok": False, "payload_json": "not json"}) == {
        "ok": False, "payload_json": "not json"}


# -- the stream lane (fill_watch / live_quote) --------------------------------

def stream_invoke(rows, start_ok=True):
    def invoke(operation, arguments):
        assert operation == "ibkr.market_stream"
        if arguments.get("action") == "start":
            return {"ok": start_ok, "data": {"stream_id": "s-1"}}
        return {"ok": True, "data": {"rows": rows}}
    return invoke


def live_tick(**overrides):
    tick = {"bid": 3.90, "ask": 4.00, "last": 3.95, "close": 3.1,
            "bid_size": 12.0, "ask_size": 9.0, "model_iv": 0.31,
            "delta": 0.32, "quote_time": LIVE_CLOCK,
            "contract_key": "IBKR:OPT:NVDA:20260814:190:C:100:SMART:USD"}
    tick.update(overrides)
    return tick


def test_fill_watch_supports_a_price_inside_the_streamed_tick():
    result = gates.fill_watch(FILL_ARGS, invoke=stream_invoke([live_tick()]),
                              now=NOW)
    assert result["ok"] is True
    assert result["data"]["verdict"] == "fill-supported"
    assert result["data"]["fill"]["observed_at"] == LIVE_CLOCK
    assert result["data"]["stream"]["stream_id"] == "s-1"


def test_fill_watch_applies_the_same_rulebook_as_fill_check():
    outside = gates.fill_watch({**FILL_ARGS, "price": 6.05},
                               invoke=stream_invoke([live_tick()]), now=NOW)
    assert outside["ok"] is False
    assert any("3.9 × 4" in gap for gap in outside["gaps"])
    stale = gates.fill_watch(
        FILL_ARGS,
        invoke=stream_invoke([live_tick(quote_time="2026-08-12T14:10:00-04:00")]),
        now=NOW)
    assert stale["ok"] is False
    assert any("no older than 120s" in gap for gap in stale["gaps"])


def test_fill_watch_without_ticks_refuses_honestly():
    result = gates.fill_watch(FILL_ARGS, invoke=stream_invoke([]), now=NOW)
    assert result["ok"] is False
    assert any("no persisted ticks yet" in gap for gap in result["gaps"])


def test_live_quote_answers_the_freshest_tick_with_its_clock():
    result = gates.live_quote({"symbol": "NVDA", "sec_type": "OPT"},
                              invoke=stream_invoke([live_tick()]))
    assert result["ok"] is True
    quote = result["data"]["quote"]
    assert quote["bid"] == 3.90 and quote["ask"] == 4.00
    assert quote["observed_at"] == LIVE_CLOCK
    assert result["data"]["stream"]["stream_id"] == "s-1"


def test_the_bridge_lifts_stream_rows_to_data():
    import bridge

    reply = bridge.normalize({
        "ok": True,
        "payload_json": json.dumps({"answer": {"stream_id": "s-1"},
                                    "rows": [{"bid": 1.0}]}),
    })
    assert reply["data"]["rows"] == [{"bid": 1.0}]
    assert reply["data"]["stream_id"] == "s-1"
