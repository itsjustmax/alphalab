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
        "contract": "NVDA 20260814 190C",
        "price": 4.00, "bid": 3.90, "ask": 4.00, "quantity": 2,
        "observed_at": LIVE_CLOCK,
    }
    fill.update(overrides)
    return fill


def make_case(**overrides):
    case = {
        "contracts": ["NVDA 20260814 190C"],
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
        "exit belongs only on a closed trade" in v
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

def test_a_marketable_buy_fills_at_the_ask():
    # Limit at the ask: executes at the ask.
    result = check_fill(FILL_ARGS, envelope(live_quote()))
    assert result["ok"] is True
    data = result["data"]
    assert data["verdict"] == "fill-supported"
    assert data["fill"] == {
        "contract": "NVDA 20260814 190C",
        "price": 4.00, "bid": 3.90, "ask": 4.00, "quantity": 2,
        "observed_at": LIVE_CLOCK,
    }
    assert data["action"] == "buy" and data["limit"] == 4.00
    assert "ask_card" not in data
    assert "3.9 × 4" in result["summary"]
    # Limit ABOVE the ask: still executes AT the ask — price improvement,
    # like the market would actually do.
    generous = check_fill({**FILL_ARGS, "price": 6.05}, envelope(live_quote()))
    assert generous["ok"] is True
    assert generous["data"]["fill"]["price"] == 4.00
    assert generous["data"]["limit"] == 6.05


def test_a_limit_inside_the_spread_rests():
    resting = check_fill({**FILL_ARGS, "price": 3.95}, envelope(live_quote()))
    assert resting["ok"] is False
    assert "rests below the ask" in resting["summary"]


def test_a_sell_mirrors_against_the_bid():
    sold = check_fill({**FILL_ARGS, "action": "sell", "price": 3.85},
                      envelope(live_quote()))
    assert sold["ok"] is True
    assert sold["data"]["fill"]["price"] == 3.90  # executed at the bid
    resting = check_fill({**FILL_ARGS, "action": "sell", "price": 3.95},
                         envelope(live_quote()))
    assert resting["ok"] is False
    assert "rests above the bid" in resting["summary"]


def test_the_recorded_605_catch_still_holds_at_the_trade_level():
    # The gate now fills a marketable 6.05 AT 4.00 — but a RECORDED fill
    # claiming price 6.05 against a 3.90 × 4.00 market stays impossible.
    case = make_case(state="open-simulated", fill=make_fill(price=6.05))
    violations = gates.trade_violations(case)
    assert any("sits outside its receipted" in v for v in violations)


def test_refusals_carry_their_verdict_in_data():
    # A program-backed confirmation card extracts result.data — the refusal
    # and its reasons must travel there, or the card would go silently stale.
    refusals = [
        check_fill({**FILL_ARGS, "price": 3.00}, envelope(live_quote())),
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
    monkeypatch.setenv("ALPHALAB_ENGINE_CONFIG", "/nonexistent/engine.json")
    monkeypatch.setenv("ALPHALAB_AGENTS_REPO", "/nonexistent")
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
    # Steady state: ticks already flow, so no start call happens at all.
    result = gates.fill_watch(FILL_ARGS, invoke=stream_invoke([live_tick()]),
                              now=NOW)
    assert result["ok"] is True
    assert result["data"]["verdict"] == "fill-supported"
    assert result["data"]["fill"]["observed_at"] == LIVE_CLOCK
    assert result["data"]["stream"] == {}


def test_fill_watch_warms_the_stream_when_no_ticks_exist():
    calls = []

    def invoke(operation, arguments):
        calls.append(arguments.get("action"))
        if arguments.get("action") == "start":
            return {"ok": True, "data": {"stream_id": "s-1"}}
        # First latest: quiet; after the start: a live tick.
        rows = [live_tick()] if "start" in calls else []
        return {"ok": True, "data": {"rows": rows}}

    result = gates.fill_watch(FILL_ARGS, invoke=invoke, now=NOW)
    assert calls == ["latest", "start", "latest"]
    assert result["ok"] is True
    assert result["data"]["stream"]["stream_id"] == "s-1"


def test_fill_watch_applies_the_same_rulebook_as_fill_check():
    resting = gates.fill_watch({**FILL_ARGS, "price": 3.00},
                               invoke=stream_invoke([live_tick()]), now=NOW)
    assert resting["ok"] is False
    assert "rests below the ask" in resting["summary"]
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
    assert result["data"]["stream"] == {}


def test_live_quotes_batches_and_names_quiet_symbols():
    def invoke_batch(operations):
        replies = []
        for operation, arguments in operations:
            if arguments.get("action") == "start":
                replies.append({"ok": True, "data": {"stream_id": "s"}})
            elif arguments["symbol"] == "NVDA":
                replies.append({"ok": True, "data": {"rows": [live_tick()]}})
            else:
                replies.append({"ok": True, "data": {"rows": []}})
        return replies

    result = gates.live_quotes({"symbols": ["NVDA", "SPX"], "warm": True},
                               invoke_batch=invoke_batch)
    assert result["ok"] is True
    assert result["data"]["quotes"]["NVDA"]["last"] == 3.95
    assert result["data"]["quotes"]["NVDA"]["close"] == 3.1
    assert result["data"]["quotes"]["SPX"] is None
    assert any("SPX: stream warming" in gap for gap in result["gaps"])


def test_the_bridge_lifts_stream_rows_to_data():
    import bridge

    reply = bridge.normalize({
        "ok": True,
        "payload_json": json.dumps({"answer": {"stream_id": "s-1"},
                                    "rows": [{"bid": 1.0}]}),
    })
    assert reply["data"]["rows"] == [{"bid": 1.0}]
    assert reply["data"]["stream_id"] == "s-1"


# -- the engine contract (adapter + bridge) -----------------------------------

def test_the_adapter_grant_has_no_order_route():
    import adapter

    assert adapter.screen("ibkr.quote.snapshot") is None
    assert adapter.screen("ibkr.market_stream") is None
    for forbidden in ("ibkr.orders.submit", "ibkr.orders.modify_stop",
                      "trade.idea.save", "ibkr.account.summary", ""):
        refusal = adapter.screen(forbidden)
        assert refusal is not None, forbidden
    assert "no order route" in adapter.screen("ibkr.orders.submit")


def test_the_adapter_bounds_receipts_to_entry_size():
    import adapter

    oversized = {"ok": True, "summary": "big",
                 "rows": [{"i": i, "pad": "x" * 60} for i in range(500)],
                 "answer": {"blob": "y" * 300_000}}
    bounded = adapter.bound(oversized)
    assert len(bounded["rows"]) <= adapter.MAX_ROWS
    encoded = json.dumps(bounded, ensure_ascii=False)
    assert len(encoded.encode("utf-8")) <= adapter.MAX_RECEIPT_BYTES
    assert any("bounded" in warning for warning in bounded["warnings"])


def test_the_bridge_normalizes_the_native_envelope_too():
    import bridge

    native = {"ok": True, "operation": "ibkr.quote.snapshot",
              "answer": {"quote": {"bid": 3.9, "ask": 4.0}},
              "rows": [{"bid": 3.9}]}
    normalized = bridge.normalize(dict(native))
    assert normalized["data"]["quote"]["ask"] == 4.0
    assert normalized["data"]["rows"] == [{"bid": 3.9}]


def test_the_binding_resolves_config_then_legacy_env(tmp_path, monkeypatch):
    import bridge

    config = tmp_path / "engine.json"
    config.write_text(json.dumps({"python": "/somewhere/python",
                                  "package_src": "/somewhere/src",
                                  "home": "/somewhere/home"}))
    monkeypatch.setenv("ALPHALAB_ENGINE_CONFIG", str(config))
    monkeypatch.delenv("ALPHALAB_AGENTS_REPO", raising=False)
    bound = bridge.binding()
    assert bound["python"] == "/somewhere/python"
    # No config file: the legacy env pair still binds.
    monkeypatch.setenv("ALPHALAB_ENGINE_CONFIG", str(tmp_path / "missing.json"))
    monkeypatch.setenv("ALPHALAB_AGENTS_REPO", "/repo")
    monkeypatch.setenv("ALPHALAB_HOME", "/repo-home")
    bound = bridge.binding()
    assert bound["python"] == "/repo/.venv/bin/python"
    assert bound["home"] == "/repo-home"
    # Nothing at all: no binding, and available() says so.
    monkeypatch.delenv("ALPHALAB_AGENTS_REPO", raising=False)
    assert bridge.binding() is None
    assert bridge.available() is False


def test_the_adapter_redacts_local_paths():
    import adapter

    roots = (("/Users/someone/Alphalab/home", "~ALPHALAB_HOME"),
             ("/Users/someone/engine/src", "~ENGINE"),
             ("/Users/someone", "~"))
    receipt = {
        "summary": "failed reading /Users/someone/Alphalab/home/cache.db",
        "gaps": ["traceback at /Users/someone/engine/src/alphalab/x.py",
                 "wrote /Users/someone/notes.txt"],
    }
    scrubbed = adapter.redact(receipt, roots)
    assert scrubbed["summary"] == "failed reading ~ALPHALAB_HOME/cache.db"
    assert scrubbed["gaps"][0] == "traceback at ~ENGINE/alphalab/x.py"
    assert scrubbed["gaps"][1] == "wrote ~/notes.txt"


def test_a_trade_names_one_through_five_contracts():
    for contracts in ([], ["a"] * 6, "NVDA", [""], None):
        violations = gates.trade_violations(make_case(contracts=contracts))
        assert any(
            "one through five exact contracts" in v for v in violations
        ), contracts
    spread = make_case(contracts=["NVDA 20260821 230C", "NVDA 20260821 240C"])
    assert gates.trade_violations(spread) == []


def test_a_fill_must_name_one_of_the_trades_contracts():
    trade = make_case(state="open-simulated",
                      fill=make_fill(contract="AMD 20260821 175C"))
    violations = gates.trade_violations(trade)
    assert any("not one of this trade's contracts" in v for v in violations)


# -- web_fetch: the desk learns from the member's links -----------------------

def test_web_fetch_refuses_local_and_private_addresses():
    import web

    for url in ("http://localhost:8642/x", "http://127.0.0.1/x",
                "http://192.168.1.10/a", "http://10.0.0.5/b",
                "http://169.254.1.1/c", "ftp://example.com/d",
                "http://mymac.local/e", ""):
        assert web.blocked_reason(url) is not None, url
    assert web.blocked_reason("https://observablehq.com/@d3/gallery") is None


def test_web_fetch_strips_html_but_keeps_the_code():
    import web

    markup = ("<html><head><title>Chart demo</title>"
              "<style>body{color:red}</style></head>"
              "<body><p>A streamgraph.</p>"
              "<script>const layers = d3.stack().offset(d3.stackOffsetWiggle);"
              "</script></body></html>")
    result = web.web_fetch(
        {"url": "https://example.com/demo"},
        opener=lambda url: (url, "text/html; charset=utf-8",
                            markup.encode()))
    assert result["ok"] is True
    assert result["data"]["title"] == "Chart demo"
    assert "A streamgraph." in result["data"]["text"]
    assert "stackOffsetWiggle" in result["data"]["text"]  # code kept
    assert "color:red" not in result["data"]["text"]      # styles gone
    assert any("no scripts ran" in gap for gap in result["gaps"])


def test_web_fetch_bounds_and_names_truncation():
    import web

    huge = b"x" * 50_000
    result = web.web_fetch(
        {"url": "https://example.com/big", "max_chars": 1_000},
        opener=lambda url: (url, "text/plain", huge))
    assert result["data"]["chars"] == 1_000
    assert any("bounded to 1000 chars" in gap for gap in result["gaps"])


def test_indices_stream_as_indices_not_stocks():
    requests = []

    def invoke_batch(operations):
        requests.extend(arguments for _, arguments in operations)
        return [{"ok": True, "data": {"rows": []}} for _ in operations]

    gates.live_quotes({"symbols": ["SPX", "NVDA"], "warm": True},
                      invoke_batch=invoke_batch)
    spx = [r for r in requests if r.get("symbol") == "SPX"]
    nvda = [r for r in requests if r.get("symbol") == "NVDA"]
    assert spx and all(r.get("sec_type") == "IND" for r in spx)
    assert nvda and all("sec_type" not in r for r in nvda)


# -- forms: minimal fields in, finished cells out ------------------------------

def test_the_live_desks_object_contract_normalizes_through_the_form():
    import forms

    # The exact shape that broke the live desk: a structured contract
    # where a string belongs. The form lane coerces it.
    trade, violations = forms.trade_from_form({
        "contracts": [{"symbol": "NVDA", "sec_type": "OPT",
                       "expiration": "20260821", "strike": 235, "right": "C"}],
        "thesis": "negative gamma amplifies an NVDA push",
        "invalidation": "regime flips positive on two receipts",
    })
    assert violations == []
    assert trade["contracts"] == ["NVDA 20260821 235C"]
    assert trade["state"] == "idea"


def test_a_bad_trade_form_names_its_problems_teachably():
    import forms

    trade, violations = forms.trade_from_form({"contracts": [123],
                                               "thesis": "t"})
    assert any('like "NVDA 20260821 235C"' in v for v in violations)
    assert any("prove it wrong" in v for v in violations)


def test_templates_expand_minimal_fields_into_finished_cells():
    import forms

    target, cell, violations = forms.expand(
        {"template": "live-chart", "id": "nvda", "symbol": "NVDA"})
    assert violations == []
    assert target == "widgets/chart-nvda"
    assert cell["chart"] == {"symbol": "NVDA", "days": 60}
    assert cell["title"] == "NVDA — daily"
    target, cell, violations = forms.expand(
        {"template": "paper-order", "trade": "nvda-gamma-call",
         "symbol": "NVDA", "expiration": "20260821", "strike": 235,
         "right": "C", "price": 1.05, "quantity": 1})
    assert target == "widgets/fill-nvda-gamma-call"
    assert cell["refresh"]["args"]["contract"] == "NVDA 20260821 235C"
    assert cell["refresh"]["args"]["action"] == "buy"


def test_a_form_missing_fields_or_template_is_named():
    import forms

    _, _, violations = forms.expand({"template": "live-chart", "id": "x"})
    assert any("still needs: symbol" in v for v in violations)
    _, _, violations = forms.expand({"template": "nope"})
    assert any("no template named" in v for v in violations)


def test_desk_templates_extend_the_builtins():
    import forms

    target, cell, violations = forms.expand(
        {"template": "my-note", "id": "x", "body": "hello"},
        templates={"my-note": {"target": "widgets/note-{id}",
                               "fields": {"id": "slug", "body": "text"},
                               "cell": {"kind": "text", "title": "Note",
                                        "body": {"$": "body"}}}})
    assert violations == []
    assert target == "widgets/note-x" and cell["body"] == "hello"


def test_contract_labels_match_across_notations():
    assert gates.contracts_match("NVDA 8/21 235C", "NVDA 20260821 235C")
    assert gates.contracts_match("NVDA 20260821 235C", "NVDA 20260821 235C")
    assert not gates.contracts_match("NVDA 8/21 240C", "NVDA 20260821 235C")
    assert not gates.contracts_match("AMD 8/21 235C", "NVDA 20260821 235C")
    assert not gates.contracts_match("NVDA 8/14 235C", "NVDA 20260821 235C")
    assert gates.contracts_match("NVDA shares", "NVDA shares")


def test_a_fill_under_another_notation_belongs_to_its_trade():
    fill = make_fill(contract="NVDA 8/14 190C")
    case = make_case(state="open-simulated", fill=fill)
    assert gates.trade_violations(case) == []
