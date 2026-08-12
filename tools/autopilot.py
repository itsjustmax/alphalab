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


def audit_violations(cases, check):
    """The post-turn audit: run the case gate over every case.

    Pure — `check(key, case)` returns that case's violations; the result
    maps only the broken cases to what is broken, by name.
    """

    violations = {}
    for key in sorted(cases):
        found = check(key, cases[key])
        if found:
            violations[key] = found
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

        trades = {k: v for k, v in context.items() if k.startswith("trades/")}
        fingerprint = json.dumps(trades, sort_keys=True, default=str)
        if fingerprint == self.state.get("audit_fingerprint"):
            return None
        violations = audit_violations(trades, self._check_trade)
        self.state["audit_fingerprint"] = fingerprint
        self._save()
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

    def sweep_streams(self, context):
        """Release desk-owned streams no working order holds.

        A fill releases its stream on recording; this catches the other
        exit — an order cancelled by writing null on its card — so no
        subscription (or its client-id lease) outlives its purpose.
        Watchlist symbols hold their streams: the chips read them live.
        """

        armed = [((card.get("refresh") or {}).get("args") or {})
                 for key, card in (context or {}).items()
                 if is_order_card(key, card)]
        # The watchlist holds its streams too — chips read them live.
        watchlist = (context or {}).get("watchlist")
        if isinstance(watchlist, str):
            watchlist = watchlist.replace(",", " ").split()
        for symbol in (watchlist or []):
            armed.append({"symbol": str(symbol).strip().upper()})
        # Active plans hold the streams their declared inputs read, and
        # every open position holds its own contract's stream (the PnL
        # mark) — a plan mid-trail must never lose its eyes.
        for key, plan in (context or {}).items():
            if not key.startswith("plans/") or not isinstance(plan, dict):
                continue
            if plan.get("status") != "active":
                continue
            for declaration in ((plan.get("program") or {})
                                .get("inputs") or []):
                if isinstance(declaration, dict) \
                        and isinstance(declaration.get("args"), dict) \
                        and declaration["args"].get("symbol"):
                    armed.append(declaration["args"])
        for key, trade in (context or {}).items():
            if not key.startswith("trades/") or not isinstance(trade, dict):
                continue
            if trade.get("state") != "open-simulated":
                continue
            fill_contract = (trade.get("fill") or {}).get("contract")
            if fill_contract:
                armed.append(parse_contract_label(str(fill_contract)))
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
            trade_id = key[len("plans/"):]
            trade = (context or {}).get(f"trades/{trade_id}")
            if not isinstance(trade, dict) \
                    or trade.get("state") != "open-simulated":
                continue
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
            if error:
                update["last_error"] = error
            else:
                update.pop("last_error", None)
                update["state"] = result["state"]
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

    def _apply_plan_action(self, trade_id, trade, action, context, update):
        """One bounded plan action → the desk's existing primitives."""

        kind = action["action"]
        card_key = f"widgets/fill-{trade_id}-exit"
        label = str((trade.get("fill") or {}).get("contract") or trade_id)
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
        self.sweep_streams(context)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--state", default=None)
    parser.add_argument("--model", default="sonnet",
                        help="model id for scheduled turns (see GET /models)")
    arguments = parser.parse_args()
    state_path = arguments.state or os.path.expanduser(
        f"~/.alphalab-autopilot/{arguments.environment}.json")
    pilot = Pilot(arguments.url, arguments.token, arguments.environment,
                  arguments.budget, state_path, model=arguments.model)
    last_reason = None
    print(f"[{utc_now():%H:%M:%S}] autopilot on {arguments.environment} "
          f"(budget {arguments.budget}/day, min gap {MIN_GAP_SECONDS // 60}m)",
          flush=True)
    while True:
        try:
            action, reason = pilot.tick()
            if action == "build" or reason != last_reason:
                print(f"[{utc_now():%H:%M:%S}] {action}: {reason}", flush=True)
            last_reason = reason
        except Exception as error:
            print(f"[{utc_now():%H:%M:%S}] error: {error}", flush=True)
        time.sleep(arguments.interval)


if __name__ == "__main__":
    main()
