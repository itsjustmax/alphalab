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
