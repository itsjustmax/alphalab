#!/usr/bin/env python3
"""AlphaLab's autopilot: the desk runs unassisted, asks stay human.

A client of a running Manifold service — the platform stays frozen; this
is simply the desk's fourth client (web, agent, CLI, autopilot). It never
writes cards, never answers asks, and never touches a case: it only
decides WHEN the desk agent takes a revision turn, and says so honestly
at the context key ``desk/autopilot``.

A turn fires when:
  - a member's answer landed that no turn has covered yet (backstop —
    the web client already builds on answer), or
  - the agent's own ``desk/next_check`` clock came due, or
  - the desk has been quiet past the phase's maintenance gap
    (30m in the regular session, 60m in extended hours, 4h overnight).

Bounds, always: at least 10 minutes between turns, a hard daily build
budget, and nothing at all until the member has answered intake — the
first move on a fresh desk belongs to the human.

Usage:
  python3 tools/autopilot.py --url http://localhost:PORT --token TOKEN \
      --environment ENV_ID [--budget 36] [--interval 30] [--state PATH]
"""

import argparse
import datetime
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engine"))
import forms  # noqa: E402  (minimal fields in, finished cells out)
import gates  # noqa: E402  (ET, parse_clock — one rulebook for clocks)
import plans  # noqa: E402  (member-activated position management)

MIN_GAP_SECONDS = 10 * 60
DEFAULT_BUDGET = 36
RECORD_MAX_AGE_SECONDS = 180


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def phase(now):
    """Coarse US-market phase, for maintenance cadence and honest logs."""

    local = now.astimezone(gates.ET)
    minutes = local.hour * 60 + local.minute
    if local.weekday() >= 5:
        return "weekend"
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return "regular session"
    if 8 * 60 <= minutes < 9 * 60 + 30 or 16 * 60 <= minutes < 20 * 60:
        return "extended hours"
    return "overnight"


def quiet_gap_minutes(now):
    return {"regular session": 30, "extended hours": 60}.get(phase(now), 240)


def fresh_state(now):
    return {
        "date": now.astimezone(gates.ET).date().isoformat(),
        "builds_today": 0,
        "last_turn": None,
        "seen_answers": [],
    }


def roll_date(state, now):
    """A new ET trading date resets the daily budget."""

    today = now.astimezone(gates.ET).date().isoformat()
    if state.get("date") != today:
        state["date"] = today
        state["builds_today"] = 0
    return state


def supported_orders(context, now):
    """Order cards whose market gate holds right now, ready to record.

    A working paper order is `widgets/fill-<case-id>` (or `…-exit`) with
    kind "order" and a fresh fill-supported check from its own program.
    Recording is pure transcription — the receipt's fill block lands in
    the case verbatim, the state advances, the card retires. Paper fills
    take no human confirmation; asks are for direction, not data.
    """

    recordings = []
    for key, card in sorted((context or {}).items()):
        if not is_order_card(key, card):
            continue
        recording = order_recording(key, context, card.get("check"), now)
        if recording:
            recordings.append(recording)
    return recordings


def parse_contract_label(label):
    """Canonical 'NVDA 20260821 235C' → live-lane contract args."""

    text = str(label or "").strip()
    tokens = text.split()
    parsed = {"symbol": tokens[0].upper() if tokens else ""}
    match = gates._STRIKE_RIGHT.search(text)
    date = next((t for t in tokens if t.isdigit() and len(t) == 8), None)
    if match and date:
        parsed.update({"sec_type": "OPT", "expiration": date,
                       "strike": float(match.group(1)),
                       "right": match.group(2).upper()})
    return parsed


def stream_holders(context):
    """Every stream this desk's context holds, as contract args.

    Armed orders, the watchlist (chips read live), active plans' declared
    inputs, and every open position's own contract (the PnL mark) — a
    plan mid-trail must never lose its eyes. Pure, pinned by tests.
    """

    armed = [((card.get("refresh") or {}).get("args") or {})
             for key, card in (context or {}).items()
             if is_order_card(key, card)]
    watchlist = (context or {}).get("watchlist")
    if isinstance(watchlist, str):
        watchlist = watchlist.replace(",", " ").split()
    for symbol in (watchlist or []):
        armed.append({"symbol": str(symbol).strip().upper()})
    for key, plan in (context or {}).items():
        if not key.startswith("plans/") or not isinstance(plan, dict):
            continue
        if plan.get("status") != "active":
            continue
        for declaration in ((plan.get("program") or {}).get("inputs") or []):
            if isinstance(declaration, dict) \
                    and isinstance(declaration.get("args"), dict) \
                    and declaration["args"].get("symbol"):
                armed.append(declaration["args"])
    # ANY entry whose refresh program reads a live lane holds that
    # stream — cockpit candidate cells, quotes/ chips, order cards.
    for key, value in (context or {}).items():
        if not isinstance(value, dict):
            continue
        refresh = value.get("refresh") or {}
        args = refresh.get("args") if isinstance(refresh, dict) else None
        if isinstance(args, dict) and args.get("symbol") \
                and str(refresh.get("tool")) in (
                    "live_quote", "fill_watch", "market_stream"):
            armed.append(args)
    # EVERY live trade holds its contracts' streams — an idea with no
    # eyes on its contract is how an entry gets missed. Closed trades
    # release theirs.
    for key, trade in (context or {}).items():
        if not key.startswith("trades/") or not isinstance(trade, dict):
            continue
        if trade.get("state") == "closed":
            continue
        for candidate in [(trade.get("fill") or {}).get("contract"),
                          *(trade.get("contracts") or [])]:
            if not candidate:
                continue
            parsed = parse_contract_label(str(candidate))
            if "expiration" in parsed:
                armed.append(parsed)
    return [args for args in armed if args]


def is_order_card(key, card):
    return (key.startswith("widgets/fill-") and isinstance(card, dict)
            and card.get("kind") == "order")


def stream_matches_order(contract_key, args):
    """Does an active stream's contract key belong to this order's contract?

    Keys look like IBKR:OPT:NVDA:20260821:230:C:100:SMART:USD or
    IBKR:STK:NVDA:SMART:USD. Pure, pinned by tests.
    """

    tokens = str(contract_key or "").split(":")
    if len(tokens) < 3:
        return False
    symbol = str(args.get("symbol") or "").upper()
    default_type = "IND" if symbol in gates.INDEX_SYMBOLS else "STK"
    sec_type = str(args.get("sec_type") or default_type).upper()
    if tokens[1] != sec_type or tokens[2] != symbol:
        return False
    if sec_type != "OPT":
        return True

    def norm(value):
        try:
            return f"{float(value):g}"
        except (TypeError, ValueError):
            return str(value).upper()

    expected = {str(args.get("expiration") or ""),
                norm(args.get("strike")),
                str(args.get("right") or "").upper()}
    present = set(tokens) | {norm(token) for token in tokens}
    return expected <= present


def order_recording(card_key, context, check, now):
    """The writes one supporting check earns — from a card's own program
    or a live fill_watch tick; the rules are identical either way."""

    if not isinstance(check, dict) or check.get("verdict") != "fill-supported":
        return None
    fill = check.get("fill")
    if not isinstance(fill, dict):
        return None
    clock = gates.parse_clock(fill.get("observed_at"))
    if clock is None or not \
            0 <= (now - clock).total_seconds() <= RECORD_MAX_AGE_SECONDS:
        return None  # stale receipt — wait for the next check
    suffix = card_key[len("widgets/fill-"):]
    is_exit = suffix.endswith("-exit")
    trade_id = suffix[:-5] if is_exit else suffix
    trade = (context or {}).get(f"trades/{trade_id}")
    if not isinstance(trade, dict):
        return None
    state = trade.get("state")
    if is_exit and state != "open-simulated":
        return None
    if not is_exit and state not in ("idea", "watching"):
        return None
    recorded = dict(trade)
    if is_exit:
        recorded["exit"] = fill
        recorded["state"] = "closed"
    else:
        recorded["fill"] = fill
        recorded["state"] = "open-simulated"
    recorded["as_of"] = now.isoformat(timespec="seconds")
    return {
        "card_key": card_key,
        "case_key": f"trades/{trade_id}",
        "case": recorded,
        "summary": (
            f"paper {'exit' if is_exit else 'fill'} recorded — "
            f"{check.get('action', 'buy')} {fill['quantity']} × "
            f"{check.get('contract', trade_id)} @ ${fill['price']:g} "
            f"against the live market {fill['bid']:g} × {fill['ask']:g} "
            f"({fill['observed_at']})"),
    }


AUDIT_LEDGER = os.path.expanduser("~/.alphalab-autopilot/audit-ledger.jsonl")


def append_audit_ledger(environment, now, clean, broken_keys,
                        path=None):
    """One line per audit verdict, forever — the shared board keeps only
    the LATEST desk/audit, but corpus curation needs the history: which
    build turns were followed by a clean desk, and which broke it."""

    line = {"environment": environment,
            "at": now.isoformat(timespec="seconds"),
            "clean": bool(clean), "broken": list(broken_keys)}
    try:
        target = path or AUDIT_LEDGER
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")
    except OSError:
        pass


def live_trade_for_contract(context, contracts, ignore_key=None):
    """The existing non-closed trade already holding one of `contracts`.

    One contract is one live trade on this desk: new information amends
    the trade that exists, it does not open a rival idea.
    """

    for key, trade in sorted((context or {}).items()):
        if not key.startswith("trades/") or key == ignore_key:
            continue
        if not isinstance(trade, dict) or trade.get("state") == "closed":
            continue
        held = [str(c) for c in (trade.get("contracts") or [])]
        for wanted in contracts or []:
            for existing in held:
                if gates.contracts_match(str(wanted), existing):
                    return key, existing
    return None, None


def duplicate_contract_violations(cases):
    """Cross-trade rule: no contract lives in two non-closed trades."""

    violations = {}
    keys = sorted(k for k, v in (cases or {}).items()
                  if isinstance(v, dict) and v.get("state") != "closed")
    for index, key in enumerate(keys):
        for other in keys[index + 1:]:
            for contract_a in (cases[key].get("contracts") or []):
                for contract_b in (cases[other].get("contracts") or []):
                    if gates.contracts_match(str(contract_a), str(contract_b)):
                        message = (f"{contract_a}: one contract, one live "
                                   f"trade — this contract is also in "
                                   f"{other}; amend that trade instead")
                        violations.setdefault(key, []).append(message)
                        violations.setdefault(other, []).append(
                            f"{contract_b}: also held by {key} — merge the "
                            f"newer idea into this trade")
    return violations


def evidence_violations(cases, context):
    """Dependencies are references: a live trade's evidence keys must
    exist. Retiring a cell that a trade still cites breaks the trade's
    provenance — the audit names it so the desk restores or amends."""

    violations = {}
    for key, trade in sorted((cases or {}).items()):
        if not isinstance(trade, dict) or trade.get("state") == "closed":
            continue
        for cited in trade.get("evidence") or []:
            value = (context or {}).get(str(cited))
            if value is None:
                violations.setdefault(key, []).append(
                    f"evidence {cited!r} is gone from the desk — restore "
                    f"it or amend this trade's evidence list")
    return violations


def audit_violations(cases, check):
    """The post-turn audit: run the case gate over every case.

    Pure — `check(key, case)` returns that case's violations; the result
    maps only the broken cases to what is broken, by name. The
    cross-trade rule (one contract, one live trade) is layered on top.
    """

    violations = {}
    for key in sorted(cases):
        found = check(key, cases[key])
        if found:
            violations[key] = found
    for key, found in duplicate_contract_violations(cases).items():
        violations.setdefault(key, []).extend(found)
    return violations


def decide(context, state, now, budget=DEFAULT_BUDGET):
    """(action, reason): should the desk take a revision turn right now?

    Pure — the whole autopilot policy lives here, pinned by tests.
    """

    keys = set(context or {})
    if not any(k.startswith("intake/") for k in keys) and \
            not any(k.startswith("widgets/") for k in keys):
        return "wait", "waiting for the member's intake — the first move is theirs"
    last_turn = gates.parse_clock(state.get("last_turn"))
    member_turn = gates.parse_clock((context or {}).get("desk/member_turn"))
    turns = [turn for turn in (last_turn, member_turn) if turn is not None]
    reference = max(turns) if turns else None
    if reference is not None:
        since = (now - reference).total_seconds()
        if since < MIN_GAP_SECONDS:
            return "wait", f"inside the {MIN_GAP_SECONDS // 60}-minute minimum gap"
    if state.get("builds_today", 0) >= budget:
        return "wait", f"daily build budget spent ({budget})"
    seen = set(state.get("seen_answers") or [])
    new_answers = sorted(
        k for k in keys if k.startswith("answers/") and k not in seen
    )
    if new_answers:
        return "build", f"answer landed: {', '.join(new_answers)}"
    unnarrated = sorted(set(state.get("unnarrated_fills") or []))
    if unnarrated:
        return "build", f"paper fill recorded: {', '.join(unnarrated)}"
    next_check = gates.parse_clock((context or {}).get("desk/next_check"))
    if next_check is not None and next_check <= now:
        return "build", "the desk's own next-check clock came due"
    if reference is None:
        return "build", "first turn on this desk"
    gap = quiet_gap_minutes(now)
    if (now - reference).total_seconds() >= gap * 60:
        return "build", f"quiet-period maintenance ({phase(now)}, {gap}m gap)"
    return "wait", "nothing due"


class Pilot:
    def __init__(self, url, token, environment, budget, state_path,
                 model=None):
        self.url = url.rstrip("/")
        self.token = token
        self.environment = environment
        self.budget = budget
        self.state_path = state_path
        self.model = model  # scheduled turns default to an inexpensive mind
        self.state = self._load()

    def _load(self):
        try:
            with open(self.state_path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return fresh_state(utc_now())

    def _save(self):
        os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as handle:
            json.dump(self.state, handle, indent=1)

    def _api(self, method, path, body=None):
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-Manifold-Token": self.token,
                     "Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())

    def _check_trade(self, key, trade):
        try:
            reply = self._api(
                "POST", f"/environments/{self.environment}/tools/trade_check",
                {"args": {"trade": trade, "id": key[7:]}})
        except Exception as error:
            return [f"trade_check did not answer: {str(error)[:120]}"]
        result = reply.get("result") or {}
        data = result.get("data") or {}
        if isinstance(data.get("violations"), list):
            return [str(item) for item in data["violations"]]
        if result.get("ok"):
            return []
        return [str(gap) for gap in result.get("gaps") or ["trade_check failed"]]

    def audit(self, context, now):
        """Audit the cases after they change; the verdict lands at desk/audit
        where the next turn (and the desk header) can see it."""

        # a null value is a retired entry, not a broken trade
        trades = {k: v for k, v in context.items()
                  if k.startswith("trades/") and v is not None}
        evidence_keys = sorted(k for k, v in context.items()
                               if v is not None)
        fingerprint = json.dumps([trades, evidence_keys],
                                 sort_keys=True, default=str)
        if fingerprint == self.state.get("audit_fingerprint"):
            return None
        violations = audit_violations(trades, self._check_trade)
        for key, found in evidence_violations(trades, context).items():
            violations.setdefault(key, []).extend(found)
        self.state["audit_fingerprint"] = fingerprint
        self._save()
        append_audit_ledger(self.environment, now, not violations,
                            sorted(violations))
        self._api("POST", f"/environments/{self.environment}/context", {
            "key": "desk/audit",
            "value": {
                "clean": not violations,
                "cases_checked": len(trades),
                "violations": violations,
                "as_of": now.isoformat(timespec="seconds"),
            },
        })
        return violations

    def watch_orders(self, context, now):
        """The stream lane: run fill_watch per armed order — the freshest
        reqMktData tick, one standing leased subscription per contract —
        and build recordings from live truth. Falls back silently to the
        card's own snapshot check when the engine lane is down."""

        recordings = []
        for key, card in sorted((context or {}).items()):
            if not is_order_card(key, card):
                continue
            args = ((card.get("refresh") or {}).get("args")) or {}
            if not args.get("symbol"):
                continue
            try:
                reply = self._api(
                    "POST",
                    f"/environments/{self.environment}/tools/fill_watch",
                    {"args": args})
                check = (reply.get("result") or {}).get("data") or {}
            except Exception:
                continue
            recording = order_recording(key, context, check, now)
            if recording:
                recording["stream_args"] = args
                recordings.append(recording)
        return recordings

    def _stop_stream(self, args):
        """Release the order's standing subscription (and its lease)."""

        request = {field: args[field]
                   for field in ("symbol", "sec_type", "expiration",
                                 "strike", "right")
                   if args.get(field) not in (None, "")}
        if not request.get("symbol"):
            return
        try:
            self._api("POST",
                      f"/environments/{self.environment}/tools/market_stream",
                      {"args": {**request, "action": "stop"}})
        except Exception:
            pass

    def sweep_streams(self, context, extra_holders=None):
        """Release desk-owned streams no desk holds.

        A fill releases its stream on recording; this catches the other
        exit — an order cancelled by writing null on its card — so no
        subscription (or its client-id lease) outlives its purpose.
        With several desks open, the fleet passes every OTHER desk's
        holders as extra_holders — one desk's sweep must never take
        another desk's eyes.
        """

        armed = stream_holders(context) + list(extra_holders or [])
        try:
            reply = self._api(
                "POST", f"/environments/{self.environment}/tools/market_stream",
                {"args": {"action": "list_active"}})
            rows = (((reply.get("result") or {}).get("data") or {})
                    .get("rows")) or []
        except Exception:
            return 0
        stopped = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("owner") or "") != "alphalab-desk":
                continue
            key = row.get("contract_key")
            if any(stream_matches_order(key, args) for args in armed if args):
                continue
            stream_id = str(row.get("stream_id") or "")
            if not stream_id:
                continue
            try:
                self._api(
                    "POST",
                    f"/environments/{self.environment}/tools/market_stream",
                    {"args": {"action": "stop", "stream_id": stream_id}})
                stopped += 1
                print(f"[{utc_now():%H:%M:%S}] released orphaned stream "
                      f"{stream_id} ({key}) — no working order holds it",
                      flush=True)
            except Exception:
                pass
        return stopped

    def apply_forms(self, context, now):
        """Expand every pending form into its finished cell.

        The cheap-model lane: an agent writes minimal fields at forms/*;
        this pass assembles, validates, writes the real cell, and retires
        the form. Violations land back ON the form, named — the next turn
        reads its own mistake.
        """

        applied = 0
        templates = {key[10:]: value for key, value in (context or {}).items()
                     if key.startswith("templates/") and isinstance(value, dict)}
        for key, form in sorted((context or {}).items()):
            if not key.startswith("forms/") or not isinstance(form, dict):
                continue
            if form.get("errors") and not form.get("retry"):
                continue  # already named; wait for the agent to revise
            if key.startswith("forms/trade/"):
                trade, violations = forms.trade_from_form(form)
                target = "trades/" + key[len("forms/trade/"):]
                cell = trade
                if not violations:
                    existing_key, held = live_trade_for_contract(
                        context, trade.get("contracts"), ignore_key=target)
                    if existing_key:
                        # One contract, one live trade: fold the new
                        # thinking into the trade that already holds it.
                        existing = dict(context[existing_key])
                        for field in ("thesis", "invalidation"):
                            if trade.get(field):
                                existing[field] = trade[field]
                        merged_evidence = list(existing.get("evidence") or [])
                        for item in trade.get("evidence") or []:
                            if item not in merged_evidence:
                                merged_evidence.append(item)
                        existing["evidence"] = merged_evidence
                        target, cell = existing_key, existing
                        self._api(
                            "POST",
                            f"/environments/{self.environment}/say",
                            {"text": f"One contract, one live trade: "
                             f"{held} already lives in {existing_key} — "
                             f"amended it with the new thesis instead of "
                             f"opening a rival idea."})
            else:
                target, cell, violations = forms.expand(form, templates)
            if violations:
                marked = {k: v for k, v in form.items() if k != "retry"}
                marked["errors"] = violations
                marked["checked_at"] = now.isoformat(timespec="seconds")
                self._api("POST",
                          f"/environments/{self.environment}/context",
                          {"key": key, "value": marked})
                print(f"[{utc_now():%H:%M:%S}] form {key}: "
                      f"{len(violations)} problem(s) named", flush=True)
                continue
            if key.startswith("forms/trade/"):
                cell["as_of"] = now.isoformat(timespec="seconds")
            self._api("POST", f"/environments/{self.environment}/context",
                      {"key": target, "value": cell})
            self._api("POST", f"/environments/{self.environment}/context",
                      {"key": key, "value": None})
            applied += 1
            print(f"[{utc_now():%H:%M:%S}] form {key} → {target}", flush=True)
        return applied

    def manage_plans(self, context, now):
        """Run every ACTIVE plan's decision program against fresh inputs.

        The member activated the plan after inspecting its code and test
        results; this pass only gathers the declared read-only inputs,
        runs the pure decision function, and routes its bounded actions
        through the same order cards and market gate as every other fill
        on this desk. Watermarks and ratchet levels persist as the plan's
        own state between passes.
        """

        ran = 0
        for key, plan in sorted((context or {}).items()):
            if not key.startswith("plans/") or not isinstance(plan, dict):
                continue
            if plan.get("status") != "active":
                continue
            if not plan.get("active_at"):
                # activation is the MEMBER's act — the UI stamps
                # active_at when they press the button after inspecting.
                # An agent-written "active" without the stamp does not
                # run; the audit of conventions is a tripwire, not trust.
                continue
            trade_id = key[len("plans/"):]
            if not plan.get("archived_at"):
                # The member just activated this plan — the endorsement
                # makes it a library example; the close will judge it.
                try:
                    plans.archive(trade_id, plan)
                    marked = dict(plan)
                    marked["archived_at"] = now.isoformat(timespec="seconds")
                    self._api("POST",
                              f"/environments/{self.environment}/context",
                              {"key": key, "value": marked})
                    plan = marked
                except OSError as error:
                    print(f"[{utc_now():%H:%M:%S}] plan library: "
                          f"{str(error)[:120]}", flush=True)
            trade = (context or {}).get(f"trades/{trade_id}")
            if not isinstance(trade, dict) \
                    or trade.get("state") == "closed":
                continue  # plans watch entries too; only closed retires
            program = plan.get("program") or {}
            if plans.program_violations(program):
                continue  # named by plan_check; a broken program never runs
            fill = trade.get("fill") or {}
            inputs = {"position": {"entry": fill.get("price"),
                                   "quantity": fill.get("quantity"),
                                   "contract": fill.get("contract")},
                      "now": now.astimezone(gates.ET)
                                .isoformat(timespec="seconds")}
            blind = False
            for declaration in program.get("inputs") or []:
                try:
                    reply = self._api(
                        "POST",
                        f"/environments/{self.environment}/tools/"
                        f"{declaration.get('tool')}",
                        {"args": declaration.get("args") or {}})
                    inputs[str(declaration["name"])] = \
                        (reply.get("result") or {}).get("data")
                except Exception:
                    blind = True
                    break
            if blind:
                continue  # inputs return next pass; never decide blind
            result, error = plans.run_decision(
                program.get("code") or "", inputs, plan.get("state"))
            update = dict(plan)
            update["last_run"] = now.isoformat(timespec="seconds")
            closing = not error and any(
                a.get("action") == "close" for a in result["actions"])
            if not error and trade.get("state") == "open-simulated" \
                    and not result.get("market") and not closing:
                error = ("the bot contract: an open position's plan must "
                         "answer market {stop, target} — where it is a "
                         "seller on both sides, even far from price "
                         "(a close action exempts the pass)")
            if error:
                update["last_error"] = error
            else:
                update.pop("last_error", None)
                update["state"] = result["state"]
                if result.get("market"):
                    update["market"] = result["market"]
                    self._maintain_bracket(trade_id, trade,
                                           result["market"], context)
                for action in result["actions"]:
                    self._apply_plan_action(trade_id, trade, action,
                                            context, update)
            stale = gates.parse_clock(plan.get("last_run"))
            changed = (update.get("state") != plan.get("state")
                       or update.get("last_error") != plan.get("last_error")
                       or update.get("last_note") != plan.get("last_note"))
            if changed or stale is None \
                    or (now - stale).total_seconds() >= 300:
                self._api("POST",
                          f"/environments/{self.environment}/context",
                          {"key": key, "value": update})
            if error:
                print(f"[{utc_now():%H:%M:%S}] plan {key}: {error}",
                      flush=True)
            else:
                ran += 1
        return ran

    def _maintain_bracket(self, trade_id, trade, market, context):
        """The bot's market becomes the working bracket order.

        One card carries both legs: the target as the sell limit, the
        stop underneath. The gate fills whichever leg the market
        touches; deterministic executors (this pass, the card's own
        refresh, and the tick relay) act on the same levels.
        """

        if trade.get("state") != "open-simulated":
            return
        stop = market.get("stop")
        target = market.get("target")
        if stop is None or target is None:
            return
        card_key = f"widgets/fill-{trade_id}-exit"
        label = str((trade.get("fill") or {}).get("contract")
                    or (trade.get("contracts") or [trade_id])[0])
        if "expiration" not in parse_contract_label(label):
            for candidate in (trade.get("contracts") or []):
                if "expiration" in parse_contract_label(str(candidate)):
                    label = str(candidate)
                    break
        existing = ((context.get(card_key) or {})
                    .get("refresh") or {}).get("args") or {}
        if existing.get("price") == round(float(target), 2) \
                and existing.get("stop") == round(float(stop), 2):
            return  # the bracket already works these exact levels
        quantity = int((trade.get("fill") or {}).get("quantity") or 1)
        card = {"kind": "order",
                "title": f"Bracket — {label} "
                         f"(stop {stop:g} / target {target:g})",
                "plan": f"plans/{trade_id}",
                "refresh": {"tool": "fill_watch",
                            "args": {**parse_contract_label(label),
                                     "price": round(float(target), 2),
                                     "stop": round(float(stop), 2),
                                     "quantity": quantity,
                                     "action": "sell",
                                     "contract": label},
                            "minutes": 2, "value_path": "result.data",
                            "into": "check"}}
        self._api("POST", f"/environments/{self.environment}/context",
                  {"key": card_key, "value": card})
        print(f"[{utc_now():%H:%M:%S}] bracket {trade_id}: "
              f"stop {stop:g} / target {target:g}", flush=True)

    def _apply_plan_action(self, trade_id, trade, action, context, update):
        """One bounded plan action → the desk's existing primitives."""

        kind = action["action"]
        card_key = f"widgets/fill-{trade_id}-exit"
        # The exit must stream the OPTION, so prefer a label that parses
        # to full contract args — a fill recorded in display notation
        # ("NVDA 8/21 235C") has no 8-digit date and would quote the stock.
        label = str((trade.get("fill") or {}).get("contract") or trade_id)
        if "expiration" not in parse_contract_label(label):
            for candidate in (trade.get("contracts") or []):
                if "expiration" in parse_contract_label(str(candidate)):
                    label = str(candidate)
                    break
        if kind == "note":
            text = str(action.get("text") or "").strip()
            if text and text != update.get("last_note"):
                update["last_note"] = text
                self._api("POST",
                          f"/environments/{self.environment}/say",
                          {"text": f"[plan {trade_id}] {text}"})
            return
        if kind == "cancel_exit":
            if context.get(card_key) is not None:
                self._api("POST",
                          f"/environments/{self.environment}/context",
                          {"key": card_key, "value": None})
                print(f"[{utc_now():%H:%M:%S}] plan {trade_id}: "
                      "exit order cancelled", flush=True)
            return
        if kind == "arm_entry":
            if trade.get("state") not in ("idea", "watching"):
                return  # entries arm only before a fill exists
            entry_key = f"widgets/fill-{trade_id}"
            entry_price = round(float(action["price"]), 2)
            working = ((context.get(entry_key) or {})
                       .get("refresh") or {}).get("args") or {}
            if working.get("price") == entry_price \
                    and working.get("action", "buy") == "buy":
                return  # the desired entry is already working
            quantity = int(action.get("quantity") or 1)
            card = {"kind": "order",
                    "title": f"Plan entry — {label}",
                    "plan": f"plans/{trade_id}",
                    "refresh": {"tool": "fill_watch",
                                "args": {**parse_contract_label(label),
                                         "price": entry_price,
                                         "quantity": max(1, min(quantity, 100)),
                                         "action": "buy",
                                         "contract": label},
                                "minutes": 2, "value_path": "result.data",
                                "into": "check"}}
            self._api("POST", f"/environments/{self.environment}/context",
                      {"key": entry_key, "value": card})
            self._api("POST", f"/environments/{self.environment}/say",
                      {"text": f"[plan {trade_id}] entry armed at "
                               f"${entry_price:g} — the gate watches "
                               f"from here"})
            print(f"[{utc_now():%H:%M:%S}] plan {trade_id}: entry armed "
                  f"@ ${entry_price:g}", flush=True)
            return
        # close = a marketable sell: a 0.01 limit executes AT the bid
        # through the same gate as every fill; place_exit rests a limit.
        price = 0.01 if kind == "close" else round(float(action["price"]), 2)
        existing = ((context.get(card_key) or {})
                    .get("refresh") or {}).get("args") or {}
        if existing.get("price") == price \
                and existing.get("action") == "sell":
            return  # the desired exit is already working
        quantity = int((trade.get("fill") or {}).get("quantity") or 1)
        card = {"kind": "order",
                "title": f"Plan exit — {label}",
                "plan": f"plans/{trade_id}",
                "refresh": {"tool": "fill_watch",
                            "args": {**parse_contract_label(label),
                                     "price": price, "quantity": quantity,
                                     "action": "sell", "contract": label},
                            "minutes": 2, "value_path": "result.data",
                            "into": "check"}}
        self._api("POST", f"/environments/{self.environment}/context",
                  {"key": card_key, "value": card})
        self._api("POST", f"/environments/{self.environment}/say",
                  {"text": f"[plan {trade_id}] "
                   + ("closing at the market (sell limit $0.01 executes "
                      "at the bid)" if kind == "close"
                      else f"exit limit now working at ${price:g}")})
        print(f"[{utc_now():%H:%M:%S}] plan {trade_id}: {kind} "
              f"@ ${price:g}", flush=True)

    def stream_health(self, context, now):
        """Watchlist truth, written where everyone can see it.

        Each pass reads the freshest persisted tick per watchlist symbol
        (fast batch — no warming) and writes desk/streams: last, clock,
        age, and which symbols are STALE or silent. The member's chips
        show it; the agent's next turn reads it and can act (re-warm,
        check the lane) instead of assuming the chips are fine.
        """

        watchlist = (context or {}).get("watchlist")
        if isinstance(watchlist, str):
            watchlist = watchlist.replace(",", " ").split()
        symbols = [str(s).strip().upper() for s in (watchlist or []) if s]
        # Open positions' own contracts are watched the same way — the
        # PnL mark reads their stream, so a dead one lies to the member.
        contracts = []
        seen_labels = set()
        for key, value in (context or {}).items():
            if not isinstance(value, dict):
                continue
            refresh = value.get("refresh") or {}
            args = refresh.get("args") if isinstance(refresh, dict) else None
            if not (isinstance(args, dict)
                    and str(refresh.get("tool")) == "live_quote"
                    and args.get("expiration") and args.get("strike")):
                continue
            label = str(args.get("contract")
                        or f"{str(args['symbol']).upper()} "
                           f"{args['expiration']} "
                           f"{float(args['strike']):g}"
                           f"{str(args.get('right') or 'C').upper()}")
            parsed = {"symbol": str(args["symbol"]).upper(),
                      "sec_type": "OPT",
                      "expiration": str(args["expiration"]),
                      "strike": float(args["strike"]),
                      "right": str(args.get("right") or "C").upper()}
            if label not in seen_labels:
                seen_labels.add(label)
                contracts.append((label, parsed))
        for key, trade in (context or {}).items():
            if not key.startswith("trades/") or not isinstance(trade, dict):
                continue
            if trade.get("state") == "closed":
                continue  # ideas and watchers hold eyes too — an entry
                          # can only be caught by a stream that exists
            for candidate in [(trade.get("fill") or {}).get("contract"),
                              *(trade.get("contracts") or [])]:
                if not candidate:
                    continue
                label = str(candidate)
                parsed = parse_contract_label(label)
                if "expiration" in parsed and label not in seen_labels:
                    seen_labels.add(label)
                    contracts.append((label, parsed))
        if not symbols and not contracts:
            return None
        try:
            reply = self._api(
                "POST",
                f"/environments/{self.environment}/tools/live_quotes",
                {"args": {"symbols": symbols}})
            quotes = (((reply.get("result") or {}).get("data") or {})
                      .get("quotes")) or {}
        except Exception as error:
            quotes = {}
            print(f"[{utc_now():%H:%M:%S}] stream health check failed: "
                  f"{str(error)[:120]}", flush=True)
        health, stale = {}, []
        stale_contracts = []
        for label, parsed in contracts:
            try:
                reply = self._api(
                    "POST",
                    f"/environments/{self.environment}/tools/live_quote",
                    {"args": parsed})
                quote = (((reply.get("result") or {}).get("data") or {})
                         .get("quote")) or {}
            except Exception:
                quote = {}
            clock = gates.parse_clock(quote.get("observed_at"))
            age = None if clock is None \
                else max(0, int((now - clock).total_seconds()))
            health[label] = None if quote.get("bid") is None else {
                "last": quote.get("last"), "bid": quote.get("bid"),
                "observed_at": quote.get("observed_at"),
                "age_seconds": age}
            if age is None or age > 180:
                stale.append(label)
                stale_contracts.append((label, parsed))
        for symbol in symbols:
            quote = quotes.get(symbol)
            if not isinstance(quote, dict) or quote.get("last") is None:
                health[symbol] = None
                stale.append(symbol)
                continue
            clock = gates.parse_clock(quote.get("observed_at"))
            age = None if clock is None \
                else max(0, int((now - clock).total_seconds()))
            health[symbol] = {"last": quote.get("last"),
                              "observed_at": quote.get("observed_at"),
                              "age_seconds": age}
            if age is None or age > 180:
                stale.append(symbol)
        report = {"quotes": health, "stale": sorted(stale),
                  "checked_at": now.isoformat(timespec="seconds")}
        # Self-healing: a stale stream is re-warmed, not just reported —
        # when the broker connection returns, the desk comes back on its
        # own. Throttled per symbol so a dead lane isn't hammered.
        warm = []
        attempts = self.state.setdefault("warm_attempts", {})
        for symbol in report["stale"]:
            last = gates.parse_clock(attempts.get(symbol))
            if last is None or (now - last).total_seconds() >= 300:
                warm.append(symbol)
                attempts[symbol] = now.isoformat(timespec="seconds")
        stale_contract_map = dict(stale_contracts)
        if warm:
            self._save()
            # Stop-then-start, not a plain warm: the engine's start
            # trusts a registration row that can outlive its dead
            # worker ("already active", zero live workers) — clearing
            # the phantom first is what actually revives the stream.
            for symbol in warm:
                request = stale_contract_map.get(symbol) \
                    or {"symbol": symbol}
                if "sec_type" not in request \
                        and symbol in gates.INDEX_SYMBOLS:
                    request = {"symbol": symbol, "sec_type": "IND"}
                try:
                    self._api(
                        "POST",
                        f"/environments/{self.environment}"
                        f"/tools/market_stream",
                        {"args": {**request, "action": "stop"}})
                    # stop is asynchronous ("stop requested") — start
                    # too soon and the phantom still answers "already
                    # active" with no live worker underneath
                    time.sleep(2)
                    self._api(
                        "POST",
                        f"/environments/{self.environment}"
                        f"/tools/market_stream",
                        {"args": {**request, "action": "start",
                                  "owner": "alphalab-desk"}})
                except Exception:
                    continue
            print(f"[{utc_now():%H:%M:%S}] restarted stale stream(s): "
                  f"{', '.join(warm)}", flush=True)
        previous = (context or {}).get("desk/streams") or {}
        last_write = gates.parse_clock(previous.get("checked_at"))
        if sorted(previous.get("stale") or []) != report["stale"] \
                or last_write is None \
                or (now - last_write).total_seconds() >= 300:
            self._api("POST", f"/environments/{self.environment}/context",
                      {"key": "desk/streams", "value": report})
            if report["stale"]:
                print(f"[{utc_now():%H:%M:%S}] streams stale: "
                      f"{', '.join(report['stale'])}", flush=True)
        return report

    def compute_overlays(self, context, now):
        """Run every overlay program; computed data lands on its entry.

        An agent writes code that THINKS about a trade — gamma-derived
        levels, option-volume signals — as `def compute(inputs)` with
        declared read-only inputs, on the overlay entry itself. Same
        restricted namespace as plan decisions; output must be overlay
        data (validated, bounded); errors land on the entry, named.
        """

        ran = 0
        for key, overlay in sorted((context or {}).items()):
            if not key.startswith("overlays/") \
                    or not isinstance(overlay, dict):
                continue
            program = overlay.get("program")
            if not isinstance(program, dict) or not program.get("code"):
                continue
            cadence = max(2, int(overlay.get("minutes") or 10))
            last = gates.parse_clock(overlay.get("computed_at"))
            if last is not None \
                    and (now - last).total_seconds() < cadence * 60:
                continue
            inputs = {"now": now.astimezone(gates.ET)
                              .isoformat(timespec="seconds")}
            blind = False
            for declaration in program.get("inputs") or []:
                tool = str(declaration.get("tool") or "")
                if tool not in plans.READ_ONLY_TOOLS:
                    blind = True
                    break
                try:
                    reply = self._api(
                        "POST",
                        f"/environments/{self.environment}/tools/{tool}",
                        {"args": declaration.get("args") or {}})
                    inputs[str(declaration.get("name"))] = \
                        (reply.get("result") or {}).get("data")
                except Exception:
                    blind = True
                    break
            if blind:
                continue
            result, error = plans.run_compute(program.get("code"), inputs)
            update = dict(overlay)
            update["computed_at"] = now.isoformat(timespec="seconds")
            if error:
                update["last_error"] = error
                print(f"[{utc_now():%H:%M:%S}] overlay {key}: {error}",
                      flush=True)
            else:
                update.pop("last_error", None)
                for field in ("levels", "bands", "clocks", "note"):
                    if field in result:
                        update[field] = result[field]
                    else:
                        update.pop(field, None)
                if result.get("target"):
                    update["target"] = result["target"]
                ran += 1
            self._api("POST", f"/environments/{self.environment}/context",
                      {"key": key, "value": update})
        return ran

    def record_orders(self, context, now):
        """Record every order whose market gate holds; announce each one."""

        by_case = {}
        for recording in self.watch_orders(context, now) \
                + supported_orders(context, now):
            by_case.setdefault(recording["case_key"], recording)
        recordings = list(by_case.values())
        for recording in recordings:
            self._api("POST", f"/environments/{self.environment}/context",
                      {"key": recording["case_key"], "value": recording["case"]})
            self._api("POST", f"/environments/{self.environment}/context",
                      {"key": recording["card_key"], "value": None})
            self._api("POST", f"/environments/{self.environment}/say",
                      {"text": recording["summary"].capitalize() + "."})
            fills = self.state.setdefault("unnarrated_fills", [])
            if recording["case_key"] not in fills:
                fills.append(recording["case_key"])
            stream_args = recording.get("stream_args") or (
                ((context.get(recording["card_key"]) or {})
                 .get("refresh") or {}).get("args") or {})
            self._stop_stream(stream_args)
            if recording["case"].get("state") == "closed":
                plan_key = "plans/" + recording["case_key"][len("trades/"):]
                plan = context.get(plan_key)
                if isinstance(plan, dict) and plan.get("program"):
                    retired = dict(plan)
                    retired["status"] = "completed"
                    try:
                        plans.archive(
                            recording["case_key"][len("trades/"):],
                            retired,
                            outcome=plans.close_outcome(recording["case"]))
                    except OSError as error:
                        print(f"[{utc_now():%H:%M:%S}] plan library: "
                              f"{str(error)[:120]}", flush=True)
                    self._api("POST",
                              f"/environments/{self.environment}/context",
                              {"key": plan_key, "value": retired})
            print(f"[{utc_now():%H:%M:%S}] {recording['summary']}", flush=True)
        if recordings:
            self._save()
        return recordings

    def tick(self, now=None):
        now = now or utc_now()
        roll_date(self.state, now)
        view = self._api("GET", f"/environments/{self.environment}")
        context = view.get("context") or {}
        self.apply_forms(context, now)
        audited = self.audit(context, now)
        if audited:
            print(f"[{utc_now():%H:%M:%S}] audit: {len(audited)} case(s) "
                  f"broken — {', '.join(sorted(audited))}", flush=True)
        self.record_orders(context, now)
        self.manage_plans(context, now)
        self.compute_overlays(context, now)
        self.stream_health(context, now)
        self.last_holders = stream_holders(context)
        action, reason = decide(context, self.state, now, self.budget)
        if action == "build":
            self._api("POST", f"/environments/{self.environment}/build",
                      {"model": self.model} if self.model else {})
            self.state["last_turn"] = now.isoformat(timespec="seconds")
            self.state["builds_today"] = self.state.get("builds_today", 0) + 1
            self.state["seen_answers"] = sorted(
                k for k in context if k.startswith("answers/"))
            self.state["unnarrated_fills"] = []
            self._save()
            self._api("POST", f"/environments/{self.environment}/context", {
                "key": "desk/autopilot",
                "value": {
                    "last_turn": self.state["last_turn"],
                    "reason": reason,
                    "builds_today": self.state["builds_today"],
                    "budget": self.budget,
                },
            })
        return action, reason


class Fleet:
    """One autopilot for every AlphaLab desk on this service.

    The member can open as many desks as they like (mess one up, start a
    fresh one — the platform home page opens them); the fleet discovers
    every environment running this harness each pass, keeps one Pilot
    (own state file, own budget) per desk, and runs the stream sweep
    ONCE with the union of every desk's holders — one desk's sweep must
    never take another desk's streams.
    """

    def __init__(self, url, token, budget, state_dir, model=None,
                 harness_name="AlphaLab", environment=None):
        self.url = url.rstrip("/")
        self.token = token
        self.budget = budget
        self.state_dir = state_dir
        self.model = model
        self.harness_name = harness_name
        self.environment = environment  # pinned single-desk mode
        self.pilots = {}

    def discover(self):
        if self.environment:
            return [self.environment]
        probe = Pilot.__new__(Pilot)
        probe.url, probe.token = self.url, self.token
        try:
            listed = probe._api("GET", "/environments")
        except Exception:
            return sorted(self.pilots)  # service blinked; keep known desks
        return [item["environment_id"] for item in listed
                if isinstance(item, dict)
                and item.get("harness") == self.harness_name]

    def _pilot(self, environment):
        pilot = self.pilots.get(environment)
        if pilot is None:
            state_path = os.path.join(
                self.state_dir, f"{environment}.json")
            pilot = Pilot(self.url, self.token, environment, self.budget,
                          state_path, model=self.model)
            self.pilots[environment] = pilot
            print(f"[{utc_now():%H:%M:%S}] desk {environment[:8]} "
                  f"under autopilot", flush=True)
        return pilot

    def tick(self):
        environments = self.discover()
        results = []
        for environment in environments:
            pilot = self._pilot(environment)
            try:
                action, reason = pilot.tick()
                results.append((environment, action, reason))
            except Exception as error:
                results.append((environment, "error", str(error)[:200]))
        for stale in set(self.pilots) - set(environments):
            print(f"[{utc_now():%H:%M:%S}] desk {stale[:8]} is gone; "
                  f"autopilot released", flush=True)
            self.pilots.pop(stale)
        # One sweep for the whole fleet: every desk's holders protect
        # every stream; only truly orphaned subscriptions are released.
        live = [p for e, p in self.pilots.items() if e in environments]
        if live:
            union = [args for pilot in live
                     for args in getattr(pilot, "last_holders", [])]
            try:
                live[0].sweep_streams(None, extra_holders=union)
            except Exception:
                pass
        return results


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--environment", default=None,
                        help="pin to one environment; omit to serve every "
                             "desk running this harness")
    parser.add_argument("--harness", default="AlphaLab",
                        help="harness name to discover desks by")
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET,
                        help="daily build budget PER DESK")
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--state-dir",
                        default=os.path.expanduser("~/.alphalab-autopilot"))
    parser.add_argument("--model", default="sonnet",
                        help="model id for scheduled turns (see GET /models)")
    arguments = parser.parse_args()
    os.makedirs(arguments.state_dir, exist_ok=True)
    fleet = Fleet(arguments.url, arguments.token, arguments.budget,
                  arguments.state_dir, model=arguments.model,
                  harness_name=arguments.harness,
                  environment=arguments.environment)
    print(f"[{utc_now():%H:%M:%S}] autopilot fleet on "
          f"{arguments.environment or f'every {arguments.harness} desk'} "
          f"(budget {arguments.budget}/day per desk, "
          f"min gap {MIN_GAP_SECONDS // 60}m)", flush=True)
    last = {}
    while True:
        try:
            for environment, action, reason in fleet.tick():
                if action == "build" or last.get(environment) != reason:
                    print(f"[{utc_now():%H:%M:%S}] {environment[:8]} "
                          f"{action}: {reason}", flush=True)
                last[environment] = reason
        except Exception as error:
            print(f"[{utc_now():%H:%M:%S}] error: {error}", flush=True)
        time.sleep(arguments.interval)


if __name__ == "__main__":
    main()
