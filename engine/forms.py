"""Forms: minimal fields in, finished cells out.

The design insight this encodes: agents should not hand-assemble cell
structure. A strong model designs a template once; during market hours
an affordable model fills a small form, and the desk expands it into
the exact cell — normalized, validated, honest. Violations come back on
the form itself, named and teachable.

Two lanes:

  forms/trade/<id>   {"contracts", "thesis", "invalidation", ...}
                     → trades/<id>, normalized through the gate rulebook
  forms/<id>         {"template": <name>, ...fields}
                     → the template's target key, placeholders filled

Templates live in BUILTIN_TEMPLATES (harness bytes, designed with a
strong model) and may be extended per desk via templates/<name> context
entries. A template is {"target", "fields", "cell"} where the cell's
{"$": "field"} nodes and "{field}" strings are filled from the form.
"""

import re

import gates

BUILTIN_TEMPLATES = {
    "live-chart": {
        "describe": "a streaming-refreshed candle chart for one symbol",
        "target": "widgets/chart-{id}",
        "fields": {"id": "short slug", "symbol": "ticker",
                   "days": "trading days (optional, default 60)",
                   "title": "optional card title"},
        "cell": {"kind": "candle", "title": {"$": "title", "default": "{symbol} — daily"},
                 "chart": {"symbol": {"$": "symbol"},
                           "days": {"$": "days", "default": 60}}},
    },
    "live-quote": {
        "describe": "a live streamed quote chip entry for one symbol",
        "target": "quotes/{symbol}",
        "fields": {"symbol": "ticker"},
        "cell": {"refresh": {"tool": "live_quote",
                             "args": {"symbol": {"$": "symbol"}},
                             "minutes": 1,
                             "value_path": "result.data.quote",
                             "into": "quote"}},
    },
    "gamma-metric": {
        "describe": "one SPX dealer-gamma field as a live metric",
        "target": "widgets/gamma-{id}",
        "fields": {"id": "short slug", "field": "gamma_v2 field name",
                   "title": "card title", "label": "optional unit note"},
        "cell": {"kind": "metric", "title": {"$": "title"},
                 "value": "awaiting first receipt",
                 "label": {"$": "label", "default": "{field}"},
                 "refresh": {"tool": "spx_gamma",
                             "args": {"max_age_minutes": 10}, "minutes": 10,
                             "value_path": "result.data.gamma_v2.{field}",
                             "into": "value"}},
    },
    "paper-order": {
        "describe": "a working paper order on one exact option contract",
        "target": "widgets/fill-{trade}",
        "fields": {"trade": "the trades/<id> slug this order enters",
                   "symbol": "underlying ticker",
                   "expiration": "YYYYMMDD", "strike": "strike number",
                   "right": "C or P", "price": "limit price",
                   "quantity": "contracts 1-100",
                   "action": "buy (default) or sell"},
        "cell": {"kind": "order",
                 "title": "Paper order — {symbol} {expiration} {strike}{right}",
                 "refresh": {"tool": "fill_watch",
                             "args": {"symbol": {"$": "symbol"},
                                      "sec_type": "OPT",
                                      "expiration": {"$": "expiration"},
                                      "strike": {"$": "strike"},
                                      "right": {"$": "right"},
                                      "price": {"$": "price"},
                                      "quantity": {"$": "quantity"},
                                      "action": {"$": "action", "default": "buy"},
                                      "contract": "{symbol} {expiration} {strike}{right}"},
                             "minutes": 2, "value_path": "result.data",
                             "into": "check"}},
    },
}


def contract_text(value):
    """Coerce one contract, string or structured, to its canonical string.

    The live desk's first gate violation was exactly this: an agent wrote
    {"symbol": "NVDA", ...} where "NVDA 20260821 235C" belongs.
    """

    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    if isinstance(value, dict):
        symbol = str(value.get("symbol") or "").strip().upper()
        if not symbol:
            return ""
        parts = [symbol]
        expiration = str(value.get("expiration") or "").strip()
        if expiration:
            parts.append(expiration)
        strike = value.get("strike")
        right = str(value.get("right") or "").strip().upper()
        if strike is not None and right:
            try:
                parts.append(f"{float(strike):g}{right}")
            except (TypeError, ValueError):
                parts.append(f"{strike}{right}")
        elif str(value.get("sec_type") or "").upper() in ("STK", "IND", ""):
            parts.append("shares" if not value.get("sec_type")
                         or value.get("sec_type") == "STK" else "index")
        return " ".join(parts)
    return ""


def trade_from_form(fields):
    """(trades/<id> value, violations) from minimal fields.

    Normalizes what can be normalized — a lone contract becomes a list,
    structured contracts become canonical strings — and validates the
    finished trade through the one rulebook.
    """

    if not isinstance(fields, dict):
        return None, ["a trade form is one JSON object"]
    raw_contracts = fields.get("contracts", fields.get("contract"))
    if raw_contracts is None:
        raw_contracts = []
    if isinstance(raw_contracts, (str, dict)):
        raw_contracts = [raw_contracts]
    contracts = [text for text in
                 (contract_text(item) for item in raw_contracts) if text]
    if raw_contracts and not contracts:
        # junk was OFFERED — keep it visible so the gate teaches the
        # exact shape instead of silently passing an empty idea
        contracts = list(raw_contracts)
    trade = {
        "contracts": contracts,
        "thesis": str(fields.get("thesis") or "").strip(),
        "evidence": [str(item) for item in (fields.get("evidence") or [])],
        "invalidation": str(fields.get("invalidation") or "").strip(),
        "state": str(fields.get("state") or "idea").strip().lower(),
        "fill": fields.get("fill"),
        "exit": fields.get("exit"),
    }
    if fields.get("as_of"):
        trade["as_of"] = fields["as_of"]
    return trade, gates.trade_violations(trade)


def _fill(node, fields):
    if isinstance(node, dict):
        if "$" in node:
            name = str(node["$"])
            value = fields.get(name, node.get("default"))
            if isinstance(value, str):
                return _substitute(value, fields)
            return value
        return {key: _fill(item, fields) for key, item in node.items()}
    if isinstance(node, list):
        return [_fill(item, fields) for item in node]
    if isinstance(node, str):
        return _substitute(node, fields)
    return node


def _substitute(text, fields):
    def replace(match):
        return str(fields.get(match.group(1), match.group(0)))
    return re.sub(r"\{(\w+)\}", replace, text)


def expand(form, templates=None):
    """(target_key, cell_value, violations) for one template form."""

    if not isinstance(form, dict):
        return None, None, ["a form is one JSON object"]
    name = str(form.get("template") or "").strip()
    library = dict(BUILTIN_TEMPLATES)
    for key, value in (templates or {}).items():
        if isinstance(value, dict) and value.get("target") and value.get("cell"):
            library[str(key)] = value
    template = library.get(name)
    if template is None:
        return None, None, [
            f"no template named {name!r} — this desk knows: "
            + ", ".join(sorted(library))]
    fields = {key: value for key, value in form.items() if key != "template"}
    missing = [field for field, note in (template.get("fields") or {}).items()
               if "optional" not in str(note).lower()
               and "default" not in str(note).lower()
               and fields.get(field) in (None, "")]
    if missing:
        return None, None, [
            f"template {name!r} still needs: {', '.join(sorted(missing))}"]
    target = _substitute(str(template["target"]), fields)
    cell = _fill(template["cell"], fields)
    return target, cell, []


def form_check(arguments):
    """Tool face: validate/expand a form without writing anything."""

    form = arguments.get("form")
    if isinstance(form, dict) and (form.get("contracts") or form.get("contract")
                                   or form.get("thesis")):
        trade, violations = trade_from_form(form)
        if violations:
            return gates.receipt(
                f"the trade form breaks {len(violations)} rule(s)",
                {"violations": violations, "trade": trade},
                gaps=violations, ok=False)
        return gates.receipt("the trade form expands cleanly",
                             {"trade": trade, "violations": []})
    target, cell, violations = expand(form or {},
                                      arguments.get("templates") or {})
    if violations:
        return gates.receipt(
            f"the form breaks {len(violations)} rule(s)",
            {"violations": violations}, gaps=violations, ok=False)
    return gates.receipt(f"the form expands to {target}",
                         {"target": target, "cell": cell, "violations": []})
