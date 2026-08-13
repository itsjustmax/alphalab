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
levels), and — this is the bot contract — while the position is OPEN it
MUST answer a ``market``: {"stop": price, "target": price, "buy"?} —
where the bot is a seller on both sides, even far from price. Those
levels ARE the working orders (the bracket), modulated as fast as the
bot likes; deterministic executors act on them tick by tick. It may
also answer bounded actions — close (a marketable exit
through the same market gate as everything else), place_exit (a resting
limit), cancel_exit, arm_entry (a standing entry order for a trade not
yet filled — plans catch entries, not only manage exits), note. It
cannot reach the network, the filesystem,
or the broker: the desk gathers, the program decides, the gate executes.

Inspection is the contract: plan_check runs the program's own tests and
answers pass/fail per test — the member reads the explanation, the code,
and the test results before activating.
"""

import json

READ_ONLY_TOOLS = {
    "live_quote", "live_quotes", "price_summary", "daily_bars",
    "quote_snapshot", "spx_gamma", "market_context", "options_chain",
    "contract_bars", "symbol_zones", "rss_fetch", "implied_move",
    "short_volume", "tick_tape",
}

ALLOWED_ACTIONS = {"close", "place_exit", "cancel_exit", "arm_entry",
                   "retire", "note"}

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
        if action["action"] == "arm_entry" \
                and action.get("contract") is not None \
                and not (isinstance(action["contract"], str)
                         and 0 < len(action["contract"]) <= 120):
            return None, "arm_entry.contract is a candidate contract string"
        if action["action"] in ("place_exit", "arm_entry"):
            try:
                price = float(action.get("price"))
            except (TypeError, ValueError):
                return None, f"{action['action']} carries a numeric price"
            if not price >= 0:
                return None, f"{action['action']} price is non-negative"
        if action["action"] == "note" and not str(action.get("text") or "").strip():
            return None, "a note carries text"
    market = result.get("market")
    if market is not None:
        if not isinstance(market, dict):
            return None, "market is an object: {stop, target, buy?}"
        for field in ("stop", "target", "buy"):
            raw = market.get(field)
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return None, f"market.{field} must be numeric, got {raw!r}"
            if value < 0:
                return None, f"market.{field} is non-negative"
        stop_v, target_v = market.get("stop"), market.get("target")
        if stop_v is not None and target_v is not None \
                and float(stop_v) >= float(target_v):
            return None, (f"market.stop ({stop_v}) must sit below "
                          f"market.target ({target_v})")
    new_state = result.get("state") or {}
    try:
        encoded = json.dumps(new_state)
    except (TypeError, ValueError):
        return None, "state must be plain JSON"
    if len(encoded.encode("utf-8")) > MAX_STATE_BYTES:
        return None, f"state stays under {MAX_STATE_BYTES} bytes"
    answer = {"actions": actions, "state": new_state}
    if market is not None:
        answer["market"] = {k: float(market[k]) for k in
                            ("stop", "target", "buy")
                            if market.get(k) is not None}
    return answer, None


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


def library_dir():
    import os

    return os.environ.get(
        "ALPHALAB_PLAN_LIBRARY",
        os.path.expanduser("~/.alphalab/plans"))


def archive(trade_id, plan, outcome=None, directory=None):
    """Save a plan to the machine's library — the desk's institutional memory.

    Written at activation (the member endorsed this program) and again at
    close (the outcome makes it a judged example). One file per trade id,
    last write wins; future agents browse these through plan_library when
    they want proven management patterns, not blank-page guesses.
    """

    import datetime
    import os

    directory = directory or library_dir()
    os.makedirs(directory, exist_ok=True)
    record = {
        "trade_id": str(trade_id),
        "plan": (plan or {}).get("plan"),
        "status": (plan or {}).get("status"),
        "program": (plan or {}).get("program"),
        "state": (plan or {}).get("state"),
        "saved_at": datetime.datetime.now(datetime.timezone.utc)
                    .isoformat(timespec="seconds"),
    }
    if outcome:
        record["outcome"] = outcome
    path = os.path.join(directory, f"{str(trade_id)}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=1)
    return path


def close_outcome(trade):
    """The judged result of a managed position, from its own receipts."""

    fill = (trade or {}).get("fill") or {}
    exit_fill = (trade or {}).get("exit") or {}
    entry, exit_price = fill.get("price"), exit_fill.get("price")
    if not isinstance(entry, (int, float)) \
            or not isinstance(exit_price, (int, float)):
        return None
    quantity = fill.get("quantity") or 1
    return {
        "entry": entry, "exit": exit_price, "quantity": quantity,
        "pnl_per_contract": round(exit_price - entry, 4),
        "pnl_pct": round(100 * (exit_price - entry) / entry, 2)
        if entry else None,
        "entered_at": fill.get("observed_at"),
        "closed_at": exit_fill.get("observed_at"),
    }


def plan_library(arguments):
    """Tool face: browse the saved plans — proven management examples."""

    import glob
    import os

    import gates

    directory = str(arguments.get("directory") or library_dir())
    paths = sorted(glob.glob(os.path.join(directory, "*.json")),
                   key=os.path.getmtime, reverse=True)
    limit = min(int(arguments.get("limit") or 12), 30)
    records, gaps = [], []
    for path in paths[:limit]:
        try:
            with open(path, encoding="utf-8") as handle:
                record = json.load(handle)
        except (json.JSONDecodeError, OSError):
            gaps.append(f"{os.path.basename(path)} is not readable JSON")
            continue
        code = ((record.get("program") or {}).get("code") or "")
        if len(code) > 4000:
            record.setdefault("program", {})["code"] = code[:4000] + "\n# …"
        records.append(record)
    if not records:
        return gates.receipt(
            "the plan library is empty — no plan has been activated or "
            "closed on this machine yet", {"plans": []}, gaps=gaps)
    judged = [r for r in records if r.get("outcome")]
    return gates.receipt(
        f"{len(records)} saved plan(s), {len(judged)} with a judged "
        f"outcome — newest first",
        {"plans": records}, gaps=gaps)


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


# ---- Computed overlays: agent code that thinks about a trade --------
# An overlays/<trade-id>-<slug> entry may carry a program:
#   {"program": {"inputs": [{name, tool, args}],   # read-only tools
#                "code": "def compute(inputs): ... return {...}"},
#    "minutes": 10}
# The autopilot gathers the inputs and runs compute() in the same
# restricted namespace as plan decisions; the answer must be overlay
# DATA (levels/bands/clocks/note), which lands back on the entry where
# the cockpit's custom plugin renders it. Creative freedom in the
# computation; a bounded, validated shape at the boundary.

MAX_OVERLAY_ITEMS = 24


def overlay_violations(value):
    """Every way computed overlay output breaks the shape, by name."""

    if not isinstance(value, dict):
        return ["compute() answers one JSON object"]
    violations = []
    for field, required in (("levels", ("price",)), ("bands",
                            ("lower", "upper")), ("clocks", ("at",))):
        items = value.get(field)
        if items is None:
            continue
        if not isinstance(items, list) or len(items) > MAX_OVERLAY_ITEMS:
            violations.append(
                f"{field} is a list of at most {MAX_OVERLAY_ITEMS}")
            continue
        for item in items:
            if not isinstance(item, dict):
                violations.append(f"every {field} item is an object")
                break
            for need in required:
                raw = item.get(need)
                if need == "at":
                    if not str(raw or "").strip():
                        violations.append(f"a clock needs an `at` time")
                        break
                else:
                    try:
                        float(raw)
                    except (TypeError, ValueError):
                        violations.append(
                            f"{field}.{need} must be numeric, got {raw!r}")
                        break
    extras = set(value) - {"levels", "bands", "clocks", "note", "target"}
    if extras:
        violations.append(
            f"unknown field(s) {sorted(extras)} — computed overlays "
            f"answer levels/bands/clocks/note/target only")
    return violations


def run_compute(code, inputs):
    """Run compute(inputs) in the restricted namespace; validate shape."""

    namespace = {"__builtins__": dict(SAFE_BUILTINS)}
    try:
        exec(code, namespace)  # noqa: S102 — restricted, data-only output
    except Exception as error:
        return None, f"the program does not load: {str(error)[:200]}"
    compute = namespace.get("compute")
    if not callable(compute):
        return None, "the program defines no compute(inputs)"
    try:
        result = compute(dict(inputs or {}))
    except Exception as error:
        return None, f"compute() raised: {str(error)[:200]}"
    violations = overlay_violations(result)
    if violations:
        return None, "; ".join(violations)
    return result, None
