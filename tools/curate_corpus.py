#!/usr/bin/env python3
"""Curate the model-run corpus with the desk's own verdicts.

The platform records every model run in full (~/.manifold/model-runs)
and exports raw corpora; this tool adds what only the harness knows:
whether the desk's gates approved of a turn's consequences. A build
turn whose next audit was clean is a lesson; one that broke a trade is
a counter-example; one the audit never saw stays innocent (the exit
code passed and nothing objected) unless --judged-only says otherwise.

The verdict source is the autopilot's audit ledger
(~/.alphalab-autopilot/audit-ledger.jsonl) — one line per audit,
forever — joined to each build record by environment and clock: the
FIRST audit after the run judges it, if it lands within --window
minutes.

Usage:
  python3 tools/curate_corpus.py --model llama70 --out DIR \
      [--harness alphalab] [--judged-only] [--window 120]

Answers train.jsonl / valid.jsonl (chat format, deduplicated, via the
platform exporter) plus a curation report that names every count.
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "engine"))
import gates  # noqa: E402  (parse_clock — one rulebook for clocks)

DEFAULT_LEDGER = os.path.expanduser(
    "~/.alphalab-autopilot/audit-ledger.jsonl")
DEFAULT_WINDOW_MINUTES = 120


def read_ledger(path):
    """[(environment, clock, clean)] sorted by clock; bad lines skipped."""

    entries = []
    if not os.path.isfile(path):
        return entries
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            clock = gates.parse_clock(record.get("at"))
            if clock is None or not record.get("environment"):
                continue
            entries.append((str(record["environment"]), clock,
                            bool(record.get("clean"))))
    entries.sort(key=lambda item: item[1])
    return entries


def verdict_for(record, ledger, window_minutes=DEFAULT_WINDOW_MINUTES):
    """'accepted' | 'rejected' | 'unjudged' for one model-run record.

    The first audit in the run's environment AFTER the run judges it —
    the audit fires when trades change, so it sees what the turn did.
    Past the window, causality is too weak to claim; the run stays
    unjudged. Pure, pinned by tests.
    """

    run_clock = gates.parse_clock(record.get("at"))
    environment = record.get("environment")
    if run_clock is None or not environment:
        return "unjudged"
    for entry_env, clock, clean in ledger:
        if entry_env != environment or clock <= run_clock:
            continue
        if (clock - run_clock).total_seconds() > window_minutes * 60:
            return "unjudged"
        return "accepted" if clean else "rejected"
    return "unjudged"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--task", default="build",
                        help="which lane to curate (default: build)")
    parser.add_argument("--harness", default="alphalab")
    parser.add_argument("--manifold-home",
                        default=os.path.expanduser("~/.manifold"))
    parser.add_argument("--ledger", default=DEFAULT_LEDGER)
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW_MINUTES,
                        help="minutes an audit may lag a run and still judge it")
    parser.add_argument("--judged-only", action="store_true",
                        help="drop unjudged runs instead of keeping them")
    arguments = parser.parse_args()

    manifold_src = os.environ.get("MANIFOLD_SRC", "").strip()
    if manifold_src:
        sys.path.insert(0, manifold_src)
    try:
        from manifold.corpus import export
    except ImportError:
        print("The manifold package is not importable — pip install it, "
              "or set MANIFOLD_SRC to the platform repo's src/ folder.",
              file=sys.stderr)
        raise SystemExit(1)

    ledger = read_ledger(arguments.ledger)
    tally = {"accepted": 0, "rejected": 0, "unjudged": 0}

    def accept(record):
        verdict = verdict_for(record, ledger, arguments.window)
        tally[verdict] += 1
        if verdict == "rejected":
            return False
        if verdict == "unjudged" and arguments.judged_only:
            return False
        return True

    report = export(arguments.manifold_home, arguments.model,
                    arguments.out, task=arguments.task,
                    harness=arguments.harness or None, accept=accept)
    print(f"curated {arguments.model} [{arguments.task}] -> {report['out']}")
    print(f"  audit verdicts: {tally['accepted']} accepted, "
          f"{tally['rejected']} rejected, {tally['unjudged']} unjudged"
          + (" (dropped)" if arguments.judged_only else " (kept — the exit "
             "code passed and no audit objected)"))
    print(f"  exported: {report['train']} train / {report['valid']} valid, "
          f"{report['duplicates_skipped']} duplicate(s) collapsed")
    print(f"  lengths: median {report['median_chars']}, "
          f"p95 {report['p95_chars']}, max {report['max_chars']} chars "
          f"— max seq length ~ {max(512, report['p95_chars'] // 4)} tokens")


if __name__ == "__main__":
    main()
