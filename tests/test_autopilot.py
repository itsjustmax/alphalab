"""The autopilot policy, pinned.

decide() is the whole policy: when the desk takes a revision turn with no
member present. The first move on a fresh desk is the member's; answers
and the agent's own clock come first; everything is bounded by the
minimum gap and the daily budget.
"""

import datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "engine"))

import autopilot


# A Wednesday during the regular session (14:31 ET) and overnight (23:00 ET).
RTH = datetime.datetime(2026, 8, 12, 18, 31, 0, tzinfo=datetime.timezone.utc)
OVERNIGHT = datetime.datetime(2026, 8, 13, 3, 0, 0, tzinfo=datetime.timezone.utc)


def state(**overrides):
    base = {"date": "2026-08-12", "builds_today": 3,
            "last_turn": "2026-08-12T17:00:00+00:00", "seen_answers": []}
    base.update(overrides)
    return base


ACTIVE_DESK = {"intake/watchlist": "NVDA", "widgets/brief": {"kind": "text"}}


def test_a_fresh_desk_waits_for_the_member():
    action, reason = autopilot.decide({}, state(last_turn=None), RTH)
    assert action == "wait"
    assert "the first move is theirs" in reason


def test_first_turn_fires_once_intake_is_answered():
    action, reason = autopilot.decide(
        {"intake/watchlist": "NVDA"}, state(last_turn=None), RTH)
    assert (action, reason) == ("build", "first turn on this desk")


def test_the_minimum_gap_suppresses_everything():
    recent = state(last_turn="2026-08-12T18:25:00+00:00")
    context = {**ACTIVE_DESK, "answers/fill-x": "Confirm fill",
               "desk/next_check": "2026-08-12T18:00:00+00:00"}
    action, reason = autopilot.decide(context, recent, RTH)
    assert action == "wait"
    assert "minimum gap" in reason


def test_the_daily_budget_is_a_hard_ceiling():
    action, reason = autopilot.decide(
        {**ACTIVE_DESK, "answers/fill-x": "Confirm fill"},
        state(builds_today=36), RTH)
    assert action == "wait"
    assert "budget" in reason


def test_an_uncovered_answer_triggers_a_turn():
    action, reason = autopilot.decide(
        {**ACTIVE_DESK, "answers/fill-x": "Confirm fill"}, state(), RTH)
    assert action == "build"
    assert "answers/fill-x" in reason


def test_a_covered_answer_does_not_retrigger():
    # 26 minutes quiet: outside the minimum gap, inside the RTH maintenance
    # gap — so a covered answer alone must not fire a turn.
    settled = state(seen_answers=["answers/fill-x"],
                    last_turn="2026-08-12T18:05:00+00:00")
    action, _ = autopilot.decide(
        {**ACTIVE_DESK, "answers/fill-x": "Confirm fill"}, settled, RTH)
    assert action == "wait"


def test_the_desks_own_clock_comes_due():
    settled = state(last_turn="2026-08-12T18:05:00+00:00")
    context = {**ACTIVE_DESK, "desk/next_check": "2026-08-12T18:20:00+00:00"}
    action, reason = autopilot.decide(context, settled, RTH)
    assert (action, reason) == ("build", "the desk's own next-check clock came due")
    context["desk/next_check"] = "2026-08-12T19:30:00+00:00"
    action, _ = autopilot.decide(context, settled, RTH)
    assert action == "wait"


def test_quiet_maintenance_matches_the_session_phase():
    # 91 minutes quiet in the regular session: past the 30m gap.
    action, reason = autopilot.decide(ACTIVE_DESK, state(), RTH)
    assert action == "build"
    assert "regular session" in reason
    # The same 91 minutes overnight sits inside the 4h gap.
    overnight_state = state(last_turn="2026-08-13T01:29:00+00:00")
    action, reason = autopilot.decide(ACTIVE_DESK, overnight_state, OVERNIGHT)
    assert (action, reason) == ("wait", "nothing due")
    # Five hours overnight is past it.
    stale = state(last_turn="2026-08-12T22:00:00+00:00")
    action, reason = autopilot.decide(ACTIVE_DESK, stale, OVERNIGHT)
    assert action == "build"
    assert "overnight" in reason


def test_a_new_trading_date_resets_the_budget():
    # 23:00 ET on the 12th is still the 12th: no reset.
    same_day = autopilot.roll_date(state(builds_today=36), OVERNIGHT)
    assert same_day["builds_today"] == 36
    # 08:00 ET on the 13th is a new trading date: budget back to zero.
    next_day = datetime.datetime(2026, 8, 13, 12, 0, 0,
                                 tzinfo=datetime.timezone.utc)
    rolled = autopilot.roll_date(state(builds_today=36), next_day)
    assert rolled["builds_today"] == 0
    assert rolled["date"] == "2026-08-13"


def test_a_pending_trigger_summons_the_steward():
    context = dict(ACTIVE_DESK)
    context["triggers/nvda"] = {"reason": "stop within 2 ticks",
                                "from": "bot"}
    action, reason = autopilot.decide(context, state(), RTH)
    assert (action, reason) == ("build", "steward summoned: triggers/nvda")


def test_a_retired_trigger_summons_nobody():
    context = dict(ACTIVE_DESK)
    context["triggers/nvda"] = None
    _, reason = autopilot.decide(context, state(), RTH)
    assert "steward" not in reason


def test_the_audit_names_only_the_broken_cases():
    cases = {"trades/good": {"state": "idea"}, "trades/bad": {"state": "open"}}
    verdicts = {"trades/good": [], "trades/bad": ["no receipted fill"]}
    result = autopilot.audit_violations(cases, lambda key, case: verdicts[key])
    assert result == {"trades/bad": ["no receipted fill"]}


def test_the_audit_runs_once_per_case_change(tmp_path):
    pilot = autopilot.Pilot("http://x", "t", "env", 36,
                            str(tmp_path / "state.json"))
    calls = []

    def fake_api(method, path, body=None):
        calls.append((method, path))
        if "tools/trade_check" in path:
            return {"ok": True, "result": {"ok": True,
                                           "data": {"violations": []}}}
        return {"ok": True}

    pilot._api = fake_api
    context = {"trades/nvda": {"state": "idea"}}
    now = datetime.datetime(2026, 8, 12, 18, 31, 0,
                            tzinfo=datetime.timezone.utc)
    pilot.audit(context, now)
    first = len(calls)
    pilot.audit(context, now)          # unchanged cases: no rework
    assert len(calls) == first
    context["trades/nvda"] = {"state": "watching"}
    pilot.audit(context, now)          # changed: audited again
    assert len(calls) > first
    audits = [c for c in calls if c == ("POST", "/environments/env/context")]
    assert len(audits) == 2


def make_order(check_verdict="fill-supported", observed_at=None, kind="order"):
    fill = {"contract": "NVDA 20260814 190C", "price": 4.0, "bid": 3.9,
            "ask": 4.0, "quantity": 2,
            "observed_at": observed_at or "2026-08-12T14:30:30-04:00"}
    return {"kind": kind, "title": "order",
            "check": {"verdict": check_verdict, "contract": "NVDA 20260814 190C",
                      "action": "buy", "fill": fill},
            "refresh": {"tool": "fill_check", "args": {}}}


WATCHING_CASE = {"contracts": ["NVDA 20260814 190C"], "thesis": "t",
                 "evidence": [], "invalidation": "close under 185",
                 "state": "watching", "fill": None, "exit": None}


def test_a_supported_fresh_order_records_the_fill():
    # RTH: 2026-08-12 14:31 ET; receipt 30s old.
    context = {"widgets/fill-nvda-190c": make_order(),
               "trades/nvda-190c": dict(WATCHING_CASE)}
    recordings = autopilot.supported_orders(context, RTH)
    assert len(recordings) == 1
    recording = recordings[0]
    assert recording["case_key"] == "trades/nvda-190c"
    assert recording["card_key"] == "widgets/fill-nvda-190c"
    assert recording["case"]["state"] == "open-simulated"
    assert recording["case"]["fill"]["price"] == 4.0
    assert "confirmed" not in recording["case"]["fill"]


def test_orders_wait_on_stale_refused_or_missing_gates():
    fresh_case = {"trades/nvda-190c": dict(WATCHING_CASE)}
    stale = {"widgets/fill-nvda-190c":
             make_order(observed_at="2026-08-12T14:10:00-04:00"), **fresh_case}
    refused = {"widgets/fill-nvda-190c":
               make_order(check_verdict="refused"), **fresh_case}
    not_an_order = {"widgets/fill-nvda-190c": make_order(kind="ask"), **fresh_case}
    caseless = {"widgets/fill-nvda-190c": make_order()}
    for context in (stale, refused, not_an_order, caseless):
        assert autopilot.supported_orders(context, RTH) == []


def test_an_exit_order_closes_an_open_case():
    open_case = {**WATCHING_CASE, "state": "open-simulated",
                 "fill": {"contract": "NVDA 20260814 190C", "price": 3.5, "bid": 3.4,
                          "ask": 3.5, "quantity": 2,
                          "observed_at": "2026-08-11T14:00:00-04:00"}}
    context = {"widgets/fill-nvda-190c-exit": make_order(),
               "trades/nvda-190c": open_case}
    recordings = autopilot.supported_orders(context, RTH)
    assert len(recordings) == 1
    assert recordings[0]["case"]["state"] == "closed"
    assert recordings[0]["case"]["exit"]["price"] == 4.0
    assert recordings[0]["case"]["fill"]["price"] == 3.5  # entry untouched
    # An exit against a case that is not open records nothing.
    context["trades/nvda-190c"] = dict(WATCHING_CASE)
    assert autopilot.supported_orders(context, RTH) == []


def test_flow_mode_emits_events_instead_of_dispatching(tmp_path):
    """With a platform flow on the harness, the pilot writes EVENT
    entries (kind-tagged triggers) and never fires builds itself."""

    pilot = autopilot.Pilot("http://x", "t", "env", 36,
                            str(tmp_path / "state.json"))
    open_case = {**WATCHING_CASE, "state": "open-simulated",
                 "fill": {"contract": "NVDA 20260814 190C", "price": 3.5,
                          "bid": 3.4, "ask": 3.5, "quantity": 2,
                          "observed_at": "2026-08-11T14:00:00-04:00"}}
    context = {"widgets/fill-nvda-190c-exit": make_order(),
               "trades/nvda-190c": open_case,
               "intake/watchlist": "NVDA"}
    calls = []

    def api(method, path, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return {"context": context, "flow": 11}
        if path.endswith("/context") and body:
            context[body["key"]] = body["value"]
        return {}

    pilot._api = api
    pilot._save = lambda: None
    action, _ = pilot.tick(RTH)
    assert action == "flow"
    assert not any("/build" in c[1] or "/invoke" in c[1] for c in calls)
    event = context.get("triggers/nvda-190c-cycle-closed")
    assert event and event["kind"] == "cycle-closed"
    assert event["trade_id"] == "nvda-190c"


def test_a_recorded_fill_earns_a_narration_turn():
    settled = state(last_turn="2026-08-12T18:05:00+00:00",
                    unnarrated_fills=["trades/nvda-190c"])
    action, reason = autopilot.decide(ACTIVE_DESK, settled, RTH)
    assert action == "build"
    assert "paper fill recorded: trades/nvda-190c" == reason.replace("paper fill recorded: ", "paper fill recorded: ")
    assert "trades/nvda-190c" in reason


def test_the_stream_lane_records_and_releases_the_subscription(tmp_path):
    pilot = autopilot.Pilot("http://x", "t", "env", 36,
                            str(tmp_path / "state.json"))
    calls = []
    order_args = {"symbol": "NVDA", "sec_type": "OPT",
                  "expiration": "20260814", "strike": 190, "right": "C",
                  "price": 4.0, "quantity": 2, "action": "buy",
                  "contract": "NVDA 20260814 190C"}
    live_check = {"verdict": "fill-supported", "contract": "NVDA 20260814 190C",
                  "action": "buy", "stream": {"stream_id": "s-1"},
                  "fill": {"contract": "NVDA 20260814 190C", "price": 4.0, "bid": 3.9,
                           "ask": 4.0, "quantity": 2,
                           "observed_at": "2026-08-12T14:30:30-04:00"}}

    def fake_api(method, path, body=None):
        calls.append((path, body))
        if path.endswith("/tools/fill_watch"):
            return {"ok": True, "result": {"ok": True, "data": live_check}}
        if path.endswith("/tools/trade_check"):
            return {"ok": True, "result": {"ok": True, "data": {"violations": []}}}
        return {"ok": True}

    pilot._api = fake_api
    context = {
        "widgets/fill-nvda-190c": {"kind": "order",
                                   "refresh": {"tool": "fill_watch",
                                               "args": order_args}},
        "trades/nvda-190c": dict(WATCHING_CASE),
    }
    recordings = pilot.record_orders(context, RTH)
    assert len(recordings) == 1
    case_writes = [body for path, body in calls
                   if path.endswith("/context") and body
                   and body.get("key") == "trades/nvda-190c"]
    assert case_writes and case_writes[0]["value"]["state"] == "open-simulated"
    stops = [body for path, body in calls
             if path.endswith("/tools/market_stream")]
    assert stops and stops[0]["args"]["action"] == "stop"
    assert stops[0]["args"]["symbol"] == "NVDA"


def test_stream_keys_match_their_orders():
    option_key = "IBKR:OPT:NVDA:20260821:230:C:100:SMART:USD"
    option_args = {"symbol": "NVDA", "sec_type": "OPT",
                   "expiration": "20260821", "strike": 230, "right": "C"}
    assert autopilot.stream_matches_order(option_key, option_args)
    assert autopilot.stream_matches_order(option_key,
                                          {**option_args, "strike": 230.0})
    assert not autopilot.stream_matches_order(option_key,
                                              {**option_args, "strike": 235})
    assert not autopilot.stream_matches_order(option_key,
                                              {**option_args, "right": "P"})
    stock_key = "IBKR:STK:NVDA:SMART:USD"
    assert autopilot.stream_matches_order(stock_key, {"symbol": "NVDA"})
    assert not autopilot.stream_matches_order(stock_key, {"symbol": "AMD"})
    assert not autopilot.stream_matches_order(option_key, {"symbol": "NVDA"})


def test_the_sweep_releases_only_orphaned_desk_streams(tmp_path):
    pilot = autopilot.Pilot("http://x", "t", "env", 36,
                            str(tmp_path / "state.json"))
    calls = []
    active_rows = [
        {"stream_id": "s-armed", "owner": "alphalab-desk",
         "contract_key": "IBKR:OPT:NVDA:20260821:230:C:100:SMART:USD"},
        {"stream_id": "s-orphan", "owner": "alphalab-desk",
         "contract_key": "IBKR:OPT:AMD:20260821:175:C:100:SMART:USD"},
        {"stream_id": "s-foreign", "owner": "someone-else",
         "contract_key": "IBKR:STK:SPY:SMART:USD"},
    ]

    def fake_api(method, path, body=None):
        calls.append((path, body))
        if path.endswith("/tools/market_stream"):
            if body["args"]["action"] == "list_active":
                return {"ok": True, "result": {"ok": True,
                                               "data": {"rows": active_rows}}}
            return {"ok": True, "result": {"ok": True, "data": {}}}
        return {"ok": True}

    pilot._api = fake_api
    context = {"widgets/fill-nvda-230c": {
        "kind": "order",
        "refresh": {"tool": "fill_watch",
                    "args": {"symbol": "NVDA", "sec_type": "OPT",
                             "expiration": "20260821", "strike": 230,
                             "right": "C", "price": 2.3, "quantity": 2}}}}
    stopped = pilot.sweep_streams(context)
    assert stopped == 1
    stops = [body["args"] for path, body in calls
             if path.endswith("/tools/market_stream")
             and body["args"].get("action") == "stop"]
    assert stops == [{"action": "stop", "stream_id": "s-orphan"}]


def test_a_member_turn_holds_the_autopilot_off():
    # The member (or their client) just fired a build: desk/member_turn is
    # stamped, and the autopilot must not race it — even on a fresh desk.
    fresh = state(last_turn=None)
    context = {**ACTIVE_DESK, "desk/member_turn": "2026-08-12T18:25:00+00:00"}
    action, reason = autopilot.decide(context, fresh, RTH)
    assert action == "wait" and "minimum gap" in reason
    # An old member turn does not hold the desk forever — the next build
    # is honest maintenance, not a "first turn" that rebuilds from scratch.
    context["desk/member_turn"] = "2026-08-12T16:00:00+00:00"
    action, reason = autopilot.decide(context, fresh, RTH)
    assert action == "build" and "maintenance" in reason


def test_watchlist_streams_are_held_not_swept(tmp_path):
    pilot = autopilot.Pilot("http://x", "t", "env", 36,
                            str(tmp_path / "state.json"))
    stops = []
    active = [
        {"stream_id": "s-nvda", "owner": "alphalab-desk",
         "contract_key": "IBKR:STK:NVDA:SMART:USD"},
        {"stream_id": "s-spx", "owner": "alphalab-desk",
         "contract_key": "IBKR:IND:SPX:CBOE:USD"},
        {"stream_id": "s-orphan", "owner": "alphalab-desk",
         "contract_key": "IBKR:STK:TSLA:SMART:USD"},
    ]

    def fake_api(method, path, body=None):
        if path.endswith("/tools/market_stream"):
            if body["args"]["action"] == "list_active":
                return {"ok": True, "result": {"ok": True,
                                               "data": {"rows": active}}}
            stops.append(body["args"]["stream_id"])
            return {"ok": True, "result": {"ok": True, "data": {}}}
        return {"ok": True}

    pilot._api = fake_api
    context = {"watchlist": ["NVDA", "SPX"]}
    stopped = pilot.sweep_streams(context)
    assert stopped == 1
    assert stops == ["s-orphan"]  # SPX (index) and NVDA both held


def test_forms_become_cells_and_bad_forms_learn_their_errors(tmp_path):
    pilot = autopilot.Pilot("http://x", "t", "env", 36,
                            str(tmp_path / "state.json"))
    writes = {}

    def fake_api(method, path, body=None):
        if path.endswith("/context") and body:
            writes[body["key"]] = body["value"]
            return {"ok": True}
        return {"ok": True, "result": {"ok": True, "data": {}}}

    pilot._api = fake_api
    context = {
        "forms/trade/spread": {
            "contracts": [{"symbol": "NVDA", "sec_type": "OPT",
                           "expiration": "20260821", "strike": 235,
                           "right": "C"}],
            "thesis": "t", "invalidation": "break named"},
        "forms/chart-amd": {"template": "live-chart", "id": "amd",
                            "symbol": "AMD"},
        "forms/broken": {"template": "live-chart", "id": "x"},
    }
    applied = pilot.apply_forms(context, RTH)
    assert applied == 2
    assert writes["trades/spread"]["contracts"] == ["NVDA 20260821 235C"]
    assert writes["forms/trade/spread"] is None          # form retired
    assert writes["widgets/chart-amd"]["chart"]["symbol"] == "AMD"
    assert "still needs: symbol" in writes["forms/broken"]["errors"][0]


def test_extra_holders_protect_another_desks_streams(tmp_path):
    # Two desks: this pilot's context holds nothing, but the fleet passes
    # the OTHER desk's holders — its streams must survive the sweep.
    pilot = autopilot.Pilot("http://x", "t", "env", 36,
                            str(tmp_path / "state.json"))
    stops = []
    active = [
        {"stream_id": "s-other-desk", "owner": "alphalab-desk",
         "contract_key": "IBKR:OPT:NVDA:20260821:235:C:100:SMART:USD"},
        {"stream_id": "s-orphan", "owner": "alphalab-desk",
         "contract_key": "IBKR:STK:TSLA:SMART:USD"},
    ]

    def fake_api(method, path, body=None):
        if path.endswith("/tools/market_stream"):
            if body["args"]["action"] == "list_active":
                return {"ok": True, "result": {"ok": True,
                                               "data": {"rows": active}}}
            stops.append(body["args"]["stream_id"])
            return {"ok": True, "result": {"ok": True, "data": {}}}
        return {"ok": True}

    pilot._api = fake_api
    stopped = pilot.sweep_streams(None, extra_holders=[
        {"symbol": "NVDA", "sec_type": "OPT", "expiration": "20260821",
         "strike": 235, "right": "C"}])
    assert stopped == 1
    assert stops == ["s-orphan"]


def test_fleet_discovers_only_this_harness(monkeypatch, tmp_path):
    listed = [
        {"environment_id": "desk-1", "harness": "AlphaLab"},
        {"environment_id": "other", "harness": "Campfire"},
        {"environment_id": "desk-2", "harness": "AlphaLab"},
    ]
    monkeypatch.setattr(autopilot.Pilot, "_api",
                        lambda self, method, path, body=None: listed)
    fleet = autopilot.Fleet("http://x", "t", 36, str(tmp_path))
    assert fleet.discover() == ["desk-1", "desk-2"]
    # A pinned fleet ignores discovery entirely.
    pinned = autopilot.Fleet("http://x", "t", 36, str(tmp_path),
                             environment="desk-9")
    assert pinned.discover() == ["desk-9"]


def test_fleet_keeps_one_state_file_per_desk(monkeypatch, tmp_path):
    fleet = autopilot.Fleet("http://x", "t", 36, str(tmp_path))
    a = fleet._pilot("desk-1")
    b = fleet._pilot("desk-2")
    assert a is not b
    assert a.state_path != b.state_path
    assert fleet._pilot("desk-1") is a


def test_audit_ledger_appends_verdict_lines(tmp_path):
    import datetime
    ledger = str(tmp_path / "ledger.jsonl")
    now = datetime.datetime(2026, 8, 13, 1, 0,
                            tzinfo=datetime.timezone.utc)
    autopilot.append_audit_ledger("env-1", now, True, [], path=ledger)
    autopilot.append_audit_ledger("env-1", now, False, ["trades/x"],
                                  path=ledger)
    import json as json_module
    lines = [json_module.loads(line) for line in
             open(ledger, encoding="utf-8")]
    assert [line["clean"] for line in lines] == [True, False]
    assert lines[1]["broken"] == ["trades/x"]


def test_curation_verdicts_join_runs_to_audits():
    import importlib.util
    import os
    spec = importlib.util.spec_from_file_location(
        "curate_corpus", os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "tools", "curate_corpus.py"))
    curate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(curate)

    def entry(env, iso, clean):
        import gates
        return (env, gates.parse_clock(iso), clean)

    ledger = [
        entry("desk-a", "2026-08-13T10:05:00+00:00", True),
        entry("desk-a", "2026-08-13T11:00:00+00:00", False),
        entry("desk-b", "2026-08-13T10:06:00+00:00", False),
    ]
    run = {"at": "2026-08-13T10:00:00+00:00", "environment": "desk-a"}
    assert curate.verdict_for(run, ledger) == "accepted"
    later = {"at": "2026-08-13T10:30:00+00:00", "environment": "desk-a"}
    assert curate.verdict_for(later, ledger) == "rejected"
    other_desk = {"at": "2026-08-13T10:00:00+00:00",
                  "environment": "desk-b"}
    assert curate.verdict_for(other_desk, ledger) == "rejected"
    # an audit far past the window claims nothing
    stale = {"at": "2026-08-13T05:00:00+00:00", "environment": "desk-a"}
    assert curate.verdict_for(stale, ledger, window_minutes=60) == "unjudged"
    unattributed = {"at": "2026-08-13T10:00:00+00:00"}
    assert curate.verdict_for(unattributed, ledger) == "unjudged"


def test_one_contract_one_live_trade_in_the_forms_lane(tmp_path):
    pilot = autopilot.Pilot("http://x", "t", "env", 36,
                            str(tmp_path / "state.json"))
    writes, said = {}, []

    def fake_api(method, path, body=None):
        if path.endswith("/say"):
            said.append(body["text"])
            return {}
        if path.endswith("/context") and body is not None:
            writes[body["key"]] = body["value"]
            return {}
        return {}

    pilot._api = fake_api
    context = {
        "trades/nvda-gamma": {
            "contracts": ["NVDA 20260821 235C"], "thesis": "old thesis",
            "invalidation": "old line", "state": "open-simulated",
            "fill": {"contract": "NVDA 20260821 235C", "price": 0.96,
                     "bid": 0.94, "ask": 0.96, "quantity": 1,
                     "observed_at": "2026-08-12T15:00:00-04:00"}},
        "forms/trade/nvda-rival": {
            "contracts": ["NVDA 8/21 235C"], "thesis": "new thinking",
            "invalidation": "new line"},
    }
    import datetime
    now = datetime.datetime(2026, 8, 13, 12, 0,
                            tzinfo=datetime.timezone.utc)
    pilot.apply_forms(context, now)
    assert "trades/nvda-rival" not in writes, "no rival idea"
    amended = writes["trades/nvda-gamma"]
    assert amended["thesis"] == "new thinking"
    assert amended["invalidation"] == "new line"
    assert amended["fill"]["price"] == 0.96  # the fill survives amends
    assert amended["state"] == "open-simulated"
    assert writes["forms/trade/nvda-rival"] is None  # form retired
    assert any("one live trade" in text.lower() for text in said)


def test_duplicate_contracts_are_audited():
    cases = {
        "trades/a": {"contracts": ["NVDA 20260821 235C"], "state": "idea"},
        "trades/b": {"contracts": ["NVDA 8/21 235C"], "state": "watching"},
        "trades/c": {"contracts": ["NVDA 20260821 235C"],
                     "state": "closed"},  # closed trades are history
    }
    found = autopilot.duplicate_contract_violations(cases)
    assert set(found) == {"trades/a", "trades/b"}
    assert "trades/c" not in found


def test_stream_health_names_the_stale(tmp_path):
    import datetime
    pilot = autopilot.Pilot("http://x", "t", "env", 36,
                            str(tmp_path / "state.json"))
    writes = {}
    now = datetime.datetime(2026, 8, 13, 14, 0,
                            tzinfo=datetime.timezone.utc)

    def fake_api(method, path, body=None):
        if path.endswith("/tools/live_quotes"):
            return {"result": {"ok": True, "data": {"quotes": {
                "NVDA": {"last": 224.3,
                         "observed_at": "2026-08-13T13:59:50+00:00"},
                "SPX": {"last": 7748.5,
                        "observed_at": "2026-08-13T12:00:00+00:00"},
                "AMD": None}}}}
        if path.endswith("/context") and body is not None:
            writes[body["key"]] = body["value"]
            return {}
        return {}

    pilot._api = fake_api
    report = pilot.stream_health({"watchlist": ["NVDA", "SPX", "AMD"]}, now)
    assert report["stale"] == ["AMD", "SPX"]  # silent + 2h-old tick
    assert writes["desk/streams"]["quotes"]["NVDA"]["age_seconds"] == 10
    assert writes["desk/streams"]["quotes"]["AMD"] is None


def test_stale_streams_are_rewarmed_throttled(tmp_path):
    import datetime
    pilot = autopilot.Pilot("http://x", "t", "env", 36,
                            str(tmp_path / "state.json"))
    warms = []
    now = datetime.datetime(2026, 8, 13, 14, 0,
                            tzinfo=datetime.timezone.utc)

    def fake_api(method, path, body=None):
        if path.endswith("/tools/market_stream"):
            warms.append((body["args"]["action"], body["args"]["symbol"]))
            return {"result": {"ok": True, "data": {}}}
        if path.endswith("/tools/live_quotes"):
            return {"result": {"ok": True, "data": {"quotes": {
                "NVDA": {"last": 224.3,
                         "observed_at": "2026-08-13T02:00:00+00:00"}}}}}
        return {}

    pilot._api = fake_api
    pilot.stream_health({"watchlist": ["NVDA"]}, now)
    # stop-then-start: a phantom registration must be cleared first
    assert warms == [("stop", "NVDA"), ("start", "NVDA")]
    pilot.stream_health({"watchlist": ["NVDA"]}, now)
    assert len(warms) == 2, "throttled: no second attempt inside 5m"
    later = now + datetime.timedelta(minutes=6)
    pilot.stream_health({"watchlist": ["NVDA"]}, later)
    assert len(warms) == 4, "a new attempt after the throttle window"


def test_retired_trades_are_not_audited(tmp_path):
    import datetime
    pilot = autopilot.Pilot("http://x", "t", "env", 36,
                            str(tmp_path / "state.json"))
    writes = {}

    def fake_api(method, path, body=None):
        if path.endswith("/context") and body is not None:
            writes[body["key"]] = body["value"]
        return {}

    pilot._api = fake_api
    pilot._check_trade = lambda key, trade: (
        [] if isinstance(trade, dict) else ["a trade is one JSON object"])
    now = datetime.datetime(2026, 8, 13, 13, 0,
                            tzinfo=datetime.timezone.utc)
    context = {"trades/live": {"contracts": ["NVDA 20260821 235C"],
                               "state": "idea"},
               "trades/retired": None}
    pilot.audit(context, now)
    assert writes["desk/audit"]["clean"] is True
    assert writes["desk/audit"]["cases_checked"] == 1


def test_retired_evidence_is_a_named_violation():
    cases = {"trades/nvda": {"contracts": ["NVDA 20260821 235C"],
                             "state": "watching",
                             "evidence": ["widgets/gamma", "findings/f1"]},
             "trades/done": {"state": "closed",
                             "evidence": ["widgets/long-gone"]}}
    context = {**cases, "widgets/gamma": {"kind": "metric"}}
    found = autopilot.evidence_violations(cases, context)
    assert list(found) == ["trades/nvda"]
    assert "findings/f1" in found["trades/nvda"][0]
    # closed trades are history; their evidence may retire freely


def test_position_contract_streams_are_healed_too(tmp_path):
    import datetime
    pilot = autopilot.Pilot("http://x", "t", "env", 36,
                            str(tmp_path / "state.json"))
    calls = []
    now = datetime.datetime(2026, 8, 13, 14, 0,
                            tzinfo=datetime.timezone.utc)

    def fake_api(method, path, body=None):
        args = (body or {}).get("args") or {}
        if path.endswith("/tools/live_quote"):
            return {"result": {"ok": True, "data": {"quote": {
                "bid": 0.94, "last": 0.95,
                "observed_at": "2026-08-12T19:59:00+00:00"}}}}  # stale
        if path.endswith("/tools/market_stream"):
            calls.append((args.get("action"), args.get("symbol"),
                          args.get("strike")))
            return {"result": {"ok": True, "data": {}}}
        if path.endswith("/context"):
            return {}
        return {}

    pilot._api = fake_api
    context = {"trades/nvda": {
        "state": "open-simulated",
        "contracts": ["NVDA 20260821 235C"],
        "fill": {"contract": "NVDA 20260821 235C", "price": 0.96,
                 "quantity": 1,
                 "observed_at": "2026-08-12T15:24:00-04:00"}}}
    report = pilot.stream_health(context, now)
    assert "NVDA 20260821 235C" in report["stale"]
    # healed with FULL option args, stop then start
    assert calls[0][0] == "stop" and calls[0][2] == 235.0
    assert calls[1][0] == "start" and calls[1][1] == "NVDA"


def test_idea_contracts_hold_streams_and_get_health_checked():
    context = {"trades/amd": {"state": "idea",
                              "contracts": ["AMD 20260821 530C"]},
               "trades/old": {"state": "closed",
                              "contracts": ["TSLA 20260821 500C"]}}
    holders = autopilot.stream_holders(context)
    keys = [(h.get("symbol"), h.get("strike")) for h in holders]
    assert ("AMD", 530.0) in keys, "an idea holds its contract's stream"
    assert not any(s == "TSLA" for s, _ in keys), "closed trades release"


def test_cockpit_candidate_contracts_hold_streams():
    context = {"cockpit/nvda/candidate-240c": {
        "kind": "metric",
        "refresh": {"tool": "live_quote",
                    "args": {"symbol": "NVDA", "sec_type": "OPT",
                             "expiration": "20260821", "strike": 240,
                             "right": "C"},
                    "minutes": 2}}}
    holders = autopilot.stream_holders(context)
    assert any(h.get("strike") == 240 for h in holders), \
        "a watched candidate holds its stream"


def test_a_requested_plan_forces_a_prompt_turn():
    context = {**ACTIVE_DESK,
               "plans/spy": {"plan": "trail it", "status": "requested"}}
    ready = state(last_turn="2026-08-12T17:00:00+00:00")
    action, reason = autopilot.decide(context, ready, RTH)
    assert action == "build" and "awaiting compilation" in reason


def test_plan_health_is_audited():
    import datetime
    now = datetime.datetime(2026, 8, 13, 16, 0,
                            tzinfo=datetime.timezone.utc)
    context = {
        "trades/naked": {"state": "open-simulated",
                         "contracts": ["SPY 20260814 785C"]},
        "trades/managed": {"state": "open-simulated",
                           "contracts": ["NVDA 20260821 235C"]},
        "plans/managed": {"status": "active", "active_at": "x",
                          "program": {}, "last_error": "manage() raised"},
        "plans/slow": {"status": "requested",
                       "requested_at": "2026-08-13T15:30:00+00:00"},
    }
    found = autopilot.plan_health_violations(context, now)
    assert "NO management plan" in found["trades/naked"][0]
    assert "plan error" in found["plans/managed"][0]
    assert "still not compiled" in found["plans/slow"][0]
