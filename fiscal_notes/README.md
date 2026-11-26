# fiscal_notes: bill text to policy spec

First piece of the shadow fiscal notes tool. It turns the text of a Hawaii bill draft into the
model's `IncomeTaxSpec` (see `TAX_SIMULATOR_SCOPE.md`, "The policy spec"): an LLM reads the bill,
and deterministic checks around the call refuse anything the bill does not support.

This directory sits outside every workspace package and every `common.fingerprint.SCRIPT_DIRS`
directory on purpose: the cache key hashes those, so nothing here can move a published number or a
cache. Do not move it into one. It imports `tax_modeler`; nothing imports it.

## Input

A bill draft as plain text: three header lines, a blank line, then sections separated by blank lines,
each starting `SECTION n.`. Text in [square brackets] is repealed material.

```
BILL: HB2306 HD1 (2026 session)
TITLE: RELATING TO INCOME TAX.
DESCRIPTION: ...

SECTION 1. ...
```

Fixtures: `tests/fiscal_notes/bills/`.

## Commands (from the repo root)

```
python -m fiscal_notes translate BILL.txt [--out DIR] [--model sonnet]
python -m fiscal_notes eval [--record] [--model sonnet]
```

`translate` writes `<name>.spec.yaml` (when there is a spec; a header comment says it is
machine-translated and unreviewed) and `<name>.translation.json` (verdict, provisions, notes,
problems, model, attempts, sha256 of the bill and of `prompt.md`) to `reforms/bills/<session>/`
by default. `<name>` comes from the header: `hb2306_hd1_2026`. Exit code 0 for full, partial and
out_of_scope; 1 for invalid.

`eval` runs the real model over the fixture bills and prints, per bill, the verdict and whether each
expectation in `expectations.py` holds (the same expectations the offline tests use). `--record`
rewrites `tests/fiscal_notes/responses/`, which the tests replay; re-record after any change to
`prompt.md`. The model is called through the `claude` CLI (`CLAUDE_BIN` overrides the binary); no
test calls it.

## Verdicts

Derived by code from the checks, never taken from the model.

- `invalid`: any check failed. `translate` retries once with the problems appended; still failing, it
  reports `invalid` and the problems.
- `out_of_scope`: the spec is null or no provision is modeled (for example, amounts indexed by a
  factor the bill does not state).
- `partial`: a spec, but some provision is not modeled or has a blank amount (for example, a credit
  the spec cannot express, or a sunset past 2031).
- `full`: every provision is modeled and none is blank. Rare; read it with suspicion.

## What the checks do and do not catch

Catch:

- a spec the model's own validator rejects (unknown keys, bad schedules, wrong first year);
- a quote that is not verbatim in the bill (whitespace collapsed on both sides);
- a bracket floor (`$9,600`), bracket rate (`3.20%`), standard deduction, personal exemption or
  capital gains rate (`7.25%` or `7.25 per cent`) that is not written in the bill text;
- a modeled provision citing a spec key that is not there, and a spec key (`income_tax.*`,
  `credits.*`) that no modeled provision claims.

Do not catch:

- a real figure from the bill put in the wrong place (a floor from the head-of-household table in
  the single schedule, a figure from a repealed [bracketed] schedule, the wrong tax year);
- a provision the model left out, or a summary that misdescribes its quote;
- a figure that occurs in the bill for some unrelated reason.

So a person reads every spec against the bill before it is scored or cited.
`tests/fiscal_notes/test_fiscal_notes_translator.py` shows each check failing on a tampered copy of
a recorded answer.
