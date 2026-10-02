"""Runs the ticket extraction agent over labelled tickets and scores it.

    python -m ticket_eval.run_eval                      # every ticket with a ground truth
    python -m ticket_eval.run_eval --only ticket_003    # one ticket
    python -m ticket_eval.run_eval --rescore ticket_eval/results/<run>   # no Gemini call

Calls the real model (needs GEMINI_API_KEY / GOOGLE_API_KEY in .env). Per ticket
it saves the model's raw output, our final API result, and the score, so a
failure can be traced to the model, our post-processing, or the ground truth.
"""
import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ticket_eval.scoring import diagnose, format_report, score_ticket, summarize

ROOT = Path(__file__).parent
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _find_tickets(tickets_dir: Path, expected_dir: Path, only: str | None) -> tuple[list[Path], list[str]]:
    """Images that have a ground-truth file (files starting with '_' are templates
    and are ignored), plus the stems of images that don't."""
    images = sorted(p for p in tickets_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES and not p.name.startswith("_"))
    if only:
        images = [p for p in images if p.stem == only]
    labelled = [p for p in images if (expected_dir / f"{p.stem}.json").exists()]
    return labelled, [p.stem for p in images if p not in labelled]


def _score_and_report(run_dir: Path, stems: list[str], expected_dir: Path, errored: list[str]) -> str:
    raw_scores, final_scores, failures = [], [], {}
    for stem in stems:
        expected = _load_json(expected_dir / f"{stem}.json")
        raw_parsed = _load_json(run_dir / f"{stem}.raw.json")["parsed"]
        final = _load_json(run_dir / f"{stem}.final.json")
        raw_sc, final_sc = score_ticket(expected, raw_parsed), score_ticket(expected, final)
        raw_scores.append(raw_sc)
        final_scores.append(final_sc)
        failures[stem] = diagnose(raw_sc, final_sc)
        _write_json(run_dir / f"{stem}.score.json", {"raw": raw_sc, "final": final_sc, "diagnosis": failures[stem]})
    raw_sum, final_sum = summarize(raw_scores), summarize(final_scores)
    _write_json(run_dir / "summary.json", {"raw": raw_sum, "final": final_sum, "errored": errored})
    report = format_report(raw_sum, final_sum, failures, errored)
    (run_dir / "report.txt").write_text(report, encoding="utf-8")
    return report


async def _run(args) -> None:
    import main  # heavy import (FastAPI app + all agents); only needed for live runs
    from agents.ticket_extraction.prompt import INSTRUCTION

    tickets_dir, expected_dir = Path(args.tickets), Path(args.expected)
    images, unlabelled = _find_tickets(tickets_dir, expected_dir, args.only)
    if unlabelled:
        print(f"Skipping {len(unlabelled)} ticket(s) with no expected/<name>.json: {', '.join(unlabelled)}")
    if not images:
        sys.exit(f"No labelled tickets found in {tickets_dir} (need an image plus {expected_dir}/<same name>.json).")

    run_dir = Path(args.out) / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)
    _write_json(run_dir / "run.json", {
        "startedAt": datetime.now().isoformat(timespec="seconds"),
        "model": main.ticket_extraction_agent.model,
        "promptSha1": hashlib.sha1(INSTRUCTION.encode()).hexdigest()[:10],
        "tickets": [p.name for p in images],
    })

    done, errored = [], []
    for image in images:
        print(f"[{len(done) + len(errored) + 1}/{len(images)}] {image.name} ...", flush=True)
        try:
            raw, parsed = await main._extract_ticket_raw(image.read_bytes())
        except Exception as exc:  # one bad call (quota, network) must not sink the run
            errored.append(image.stem)
            (run_dir / f"{image.stem}.error.txt").write_text(repr(exc), encoding="utf-8")
            print(f"    ERROR: {exc!r}")
            continue
        final = main._build_ticket_response(parsed).model_dump()
        _write_json(run_dir / f"{image.stem}.raw.json", {"raw": raw, "parsed": parsed})
        _write_json(run_dir / f"{image.stem}.final.json", final)
        done.append(image.stem)

    print()
    print(_score_and_report(run_dir, done, expected_dir, errored) if done else "Every ticket errored; nothing to score.")
    print(f"\nSaved to {run_dir}")


def _rescore(args) -> None:
    run_dir, expected_dir = Path(args.rescore), Path(args.expected)
    stems = sorted(p.name.removesuffix(".raw.json") for p in run_dir.glob("*.raw.json"))
    stems = [s for s in stems if (expected_dir / f"{s}.json").exists()]
    if not stems:
        sys.exit(f"Nothing to rescore in {run_dir}.")
    errored = sorted(p.name.removesuffix(".error.txt") for p in run_dir.glob("*.error.txt"))
    print(_score_and_report(run_dir, stems, expected_dir, errored))


def main_cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tickets", default=str(ROOT / "tickets"))
    parser.add_argument("--expected", default=str(ROOT / "expected"))
    parser.add_argument("--out", default=str(ROOT / "results"))
    parser.add_argument("--only", help="ticket name without extension, e.g. ticket_003")
    parser.add_argument("--rescore", metavar="RUN_DIR", help="re-score a saved run against the current expected/ files (no Gemini call)")
    args = parser.parse_args()
    if args.rescore:
        _rescore(args)
    else:
        asyncio.run(_run(args))


if __name__ == "__main__":
    main_cli()
