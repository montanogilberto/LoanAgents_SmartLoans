"""
Tests for the ticket eval scoring and run/rescore flow. No Gemini: the scorer
is pure and the runner is exercised through --rescore on saved run files.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ticket_eval import run_eval
from ticket_eval.scoring import diagnose, format_report, score_ticket, summarize

EXPECTED = {
    "isPurchaseTicket": True,
    "merchantName": "Sam's Club",
    "ticketDate": "2026-10-01",
    "total": 935.95,
    "paymentType": "EFECTIVO",
    "lineItems": [
        {"name": "Detergente Líquido Ariel RevitaColor 8.5 L", "quantity": 1, "lineTotal": 339.99},
        {"name": "Jabón Líquido para Manos Member's Mark 5 L", "quantity": 1, "lineTotal": 166.00},
        {"name": "Suavizante de Telas Suavitel 8.5 L", "quantity": 2, "lineTotal": 429.96},
    ],
}


def _predicted(**overrides):
    base = {
        "isPurchaseTicket": True, "merchantName": "SAMS CLUB MEXICO", "ticketDate": "2026-10-01",
        "total": 935.95, "paymentType": "EFECTIVO", "subtotal": 0.0, "tax": 0.0,
        "lineItems": [
            {"name": "Suavizante de Telas Suavitel 8.5 L", "quantity": 2, "unitPrice": 0, "lineTotal": 429.96},
            {"name": "Detergente Líquido Ariel RevitaColor 8.5 L", "quantity": 1, "unitPrice": 0, "lineTotal": 339.99},
            {"name": "Jabón Líquido para Manos Member's Mark 5 L", "quantity": 1, "unitPrice": 0, "lineTotal": 166.00},
        ],
    }
    base.update(overrides)
    return base


def test_perfect_prediction_scores_everything_correct():
    sc = score_ticket(EXPECTED, _predicted())
    assert all(f["correct"] for f in sc["fields"].values())
    assert set(sc["fields"]) == {"isPurchaseTicket", "merchantName", "ticketDate", "total", "paymentType"}
    assert (sc["lines"]["expected"], sc["lines"]["detected"], sc["lines"]["extra"]) == (3, 3, 0)
    assert (sc["lines"]["quantityCorrect"], sc["lines"]["lineTotalCorrect"]) == (3, 3)
    assert sc["arithmetic"]["correct"] is True


def test_only_fields_in_ground_truth_are_scored():
    sc = score_ticket({"total": 100.0}, _predicted(total=100.004))
    assert list(sc["fields"]) == ["total"] and sc["fields"]["total"]["correct"]
    assert sc["lines"] is None and sc["arithmetic"] is None


def test_wrong_values_are_flagged():
    sc = score_ticket(EXPECTED, _predicted(total=953.95, ticketDate="2026-01-10", paymentType="NO_VISIBLE"))
    assert {n for n, f in sc["fields"].items() if not f["correct"]} == {"total", "ticketDate", "paymentType"}


def test_lines_missing_extra_and_wrong_numbers():
    pred = _predicted(lineItems=[
        {"name": "Detergente Líquido Ariel RevitaColor 8.5 L", "quantity": 2, "lineTotal": 339.99},
        {"name": "Suavizante de Telas Suavitel 8.5 L", "quantity": 2, "lineTotal": 492.96},
        {"name": "Bolsa reutilizable", "quantity": 1, "lineTotal": 5.0},
    ])
    lines = score_ticket(EXPECTED, pred)["lines"]
    assert (lines["detected"], lines["extra"]) == (2, 1)
    assert lines["extraNames"] == ["Bolsa reutilizable"]
    assert (lines["quantityScored"], lines["quantityCorrect"]) == (2, 1)
    assert (lines["lineTotalScored"], lines["lineTotalCorrect"]) == (2, 1)
    assert [r["detected"] for r in lines["details"]] == [True, False, True]


def test_arithmetic_verdict_compared_with_truth():
    # Cropped screenshot: the visible lines legitimately don't reach the total.
    cropped_truth = {**EXPECTED, "total": 1701.93}
    sc = score_ticket(cropped_truth, _predicted(total=1701.93))
    assert sc["arithmetic"] == {"expected": False, "got": False, "correct": True}
    # Explicit override beats the computed value.
    sc = score_ticket({**cropped_truth, "arithmeticConsistent": True}, _predicted(total=1701.93))
    assert sc["arithmetic"]["correct"] is False


def test_empty_prediction_scores_wrong_not_crashes():
    sc = score_ticket(EXPECTED, {})
    assert not any(f["correct"] for f in sc["fields"].values())
    assert sc["lines"]["detected"] == 0 and sc["arithmetic"]["correct"] is False


def test_diagnose_blames_model_vs_post_processing():
    raw = score_ticket(EXPECTED, _predicted(total=999.0))              # model wrong
    final = score_ticket(EXPECTED, _predicted(total=999.0, ticketDate=""))  # + date blanked by us
    raw_date_ok = score_ticket(EXPECTED, _predicted())
    msgs = diagnose(raw, final)
    assert any("total" in m and "model output already wrong" in m for m in msgs)
    msgs = diagnose(raw_date_ok, final)
    assert any("ticketDate" in m and "post-processing" in m for m in msgs)


def test_summarize_and_report():
    good, bad = score_ticket(EXPECTED, _predicted()), score_ticket(EXPECTED, _predicted(total=1.0))
    summary = summarize([good, bad])
    assert summary["tickets"] == 2 and summary["fields"]["total"] == {"correct": 1, "total": 2}
    report = format_report(summary, summary, {"t1": ["total: expected 935.95, got 1.0 (model output already wrong)"], "t2": []}, ["t9"])
    assert "Tickets evaluated: 2" in report and "1/2" in report and "t9" in report and "t1" in report


def test_find_tickets_ignores_templates_and_unlabelled(tmp_path):
    tickets, expected = tmp_path / "t", tmp_path / "e"
    tickets.mkdir(); expected.mkdir()
    for name in ("a.jpg", "b.png", "_skip.jpg", "notes.txt"):
        (tickets / name).write_bytes(b"x")
    (expected / "a.json").write_text("{}")
    labelled, unlabelled = run_eval._find_tickets(tickets, expected, None)
    assert [p.name for p in labelled] == ["a.jpg"] and unlabelled == ["b"]


def test_rescore_reads_saved_run(tmp_path, capsys):
    from main import _build_ticket_response
    run, expected = tmp_path / "run", tmp_path / "expected"
    run.mkdir(); expected.mkdir()
    pred = _predicted(ticketDate="2026-10-01")
    (expected / "t1.json").write_text(json.dumps(EXPECTED))
    (run / "t1.raw.json").write_text(json.dumps({"raw": json.dumps(pred), "parsed": pred}))
    (run / "t1.final.json").write_text(json.dumps(_build_ticket_response(pred).model_dump()))

    run_eval._rescore(argparse.Namespace(rescore=str(run), expected=str(expected)))
    out = capsys.readouterr().out
    assert "Tickets evaluated: 1" in out
    assert (run / "summary.json").exists() and (run / "t1.score.json").exists() and (run / "report.txt").exists()
