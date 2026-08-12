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
