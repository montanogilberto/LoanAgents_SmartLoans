"""Scoring for the ticket extraction eval. Pure functions — no Gemini, no I/O.

`score_ticket` compares one predicted result to a hand-written ground truth. It
is called twice per ticket: on the model's own JSON ("raw") and on our final API
response ("final"), so a gap between the two is attributable to our
post-processing and not to the model.

A field is only scored when the expected file contains it, so a ground truth
can leave out anything an annotator can't judge.
"""
from agents.ticket_extraction.matching import (
    MATCH_THRESHOLD, _product_tokens, _score, _supplier_score, normalize_supplier,
)
from agents.ticket_extraction.reconcile import reconcile

MONEY_TOLERANCE = 0.01
# How alike a predicted line's name must be to an expected line's to count as
# "the same line" (recall) — looser than production matching on purpose.
LINE_NAME_THRESHOLD = 0.6

EXACT_FIELDS = ("isPurchaseTicket", "ticketDate", "paymentType")
MONEY_FIELDS = ("total", "subtotal", "tax")
FIELD_ORDER = ("isPurchaseTicket", "merchantName", "ticketDate", "total", "subtotal", "tax", "paymentType")


def _num(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _merchant_ok(expected: str, got: str) -> bool:
    exp, pred = normalize_supplier(expected), normalize_supplier(got)
    if not exp and not pred:
        return True
    return _supplier_score(exp, pred) >= MATCH_THRESHOLD


def _line_similarity(a: str, b: str) -> float:
    (sa, ta), (sb, tb) = _product_tokens(a), _product_tokens(b)
    return _score(ta + sorted(sa), tb + sorted(sb))


def _score_lines(expected_lines: list[dict], predicted_lines: list[dict]) -> dict:
    pairs = sorted(
        (
            (_line_similarity(str(e.get("name", "")), str(p.get("name", ""))), ei, pi)
            for ei, e in enumerate(expected_lines) for pi, p in enumerate(predicted_lines)
        ),
        reverse=True,
    )
    used_e: set[int] = set()
    used_p: set[int] = set()
    matched: dict[int, int] = {}
    for sim, ei, pi in pairs:
        if sim >= LINE_NAME_THRESHOLD and ei not in used_e and pi not in used_p:
            used_e.add(ei)
            used_p.add(pi)
            matched[ei] = pi

    details, qty_scored, qty_ok, total_scored, total_ok = [], 0, 0, 0, 0
    for ei, e in enumerate(expected_lines):
        pi = matched.get(ei)
        row = {"expectedName": e.get("name", ""), "detected": pi is not None}
        if pi is not None:
            p = predicted_lines[pi]
            row["gotName"] = p.get("name", "")
            if "quantity" in e:
                qty_scored += 1
                row["quantityOk"] = abs(_num(e["quantity"]) - _num(p.get("quantity"))) < 0.001
                qty_ok += row["quantityOk"]
                if not row["quantityOk"]:
                    row["quantity"] = {"expected": e["quantity"], "got": p.get("quantity")}
            if "lineTotal" in e:
                total_scored += 1
                row["lineTotalOk"] = abs(_num(e["lineTotal"]) - _num(p.get("lineTotal"))) <= MONEY_TOLERANCE
                total_ok += row["lineTotalOk"]
                if not row["lineTotalOk"]:
                    row["lineTotal"] = {"expected": e["lineTotal"], "got": p.get("lineTotal")}
        details.append(row)

    return {
        "expected": len(expected_lines),
        "detected": len(matched),
        "extra": len(predicted_lines) - len(matched),
        "extraNames": [str(p.get("name", "")) for pi, p in enumerate(predicted_lines) if pi not in used_p],
        "quantityScored": qty_scored, "quantityCorrect": qty_ok,
        "lineTotalScored": total_scored, "lineTotalCorrect": total_ok,
        "details": details,
    }


def _expected_arithmetic(expected: dict) -> bool | None:
    if "arithmeticConsistent" in expected:
        return bool(expected["arithmeticConsistent"])
    if not expected.get("lineItems") or not _num(expected.get("total")):
        return None
    return reconcile({"isPurchaseTicket": True, **expected})["totalMatchesItems"]


def score_ticket(expected: dict, predicted: dict) -> dict:
    """Returns {"fields": {...}, "lines": {...} | None, "arithmetic": {...} | None}."""
    fields: dict[str, dict] = {}
    for name in FIELD_ORDER:
        if expected.get(name) is None:
            continue
        want, got = expected[name], predicted.get(name)
        if name == "merchantName":
            ok = _merchant_ok(str(want), str(got or ""))
        elif name in MONEY_FIELDS:
            ok = abs(_num(want) - _num(got)) <= MONEY_TOLERANCE
        else:
            ok = want == got
        fields[name] = {"expected": want, "got": got, "correct": bool(ok)}

    lines = None
    if isinstance(expected.get("lineItems"), list):
        pred_lines = [i for i in predicted.get("lineItems", []) if isinstance(i, dict)]
        lines = _score_lines(expected["lineItems"], pred_lines)

    arithmetic = None
    want_arith = _expected_arithmetic(expected)
    if want_arith is not None:
        got_arith = reconcile(predicted)["totalMatchesItems"] if predicted else None
        arithmetic = {"expected": want_arith, "got": got_arith, "correct": want_arith == got_arith}

    return {"fields": fields, "lines": lines, "arithmetic": arithmetic}


def summarize(scores: list[dict]) -> dict:
    per_field: dict[str, dict] = {}
    lines = {k: 0 for k in ("expected", "detected", "extra", "quantityScored", "quantityCorrect",
                            "lineTotalScored", "lineTotalCorrect")}
    arithmetic = {"correct": 0, "total": 0}
    for sc in scores:
        for name, f in sc["fields"].items():
            agg = per_field.setdefault(name, {"correct": 0, "total": 0})
            agg["total"] += 1
            agg["correct"] += f["correct"]
        if sc["lines"]:
            for k in lines:
                lines[k] += sc["lines"][k]
        if sc["arithmetic"]:
            arithmetic["total"] += 1
            arithmetic["correct"] += sc["arithmetic"]["correct"]
    return {"tickets": len(scores), "fields": per_field, "lines": lines, "arithmetic": arithmetic}


def diagnose(raw_score: dict, final_score: dict) -> list[str]:
    """One line per field that is wrong in the final result, saying whether the
    model already had it wrong or our post-processing broke a correct value."""
    out = []
    for name, f in final_score["fields"].items():
        if f["correct"]:
            continue
        raw_ok = raw_score["fields"].get(name, {}).get("correct")
        cause = "post-processing changed a correct model value" if raw_ok else "model output already wrong"
        out.append(f"{name}: expected {f['expected']!r}, got {f['got']!r} ({cause})")
    lines = final_score["lines"]
    if lines:
        for row in lines["details"]:
            if not row["detected"]:
                out.append(f"line missing: {row['expectedName']!r}")
            for key in ("quantity", "lineTotal"):
                if key in row:
                    out.append(f"line {row['expectedName']!r} {key}: expected {row[key]['expected']!r}, got {row[key]['got']!r}")
        for name in lines["extraNames"]:
            out.append(f"extra line not in ground truth: {name!r}")
    arith = final_score["arithmetic"]
    if arith and not arith["correct"]:
        out.append(f"arithmetic check: expected totalMatchesItems={arith['expected']}, got {arith['got']}")
    return out


_LABELS = {
    "isPurchaseTicket": "Is purchase ticket", "merchantName": "Merchant", "ticketDate": "Date",
    "total": "Total", "subtotal": "Subtotal", "tax": "Tax", "paymentType": "Payment type",
}


def _ratio(c: int, n: int) -> str:
    return f"{c}/{n}" if n else "-"


def format_report(raw: dict, final: dict, failures: dict[str, list[str]], errored: list[str]) -> str:
    """raw/final are summarize() outputs over the same tickets."""
    rows: list[tuple[str, str, str]] = []
    for name in FIELD_ORDER:
        if name in final["fields"] or name in raw["fields"]:
            r, f = raw["fields"].get(name, {"correct": 0, "total": 0}), final["fields"].get(name, {"correct": 0, "total": 0})
            rows.append((_LABELS[name], _ratio(r["correct"], r["total"]), _ratio(f["correct"], f["total"])))
    rl, fl = raw["lines"], final["lines"]
    rows += [
        ("Lines detected", _ratio(rl["detected"], rl["expected"]), _ratio(fl["detected"], fl["expected"])),
        ("Extra lines (not in truth)", str(rl["extra"]), str(fl["extra"])),
        ("Line quantity", _ratio(rl["quantityCorrect"], rl["quantityScored"]), _ratio(fl["quantityCorrect"], fl["quantityScored"])),
        ("Line total", _ratio(rl["lineTotalCorrect"], rl["lineTotalScored"]), _ratio(fl["lineTotalCorrect"], fl["lineTotalScored"])),
        ("Arithmetic validation", _ratio(raw["arithmetic"]["correct"], raw["arithmetic"]["total"]),
         _ratio(final["arithmetic"]["correct"], final["arithmetic"]["total"])),
    ]
    out = [f"Tickets evaluated: {final['tickets']}" + (f"   (errored, excluded: {', '.join(errored)})" if errored else ""), ""]
    out.append(f"{'':28}{'raw model':>12}{'final':>12}")
    out += [f"{label:28}{r:>12}{f:>12}" for label, r, f in rows]
    if any(failures.values()):
        out += ["", "Failures (final result):"]
        for stem, items in failures.items():
            if items:
                out.append(f"  {stem}")
                out += [f"    - {line}" for line in items]
    return "\n".join(out)
