# AlphaLab

A market research desk — a harness for the
[Manifold](https://github.com/itsjustmax/manifold) runtime.

```bash
manifold install itsjustmax/alphalab
manifold open alphalab --web
```

Works instantly with public end-of-day data; broker quotes, options
chains, and dealer gamma light up when the full engine stack is
installed (`capabilities` tells you what's live). Ships its own desk UI
(cards, D3 charts) and its own rules — see FRAMING.md and DESK.md.
Paper-only by structure: the tool grant contains no order route.

## Installing

```bash
manifold install itsjustmax/alphalab
manifold open alphalab
```

The desk works immediately on public end-of-day data (charts, summaries,
cases, the paper gates' validation lane). To light the full engine —
broker quotes, live tick streams, options chains, dealer gamma:

```bash
python3 tools/install_engine.py
```

That installs the AlphalabAgents engine, binds this machine
(`~/.alphalab/engine.json`), and names the two pieces only you can do:
start Timescale (`./alphalab db plan` in the engine package) and log in
to Trader Workstation. Every tool answers honestly at whatever tier is
live — `capabilities` tells you where you stand.
