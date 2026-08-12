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
import gates  # noqa: E402  (ET, parse_clock — one rulebook for clocks)

MIN_GAP_SECONDS = 10 * 60
DEFAULT_BUDGET = 36


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


def decide(context, state, now, budget=DEFAULT_BUDGET):
    """(action, reason): should the desk take a revision turn right now?

    Pure — the whole autopilot policy lives here, pinned by tests.
    """

    keys = set(context or {})
    if not any(k.startswith("intake/") for k in keys) and \
            not any(k.startswith("widgets/") for k in keys):
        return "wait", "waiting for the member's intake — the first move is theirs"
    last_turn = gates.parse_clock(state.get("last_turn"))
    if last_turn is not None:
        since = (now - last_turn).total_seconds()
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
    next_check = gates.parse_clock((context or {}).get("desk/next_check"))
    if next_check is not None and next_check <= now:
        return "build", "the desk's own next-check clock came due"
    if last_turn is None:
        return "build", "first turn on this desk"
    gap = quiet_gap_minutes(now)
    if (now - last_turn).total_seconds() >= gap * 60:
        return "build", f"quiet-period maintenance ({phase(now)}, {gap}m gap)"
    return "wait", "nothing due"


class Pilot:
    def __init__(self, url, token, environment, budget, state_path):
        self.url = url.rstrip("/")
        self.token = token
        self.environment = environment
        self.budget = budget
        self.state_path = state_path
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

    def tick(self, now=None):
        now = now or utc_now()
        roll_date(self.state, now)
        view = self._api("GET", f"/environments/{self.environment}")
        context = view.get("context") or {}
        action, reason = decide(context, self.state, now, self.budget)
        if action == "build":
            self._api("POST", f"/environments/{self.environment}/build", {})
            self.state["last_turn"] = now.isoformat(timespec="seconds")
            self.state["builds_today"] = self.state.get("builds_today", 0) + 1
            self.state["seen_answers"] = sorted(
                k for k in context if k.startswith("answers/"))
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
    arguments = parser.parse_args()
    state_path = arguments.state or os.path.expanduser(
        f"~/.alphalab-autopilot/{arguments.environment}.json")
    pilot = Pilot(arguments.url, arguments.token, arguments.environment,
                  arguments.budget, state_path)
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
