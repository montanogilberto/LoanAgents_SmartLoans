# Ticket extraction eval

Measures how well `ticket_extraction_agent` reads real tickets, against hand-written ground truth.

```
ticket_eval/
├── tickets/    ticket_001.jpg ...      real photos/screenshots (git-ignored: may show card digits)
├── expected/   ticket_001.json ...     ground truth, same file name (committed)
└── results/    <timestamp>/            one folder per run (git-ignored)
```

## Add a ticket
1. Drop the image in `tickets/` (jpg, png, webp).
2. Write `expected/<same name>.json` **from the ticket itself, never from the agent's output**. See `expected/_example.json` (files starting with `_` are ignored).

Every key is optional — a field is only scored if you include it, so leave out anything you can't judge:

| key | write it as |
|---|---|
| `isPurchaseTicket` | `true` / `false` |
| `merchantName` | store name as a person would say it (matched leniently: accents, "SA de CV", "México" suffixes) |
| `ticketDate` | `YYYY-MM-DD` (Mexican tickets are day-first) |
| `total`, `subtotal`, `tax` | numbers, ±$0.01 |
| `paymentType` | `EFECTIVO`, `TARJETA_CREDITO`, `TARJETA_DEBITO`, `TARJETA`, `TRANSFERENCIA`, `CHEQUE`, `OTRO`, or `NO_VISIBLE` when the ticket shows no payment info |
| `lineItems` | `[{"name", "quantity", "lineTotal"}]` — `quantity`/`lineTotal` optional per line; list every purchased line, no tax/total/change rows |
| `arithmeticConsistent` | only to override: `false` when the visible lines legitimately don't add up to the total (e.g. a cropped screenshot). Otherwise it's computed from your `lineItems` + `total` |

Use `NO_VISIBLE` / omit rather than guessing: a cropped ticket with no payment info should say `"paymentType": "NO_VISIBLE"`.

## Run
Needs `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) in `.env`. Calls the real model.

```bash
python -m ticket_eval.run_eval                     # every labelled ticket
python -m ticket_eval.run_eval --only ticket_003   # one ticket
```

## Read the report
Two columns for every metric:
- **raw model** — the model's own JSON, before any of our code touches it
- **final** — what `/expenses/extract-ticket` returns (date validation, payment label, etc.)

When a ticket is wrong in *final*, the failure list says which side broke it:
`model output already wrong` (it didn't see it, or misread it) vs `post-processing changed a correct model value` (our validator/normalizer). If neither side looks wrong, suspect the ground truth.

"Extra lines" are predicted lines that match nothing in your ground truth (hallucinated or duplicated rows). "Arithmetic validation" checks that our reconcile logic reaches the same verdict as the truth about whether the lines add up to the total.

## Fix a ground-truth mistake without re-calling Gemini
Edit `expected/<name>.json`, then:

```bash
python -m ticket_eval.run_eval --rescore ticket_eval/results/<timestamp>
```

Each run folder holds, per ticket: `*.raw.json` (model output as received + parsed), `*.final.json`, `*.score.json`, plus `run.json` (model, prompt hash) so runs are comparable after a prompt change, `summary.json` and `report.txt`.

## Tickets worth collecting
Sam's Club / online order screenshot · supermarket · small local supplier · restaurant/food supplier · hardware/maintenance · cash · debit and credit card · long ticket with many lines · a ticket with a discount · tax clearly shown · a blurry or cropped one.

Not covered yet: supplier/product *matching* accuracy (needs a snapshot of a company's supplier and product lists) and image-quality failure rates.
