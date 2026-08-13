"""Position plans: the decision runner, the vocabulary, the autopilot pass.

The load-bearing pins: every management style the member asked for —
profit targets, ratcheting trails, underlying triggers, session-move
closes, dated closes, cross-contract conditions — is expressible as a
pure manage(inputs, state) program; the action vocabulary is enforced;
draft plans never run; the member's activation is the only ignition.
"""

import datetime
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "tools"))

import autopilot  # noqa: E402
import plans  # noqa: E402

# Every test writes plan archives to a sandbox, never the real library.
import tempfile  # noqa: E402
_MODULE_LIBRARY = tempfile.mkdtemp()
os.environ["ALPHALAB_PLAN_LIBRARY"] = _MODULE_LIBRARY


def _program(code, tests=None, inputs=None, explanation="managed"):
    return {"code": code, "explanation": explanation,
            "inputs": inputs if inputs is not None else
            [{"name": "mark", "tool": "live_quote",
              "args": {"symbol": "NVDA"}}],
            "tests": tests or [{"name": "loads", "inputs": {}, "state": {}}]}


class ProgramContract(unittest.TestCase):
    def test_read_only_tools_only(self):
        violations = plans.program_violations(_program(
            "def manage(inputs, state):\n return {'actions': [], 'state': {}}",
            inputs=[{"name": "x", "tool": "fill_watch", "args": {}}]))
        self.assertTrue(any("read-only" in v for v in violations))

    def test_tests_are_mandatory(self):
        program = _program("def manage(inputs, state):\n"
                           " return {'actions': [], 'state': {}}")
        program["tests"] = []
        violations = plans.program_violations(program)
        self.assertTrue(any("test" in v for v in violations))

    def test_clean_program_passes(self):
        self.assertEqual(plans.program_violations(_program(
            "def manage(inputs, state):\n"
            " return {'actions': [], 'state': {}}")), [])


class DecisionRunner(unittest.TestCase):
    def test_action_vocabulary_enforced(self):
        _, error = plans.run_decision(
            "def manage(inputs, state):\n"
            " return {'actions': [{'action': 'wire_funds'}], 'state': {}}",
            {}, {})
        self.assertIn("vocabulary", error)

    def test_imports_are_absent(self):
        _, error = plans.run_decision(
            "import os\ndef manage(inputs, state):\n"
            " return {'actions': [], 'state': {}}", {}, {})
        self.assertIn("does not load", error)

    def test_open_is_absent(self):
        _, error = plans.run_decision(
            "def manage(inputs, state):\n"
            " open('/etc/hosts')\n"
            " return {'actions': [], 'state': {}}", {}, {})
        self.assertIn("raised", error)

    def test_place_exit_needs_a_price(self):
        _, error = plans.run_decision(
            "def manage(inputs, state):\n"
            " return {'actions': [{'action': 'place_exit'}], 'state': {}}",
            {}, {})
        self.assertIn("numeric price", error)

    def test_state_round_trips(self):
        result, error = plans.run_decision(
            "def manage(inputs, state):\n"
            " state['high'] = max(state.get('high', 0), inputs['mark'])\n"
            " return {'actions': [], 'state': state}",
            {"mark": 1.4}, {"high": 1.2})
        self.assertIsNone(error)
        self.assertEqual(result["state"]["high"], 1.4)


PROFIT_TARGET = """
def manage(inputs, state):
    entry = inputs['position']['entry']
    mark = inputs['mark']['quote']['bid']
    if mark >= entry * 2:
        return {'actions': [{'action': 'close'}], 'state': state}
    return {'actions': [], 'state': state}
"""

RATCHET_TRAIL = """
def manage(inputs, state):
    mark = inputs['mark']['quote']['bid']
    high = max(state.get('high', mark), mark)
    state['high'] = high
    stop = state.get('stop')
    # ratchet: every 1.0 the high climbs, the stop climbs 1.0 with it
    wanted = state.get('base_stop', 0) + int(high - state.get('base_high', high))
    if stop is None or wanted > stop:
        state['stop'] = wanted
        stop = wanted
    if mark <= stop:
        return {'actions': [{'action': 'close'}], 'state': state}
    return {'actions': [], 'state': state}
"""

UNDERLYING_TRIGGER = """
def manage(inputs, state):
    last = inputs['underlying']['quote']['last']
    if last >= 240:
        return {'actions': [{'action': 'close'},
                            {'action': 'note', 'text': 'NVDA touched 240'}],
                'state': state}
    return {'actions': [], 'state': state}
"""

SESSION_MOVE = """
def manage(inputs, state):
    quote = inputs['underlying']['quote']
    close = quote['close']
    last = quote['last']
    if close and last and (last - close) / close >= 0.10:
        return {'actions': [{'action': 'close'}], 'state': state}
    return {'actions': [], 'state': state}
"""

DATED_CLOSE = """
def manage(inputs, state):
    if inputs['now'] >= '2026-08-21T14:00:00':
        return {'actions': [{'action': 'close'}], 'state': state}
    return {'actions': [], 'state': state}
"""

CROSS_CONTRACT = """
def manage(inputs, state):
    other = inputs['other']['quote']['bid']
    mark = inputs['mark']['quote']['bid']
    if other >= 5.0 and not state.get('armed'):
        state['armed'] = True
        state['stop'] = mark - 1.0
    if state.get('armed') and mark <= state['stop']:
        return {'actions': [{'action': 'close'}], 'state': state}
    return {'actions': [], 'state': state}
"""


class TheMembersManagementStyles(unittest.TestCase):
    """Each management style the member named, runnable as a plan program."""

    def _act(self, code, inputs, state=None):
        result, error = plans.run_decision(code, inputs, state or {})
        self.assertIsNone(error)
        return result

    def test_hundred_percent_target(self):
        position = {"position": {"entry": 0.96}}
        holds = self._act(PROFIT_TARGET,
                          {**position, "mark": {"quote": {"bid": 1.5}}})
        self.assertEqual(holds["actions"], [])
        closes = self._act(PROFIT_TARGET,
                           {**position, "mark": {"quote": {"bid": 1.92}}})
        self.assertEqual(closes["actions"][0]["action"], "close")

    def test_ratcheting_trail_at_one_point_increments(self):
        state = {"base_stop": 2.0, "base_high": 3.0, "high": 3.0, "stop": 2.0}
        climbed = self._act(RATCHET_TRAIL, {"mark": {"quote": {"bid": 4.2}}},
                            state)
        self.assertEqual(climbed["state"]["stop"], 3)   # ratcheted up 1.0
        self.assertEqual(climbed["actions"], [])
        never_down = self._act(RATCHET_TRAIL,
                               {"mark": {"quote": {"bid": 3.4}}},
                               climbed["state"])
        self.assertEqual(never_down["state"]["stop"], 3)
        stopped = self._act(RATCHET_TRAIL, {"mark": {"quote": {"bid": 2.9}}},
                            never_down["state"])
        self.assertEqual(stopped["actions"][0]["action"], "close")

    def test_underlying_price_trigger(self):
        fired = self._act(UNDERLYING_TRIGGER,
                          {"underlying": {"quote": {"last": 241.2}}})
        self.assertEqual([a["action"] for a in fired["actions"]],
                         ["close", "note"])

    def test_ten_percent_session_move(self):
        quiet = self._act(SESSION_MOVE, {"underlying": {"quote":
                          {"close": 220.0, "last": 230.0}}})
        self.assertEqual(quiet["actions"], [])
        fired = self._act(SESSION_MOVE, {"underlying": {"quote":
                          {"close": 220.0, "last": 242.5}}})
        self.assertEqual(fired["actions"][0]["action"], "close")

    def test_close_at_date_and_time(self):
        early = self._act(DATED_CLOSE, {"now": "2026-08-21T13:59:00"})
        self.assertEqual(early["actions"], [])
        due = self._act(DATED_CLOSE, {"now": "2026-08-21T14:00:05"})
        self.assertEqual(due["actions"][0]["action"], "close")

    def test_cross_contract_conditional_stop(self):
        armed = self._act(CROSS_CONTRACT,
                          {"other": {"quote": {"bid": 5.1}},
                           "mark": {"quote": {"bid": 3.0}}})
        self.assertEqual(armed["state"], {"armed": True, "stop": 2.0})
        self.assertEqual(armed["actions"], [])
        stopped = self._act(CROSS_CONTRACT,
                            {"other": {"quote": {"bid": 4.0}},
                             "mark": {"quote": {"bid": 1.9}}},
                            armed["state"])
        self.assertEqual(stopped["actions"][0]["action"], "close")


class PlanTests(unittest.TestCase):
    def test_run_tests_names_verdicts(self):
        program = _program(PROFIT_TARGET, tests=[
            {"name": "holds below target",
             "inputs": {"position": {"entry": 0.96},
                        "mark": {"quote": {"bid": 1.5}}},
             "expect_no_actions": True},
            {"name": "closes at 100%",
             "inputs": {"position": {"entry": 0.96},
                        "mark": {"quote": {"bid": 1.92}}},
             "expect_actions": ["close"]},
        ])
        results = plans.run_tests(program)
        self.assertTrue(all(r["passed"] for r in results), results)

    def test_plan_check_reports_failures(self):
        program = _program(PROFIT_TARGET, tests=[
            {"name": "wrong expectation",
             "inputs": {"position": {"entry": 0.96},
                        "mark": {"quote": {"bid": 1.5}}},
             "expect_actions": ["close"]}])
        answer = plans.plan_check({"program": program})
        self.assertFalse(answer["ok"])
        self.assertIn("FAIL", answer["summary"])

    def test_plan_check_all_green(self):
        program = _program(
            "def manage(inputs, state):\n"
            " return {'actions': [], 'state': {}}",
            tests=[{"name": "quiet", "inputs": {}, "expect_no_actions": True}])
        answer = plans.plan_check({"plan": {"program": program}})
        self.assertTrue(answer["ok"])
        self.assertTrue(answer["data"]["passed"])


def _pilot(context, tool_answers=None):
    """An Autopilot with the API faked to a context dict + canned tools."""

    pilot = autopilot.Pilot.__new__(autopilot.Pilot)
    pilot.environment = "env"
    pilot.state = {}
    pilot.calls = []
    pilot.said = []

    def api(method, path, body=None):
        pilot.calls.append((method, path, body))
        if path.endswith("/say"):
            pilot.said.append(body["text"])
            return {}
        if "/tools/" in path:
            tool = path.rsplit("/", 1)[1]
            answer = (tool_answers or {}).get(tool)
            if answer is None:
                raise RuntimeError("no lane")
            return {"result": {"ok": True, "data": answer}}
        if path.endswith("/context") and body is not None:
            context[body["key"]] = body["value"]
            return {}
        return {"context": context}

    pilot._api = api
    pilot._save = lambda: None
    return pilot


NOW = datetime.datetime(2026, 8, 12, 15, 0,
                        tzinfo=datetime.timezone.utc)


def _live_context(status="active", code=PROFIT_TARGET):
    return {
        "trades/nvda": {"contracts": ["NVDA 20260821 235C"],
                        "thesis": "gamma squeeze", "invalidation": "fade",
                        "state": "open-simulated",
                        "fill": {"contract": "NVDA 20260821 235C",
                                 "price": 0.96, "bid": 0.94, "ask": 0.96,
                                 "quantity": 1,
                                 "observed_at": "2026-08-11T15:00:00-04:00"}},
        "plans/nvda": {"plan": "close at 100%", "status": status,
                       "program": _program(code)},
    }


class AutopilotPlans(unittest.TestCase):
    def test_active_plan_closes_through_the_gate(self):
        context = _live_context()
        pilot = _pilot(context, {"live_quote":
                                 {"quote": {"bid": 1.92, "ask": 1.94}}})
        ran = pilot.manage_plans(context, NOW)
        self.assertEqual(ran, 1)
        card = context.get("widgets/fill-nvda-exit")
        self.assertIsNotNone(card, "close places a marketable exit card")
        args = card["refresh"]["args"]
        self.assertEqual(args["action"], "sell")
        self.assertEqual(args["price"], 0.01)  # executes AT the bid
        self.assertEqual(args["expiration"], "20260821")
        self.assertEqual(args["strike"], 235.0)
        self.assertTrue(any("closing at the market" in text
                            for text in pilot.said))

    def test_draft_plans_never_run(self):
        context = _live_context(status="draft")
        pilot = _pilot(context, {"live_quote":
                                 {"quote": {"bid": 9.99, "ask": 10.0}}})
        self.assertEqual(pilot.manage_plans(context, NOW), 0)
        self.assertNotIn("widgets/fill-nvda-exit", context)

    def test_no_inputs_means_no_decision(self):
        context = _live_context()
        pilot = _pilot(context, {})  # every tool raises
        self.assertEqual(pilot.manage_plans(context, NOW), 0)
        self.assertNotIn("widgets/fill-nvda-exit", context)

    def test_program_error_lands_on_the_plan(self):
        context = _live_context(code="def manage(inputs, state):\n"
                                     " return inputs['absent']")
        pilot = _pilot(context, {"live_quote":
                                 {"quote": {"bid": 1.0, "ask": 1.1}}})
        pilot.manage_plans(context, NOW)
        self.assertIn("raised", context["plans/nvda"]["last_error"])

    def test_existing_identical_exit_is_left_alone(self):
        context = _live_context()
        context["widgets/fill-nvda-exit"] = {
            "kind": "order", "refresh": {"args": {"price": 0.01,
                                                  "action": "sell"}}}
        pilot = _pilot(context, {"live_quote":
                                 {"quote": {"bid": 1.92, "ask": 1.94}}})
        pilot.manage_plans(context, NOW)
        self.assertEqual(pilot.said, [])  # nothing re-announced

    def test_notes_do_not_repeat(self):
        code = ("def manage(inputs, state):\n"
                " return {'actions': [{'action': 'note',"
                " 'text': 'watching'}], 'state': state}")
        context = _live_context(code=code)
        pilot = _pilot(context, {"live_quote":
                                 {"quote": {"bid": 1.0, "ask": 1.1}}})
        pilot.manage_plans(context, NOW)
        pilot.manage_plans(context, NOW)
        self.assertEqual(pilot.said.count("[plan nvda] watching"), 1)


class TheLibrary(unittest.TestCase):
    """Plans persist as examples: activation saves, the close judges."""

    def setUp(self):
        import tempfile
        self.directory = tempfile.mkdtemp()

    def _plan(self):
        return {"plan": "close at 100%", "status": "active",
                "program": _program(PROFIT_TARGET)}

    def test_archive_and_browse_round_trip(self):
        plans.archive("nvda", self._plan(), directory=self.directory)
        answer = plans.plan_library({"directory": self.directory})
        self.assertTrue(answer["ok"])
        record = answer["data"]["plans"][0]
        self.assertEqual(record["trade_id"], "nvda")
        self.assertIn("def manage", record["program"]["code"])
        self.assertNotIn("outcome", record)

    def test_close_outcome_judges_the_plan(self):
        trade = {"fill": {"price": 0.96, "quantity": 2,
                          "observed_at": "2026-08-11T15:00:00-04:00"},
                 "exit": {"price": 1.92,
                          "observed_at": "2026-08-14T10:00:00-04:00"}}
        outcome = plans.close_outcome(trade)
        self.assertEqual(outcome["pnl_per_contract"], 0.96)
        self.assertEqual(outcome["pnl_pct"], 100.0)
        plans.archive("nvda", self._plan(), outcome=outcome,
                      directory=self.directory)
        answer = plans.plan_library({"directory": self.directory})
        self.assertIn("1 with a judged outcome", answer["summary"])

    def test_empty_library_is_named(self):
        answer = plans.plan_library({"directory": self.directory})
        self.assertIn("empty", answer["summary"])


class AutopilotArchives(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.directory = tempfile.mkdtemp()
        os.environ["ALPHALAB_PLAN_LIBRARY"] = self.directory

    def tearDown(self):
        os.environ["ALPHALAB_PLAN_LIBRARY"] = _MODULE_LIBRARY

    def test_first_active_pass_archives_the_endorsement(self):
        context = _live_context()
        pilot = _pilot(context, {"live_quote":
                                 {"quote": {"bid": 1.5, "ask": 1.6}}})
        pilot.manage_plans(context, NOW)
        self.assertTrue(context["plans/nvda"].get("archived_at"))
        answer = plans.plan_library({"directory": self.directory})
        self.assertEqual(answer["data"]["plans"][0]["trade_id"], "nvda")
        pilot.manage_plans(context, NOW)  # once, not every pass
        self.assertEqual(len(answer["data"]["plans"]), 1)

    def test_recorded_exit_judges_and_retires_the_plan(self):
        context = _live_context()
        context["plans/nvda"]["archived_at"] = "2026-08-12T15:00:00+00:00"
        context["widgets/fill-nvda-exit"] = {
            "kind": "order",
            "refresh": {"args": {"symbol": "NVDA", "sec_type": "OPT",
                                 "expiration": "20260821", "strike": 235,
                                 "right": "C", "price": 1.92, "quantity": 1,
                                 "action": "sell",
                                 "contract": "NVDA 20260821 235C"}},
            "check": {"verdict": "fill-supported", "action": "sell",
                      "contract": "NVDA 20260821 235C",
                      "fill": {"contract": "NVDA 20260821 235C",
                               "price": 1.92, "bid": 1.92, "ask": 1.95,
                               "quantity": 1,
                               "observed_at": NOW.isoformat(
                                   timespec="seconds")}}}
        pilot = _pilot(context, {})
        pilot.record_orders(context, NOW)
        self.assertEqual(context["trades/nvda"]["state"], "closed")
        self.assertEqual(context["plans/nvda"]["status"], "completed")
        answer = plans.plan_library({"directory": self.directory})
        record = answer["data"]["plans"][0]
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["outcome"]["pnl_pct"], 100.0)


class DisplayNotationFills(unittest.TestCase):
    def test_close_streams_the_canonical_contract(self):
        # A fill recorded in display notation must not quote the stock.
        context = _live_context()
        context["trades/nvda"]["fill"]["contract"] = "NVDA 8/21 235C"
        pilot = _pilot(context, {"live_quote":
                                 {"quote": {"bid": 1.92, "ask": 1.94}}})
        pilot.manage_plans(context, NOW)
        args = context["widgets/fill-nvda-exit"]["refresh"]["args"]
        self.assertEqual(args["sec_type"], "OPT")
        self.assertEqual(args["expiration"], "20260821")
        self.assertEqual(args["contract"], "NVDA 20260821 235C")


class ContractParsing(unittest.TestCase):
    def test_option_label(self):
        parsed = autopilot.parse_contract_label("NVDA 20260821 235C")
        self.assertEqual(parsed, {"symbol": "NVDA", "sec_type": "OPT",
                                  "expiration": "20260821",
                                  "strike": 235.0, "right": "C"})

    def test_bare_symbol(self):
        self.assertEqual(autopilot.parse_contract_label("NVDA shares"),
                         {"symbol": "NVDA"})


if __name__ == "__main__":
    unittest.main()


ENTRY_PLAN = """
def manage(inputs, state):
    quote = inputs['mark']['quote']
    if quote['ask'] is not None and quote['ask'] <= 4.0:
        return {'actions': [{'action': 'arm_entry', 'price': 4.0}],
                'state': state}
    return {'actions': [], 'state': state}
"""


def test_plans_can_arm_entries_on_unfilled_trades():
    context = {
        "trades/amd": {"contracts": ["AMD 20260821 530C"],
                       "thesis": "gamma", "invalidation": "fade",
                       "state": "idea"},
        "plans/amd": {"plan": "enter if ask <= 4", "status": "active",
                      "archived_at": "x", "program": _program(ENTRY_PLAN)},
    }
    pilot = _pilot(context, {"live_quote":
                             {"quote": {"bid": 3.9, "ask": 3.95}}})
    ran = pilot.manage_plans(context, NOW)
    assert ran == 1
    card = context.get("widgets/fill-amd")
    assert card, "arm_entry places a standing entry order"
    args = card["refresh"]["args"]
    assert args["action"] == "buy" and args["price"] == 4.0
    assert args["strike"] == 530.0
    assert any("entry armed" in text for text in pilot.said)


def test_arm_entry_never_fires_on_an_open_position():
    context = _live_context()  # open-simulated
    context["plans/nvda"]["program"] = _program(ENTRY_PLAN)
    pilot = _pilot(context, {"live_quote":
                             {"quote": {"bid": 3.9, "ask": 3.9}}})
    pilot.manage_plans(context, NOW)
    assert "widgets/fill-nvda" not in context


GAMMA_LEVELS = """
def compute(inputs):
    gamma = inputs['gamma']['gamma_v2']
    return {'levels': [{'price': gamma['gamma_centroid'],
                        'label': 'gamma centroid'}],
            'target': 'underlying'}
"""


def test_computed_overlays_run_and_land_on_the_entry():
    import datetime
    context = {"overlays/spx-play-gamma": {
        "target": "underlying",
        "program": {"inputs": [{"name": "gamma", "tool": "spx_gamma",
                                "args": {}}],
                    "code": GAMMA_LEVELS},
        "minutes": 10}}
    pilot = _pilot(context, {"spx_gamma":
                             {"gamma_v2": {"gamma_centroid": 7758.2}}})
    ran = pilot.compute_overlays(context, NOW)
    assert ran == 1
    computed = context["overlays/spx-play-gamma"]
    assert computed["levels"] == [{"price": 7758.2,
                                   "label": "gamma centroid"}]
    assert computed["computed_at"]
    # cadence throttle: a second pass inside the window does nothing
    assert pilot.compute_overlays(context, NOW) == 0


def test_overlay_programs_cannot_answer_arbitrary_shapes():
    result, error = plans.run_compute(
        "def compute(inputs):\n return {'orders': [{'buy': 1}]}", {})
    assert result is None and "unknown field" in error


def test_overlay_output_shape_is_validated():
    result, error = plans.run_compute(
        "def compute(inputs):\n return {'levels': [{'price': 'high'}]}",
        {})
    assert result is None and "numeric" in error
