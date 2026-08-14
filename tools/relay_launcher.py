#!/usr/bin/env python3
"""Launch the tick relay under the Manifold runtime.

The relay needs the ENGINE python (broker libraries) and the AlphaLab
home — both recorded at engine install in ~/.alphalab/engine.json.
This launcher reads that record and execs the real process, so the
manifest can declare a plain `python3` command that works anywhere the
engine is installed. Without an engine record it exits quietly: a desk
without the full engine simply has no tape."""

import json
import os
import sys

RECORD = os.path.expanduser("~/.alphalab/engine.json")


def main():
    try:
        with open(RECORD, encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, json.JSONDecodeError):
        print("no engine record — tick relay not started", flush=True)
        return
    python = record.get("python")
    package = record.get("package_src")
    home = record.get("home")
    if not python or not os.path.isfile(python):
        print("engine python missing — tick relay not started", flush=True)
        return
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(package or "")
    environment["ALPHALAB_HOME"] = str(home or "")
    relay = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "tick_relay.py")
    os.execve(python, [python, relay], environment)


if __name__ == "__main__":
    main()
