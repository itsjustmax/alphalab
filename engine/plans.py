"""Position plans: the member's words, compiled to inspectable programs.

The member says how a position should be managed — a 100% target, a
ratcheting trail, an underlying trigger, a dated close, a cross-contract
condition, anything. An agent compiles that into a plan at
``plans/<trade-id>``:

    {"plan": "<the member's words>",
     "status": "draft",              # the MEMBER activates, never agents
     "program": {
        "explanation": "how this will manage the position, in prose",
        "inputs": [{"name": "mark", "tool": "live_quote",
                    "args": {...}}, ...],       # read-only tools only
        "code": "def manage(inputs, state): ...",
        "tests": [{"name": ..., "inputs": {...}, "state": {...},
                   "expect_actions": [...] , "expect_no_actions": true?}]
     }}

The decision function is pure: it sees the gathered inputs plus
``position`` and ``now``, keeps its own ``state`` (watermarks, ratchet
levels), and may only answer bounded actions — close (a marketable exit
through the same market gate as everything else), place_exit (a resting
limit), cancel_exit, note. It cannot reach the network, the filesystem,
or the broker: the desk gathers, the program decides, the gate executes.

Inspection is the contract: plan_check runs the program's own tests and
answers pass/fail per test — the member reads the explanation, the code,
and the test results before activating.
"""

import json

READ_ONLY_TOOLS = {
    "live_quote", "live_quotes", "price_summary", "daily_bars",
    "quote_snapshot", "spx_gamma", "market_context", "options_chain",
}

ALLOWED_ACTIONS = {"close", "place_exit", "cancel_exit", "note"}

SAFE_BUILTINS = {
    "abs": abs, "min": min, "max": max, "round": round, "len": len,
    "float": float, "int": int, "str": str, "bool": bool, "sorted": sorted,
    "sum": sum, "any": any, "all": all, "enumerate": enumerate,
    "range": range, "isinstance": isinstance, "dict": dict, "list": list,
    "ValueError": ValueError, "True": True, "False": False, "None": None,
}

MAX_STATE_BYTES = 8_000


def program_violations(program):
    """Every way a compiled program breaks the plan contract, by name."""

    if not isinstance(program, dict):
        return ["a program is one JSON object"]
    violations = []
    code = program.get("code")
    if not isinstance(code, str) or "def manage(" not in code:
        violations.append(
            "the program's code defines exactly `def manage(inputs, state):`")
    if not str(program.get("explanation") or "").strip():
        violations.append(
            "the program explains itself in prose — the member reads this "
            "before activating")
    inputs = program.get("inputs")
    if not isinstance(inputs, list):
        violations.append("inputs is a list of declared tool reads")
    else:
        for declaration in inputs:
            if not isinstance(declaration, dict) or not declaration.get("name"):
                violations.append("every input declares a name")
                continue
            tool = str(declaration.get("tool") or "")
            if tool not in READ_ONLY_TOOLS:
                violations.append(
                    f"input {declaration['name']!r} uses {tool!r} — inputs "
                    f"are read-only tools: {', '.join(sorted(READ_ONLY_TOOLS))}")
    tests = program.get("tests")
    if not isinstance(tests, list) or not tests:
        violations.append(
            "the program carries at least one test — validation is the "
            "member's window into whether this works")
    return violations


def run_decision(code, inputs, state):
    """Run manage(inputs, state) in a restricted namespace.

    (result, error): result is {"actions": [...], "state": {...}} with the
    action vocabulary enforced; error is a named string, never a traceback
    dump. The namespace has no imports, no I/O — the program decides, the
    desk does.
    """

    namespace = {"__builtins__": dict(SAFE_BUILTINS)}
    try:
        exec(code, namespace)  # noqa: S102 — restricted, member-activated
    except Exception as error:
        return None, f"the program does not load: {str(error)[:200]}"
    manage = namespace.get("manage")
    if not callable(manage):
        return None, "the program defines no manage(inputs, state)"
    try:
        result = manage(dict(inputs or {}), dict(state or {}))
    except Exception as error:
        return None, f"manage() raised: {str(error)[:200]}"
    if not isinstance(result, dict):
        return None, "manage() answers a dict with actions and state"
    actions = result.get("actions") or []
    if not isinstance(actions, list) or len(actions) > 5:
        return None, "actions is a list of at most five"
    for action in actions:
        if not isinstance(action, dict) \
                or action.get("action") not in ALLOWED_ACTIONS:
            return None, (f"unknown action {action!r} — the vocabulary is "
                          + ", ".join(sorted(ALLOWED_ACTIONS)))
        if action["action"] == "place_exit":
            try:
                price = float(action.get("price"))
            except (TypeError, ValueError):
                return None, "place_exit carries a numeric price"
            if not price >= 0:
                return None, "place_exit price is non-negative"
        if action["action"] == "note" and not str(action.get("text") or "").strip():
            return None, "a note carries text"
    new_state = result.get("state") or {}
    try:
        encoded = json.dumps(new_state)
    except (TypeError, ValueError):
        return None, "state must be plain JSON"
    if len(encoded.encode("utf-8")) > MAX_STATE_BYTES:
        return None, f"state stays under {MAX_STATE_BYTES} bytes"
    return {"actions": actions, "state": new_state}, None


def run_tests(program):
    """Execute the program's own tests; one named verdict per test."""

    results = []
    for index, test in enumerate(program.get("tests") or []):
        name = str(test.get("name") or f"test {index + 1}")
        result, error = run_decision(
            program.get("code") or "", test.get("inputs") or {},
            test.get("state") or {})
        if error:
            results.append({"name": name, "passed": False, "detail": error})
            continue
        actions = result["actions"]
        kinds = [action["action"] for action in actions]
        passed = True
        detail = f"actions: {kinds or 'none'}"
        expected = test.get("expect_actions")
        if expected is not None:
            missing = [kind for kind in expected if kind not in kinds]
            extra_ok = True
            if missing:
                passed = False
                detail = f"expected {expected}, got {kinds}"
        if test.get("expect_no_actions") and actions:
            passed = False
            detail = f"expected no actions, got {kinds}"
        expected_state = test.get("expect_state_contains")
        if passed and isinstance(expected_state, dict):
            for key, value in expected_state.items():
                if result["state"].get(key) != value:
                    passed = False
                    detail = (f"state[{key!r}] is "
                              f"{result['state'].get(key)!r}, expected {value!r}")
                    break
        results.append({"name": name, "passed": passed, "detail": detail})
    return results


def plan_check(arguments):
    """Tool face: validate a plan's program and run its tests."""

    import gates

    plan = arguments.get("plan") or {}
    program = (plan.get("program") if isinstance(plan, dict) else None) \
        or arguments.get("program") or {}
    violations = program_violations(program)
    if violations:
        return gates.receipt(
            f"the plan's program breaks {len(violations)} rule(s)",
            {"violations": violations, "tests": []},
            gaps=violations, ok=False)
    results = run_tests(program)
    failed = [r for r in results if not r["passed"]]
    return gates.receipt(
        (f"all {len(results)} plan test(s) pass" if not failed else
         f"{len(failed)} of {len(results)} plan test(s) FAIL"),
        {"violations": [], "tests": results, "passed": not failed},
        gaps=[f"{r['name']}: {r['detail']}" for r in failed],
        ok=not failed)
