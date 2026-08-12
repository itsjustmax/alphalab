#!/usr/bin/env python3
"""Light up AlphaLab's full engine on this machine.

The harness works out of the box on public end-of-day data. This
installer adds the full lane — broker quotes, live tick streams, options
chains, dealer gamma — by installing the AlphalabAgents engine and
binding the harness to it:

  1. clones the engine repository (or reuses --source),
  2. runs its packaged installer (venv + package + AlphaLab home),
  3. writes the machine binding to ~/.alphalab/engine.json,
  4. probes the lanes and names exactly what still needs you
     (Timescale via the engine's own `alphalab db` tooling; Trader
     Workstation login for broker data).

Idempotent: re-running updates in place.

  python3 tools/install_engine.py
  python3 tools/install_engine.py --dest ~/Alphalab/AlphalabAgents \
      --home ~/Alphalab/alphalab
"""

import argparse
import json
import os
import subprocess
import sys

DEFAULT_REPO = "https://github.com/itsjustmax/AlphalabAgents.git"


def run(command, **kwargs):
    print("  $", " ".join(str(part) for part in command), flush=True)
    return subprocess.run([str(part) for part in command], **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", default=DEFAULT_REPO,
                        help="git URL or local path of the engine repository")
    parser.add_argument("--dest", default=os.path.expanduser(
        "~/Alphalab/AlphalabAgents"), help="where the engine checkout lives")
    parser.add_argument("--home", default=os.path.expanduser(
        "~/Alphalab/alphalab"), help="the AlphaLab home (credentials, cache)")
    parser.add_argument("--config", default=None,
                        help="binding file path (default ~/.alphalab/engine.json)")
    parser.add_argument("--python", default=sys.executable,
                        help="python used to build the engine venv")
    arguments = parser.parse_args()

    dest = os.path.abspath(os.path.expanduser(arguments.dest))
    home = os.path.abspath(os.path.expanduser(arguments.home))
    config_path = os.path.abspath(os.path.expanduser(
        arguments.config
        or os.environ.get("ALPHALAB_ENGINE_CONFIG")
        or "~/.alphalab/engine.json"))

    print(f"engine checkout: {dest}")
    if os.path.isdir(os.path.join(dest, ".git")):
        run(["git", "-C", dest, "pull", "--ff-only"], check=False)
    elif os.path.isdir(arguments.source):
        run(["git", "clone", os.path.abspath(arguments.source), dest],
            check=True)
    else:
        run(["git", "clone", arguments.source, dest], check=True)

    package = os.path.join(dest, "packages", "alphalab-agents")
    if not os.path.isdir(package):
        print(f"error: {package} is missing — not an AlphalabAgents checkout")
        return 2
    print("running the engine's packaged installer (venv + home)…")
    installed = run(
        ["bash", os.path.join(package, "install.sh"), "--home", home],
        cwd=package,
        env={**os.environ, "PYTHON": arguments.python},
    )
    if installed.returncode != 0:
        print("error: the engine installer failed — its output above names why")
        return installed.returncode

    binding = {
        "python": os.path.join(package, ".venv", "bin", "python"),
        "package_src": os.path.join(package, "src"),
        "home": home,
    }
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as handle:
        json.dump(binding, handle, indent=1)
    print(f"binding written: {config_path}")

    print("probing the lanes…")
    harness = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for tool, payload in (("capabilities", "{}"), ("system_health", "{}")):
        probe = subprocess.run(
            [sys.executable, os.path.join(harness, "tools", "run.py"), tool],
            input=payload, capture_output=True, text=True,
            env={**os.environ, "ALPHALAB_ENGINE_CONFIG": config_path},
        )
        try:
            receipt = json.loads(probe.stdout)
            print(f"  {tool}: {'ok' if receipt.get('ok') else 'degraded'} — "
                  f"{str(receipt.get('summary') or receipt.get('error'))[:110]}")
        except (json.JSONDecodeError, TypeError):
            print(f"  {tool}: no receipt — {probe.stderr.strip()[:110]}")

    print("\nwhat still needs you (each is the engine's own tooling):")
    print(f"  1. Timescale:  cd {package} && ./alphalab db plan")
    print("  2. Broker data: open Trader Workstation and log in")
    print("  3. Re-check:    ask the desk to run capabilities")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
