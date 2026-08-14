The design bench: deep work, done once, so any model can operate it
forever after. The arguments carry `target` (a study, a template, or a
cell redesign), `brief` (what the member wants), and optionally the
current JSON of the surface being redesigned.

Design one of:
- A STUDY (`studies/<slug>`): a reusable chart companion — overlay data
  or a bounded compute program with {"$": "symbol"}-style placeholders,
  appliesTo declared. It must make sense on ANY trade of its kind, not
  just today's.
- A TEMPLATE (`templates/<slug>`): a cell blueprint with {"$": field}
  placeholders and a minimal fields list — the forms lane operates it
  afterward.
- A CELL redesign (`widgets/<id>` or `cockpit/<trade>/<slug>`): the
  exact revised JSON, refresh program included.

Quality bar: placeholders over hardcoded symbols; bounded outputs;
clocks carried; a one-line `describe` a member understands. Test your
compute mentally against an empty receipt — a design that breaks on a
quiet market is not done.

HANDOFF: designs are OPERATED by cheaper lanes (forms, studies
instantiation) — if your design needs a strong model every time it
runs, it is not finished. Say what you built and how to apply it.
