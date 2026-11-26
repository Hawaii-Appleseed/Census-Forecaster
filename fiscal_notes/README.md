# fiscal_notes: bill text to policy spec

Shadow fiscal notes tool. The translator turns the text of a Hawaii bill draft into the
model's `IncomeTaxSpec` (see `TAX_SIMULATOR_SCOPE.md`, "The policy spec"): an LLM reads the bill,
and deterministic checks around the call refuse anything the bill does not support. The scorer
then runs that spec through the simulator and writes a one-page draft note (`score.py`, `note.py`),
and `diff` compares two drafts of a bill (`diff.py`).

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
python -m fiscal_notes score SPEC.yaml [--translation FILE] [--out DIR] [--population DIR]
python -m fiscal_notes run BILL.txt [--out DIR] [--model sonnet] [--population DIR]
python -m fiscal_notes diff OLD NEW [--out FILE.md]
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

### Scoring and the note

`score` scores the spec with the library calls `analyses/forecast_custom.py --spec` makes
(`load_spec`, `Population.load(runs/tax_simulator)`, `score_spec`; no model logic is duplicated) and
writes, beside the spec by default, `<name>.note.md` and `<name>.score.json`. The translation JSON
defaults to `<name>.translation.json` beside the spec. The score JSON is the full numeric result
with sorted keys (revenue by tax year for LOW, MID, HIGH after behavioral response and MID static;
credits when the spec changes them; the total; five-year sums; the quintile and income-class tables
for the first year the spec applies; provenance), so a later draft's file diffs line by line against
it. The simulator population (`runs/tax_simulator/population.npz`) is a local, uncommitted input: no
test needs it (they use canned score results), and `score` stops with a message when it is missing.
A spec that changes credits needs the credits table: `runs/tax_simulator/credits.json` if the
population build wrote one, else the same table built from `runs/working_family_credits/`
(`tax_modeler.simulator.credits.load_credit_table`, what the build script calls); with neither, it
stops with a message.

`run` is `translate`, then `score` when the verdict is `full` or `partial`, then the note. For
`out_of_scope` it writes the translation JSON and a three-line note (nothing can be scored; the
provisions found) and scores nothing. For `invalid` it writes the translation JSON, prints the
problems and exits 1. `score` refuses an `invalid` translation too.

The note (`note.py`, a pure function of the translation JSON and the score result) has, in order: a
title and a DRAFT status line with the verdict in words; the bottom line (MID five-year change vs
current law, Act 24, and the LOW-HIGH range, in $ millions); the revenue table by tax year; who pays
and who gains; what is scored (section, summary, verbatim quote); what is NOT scored (the estimate
leaves these out); the translator's reviewer notes; method and provenance (model commit, sha256 of
the bill text, translator model). A positive figure means more revenue.

Sample: `examples/hb2306_hd1_2026.note.md` is the note for HB 2306 HD1, with the spec, translation and
score JSON it came from (`hb2306_hd1_2026.{spec.yaml,translation.json,score.json}`). The translation
is the recorded model response in `tests/fiscal_notes/responses/`, not a fresh call. Its bottom line:

> Against current law (Act 24), the bill as scored raises $1,572.5 million over tax years 2027-2031 in
> the MID scenario, after behavioral response. Across the LOW to HIGH scenarios the five-year change
> runs from a gain of $1,534.6 million to a gain of $1,605.0 million. Of the MID figure, income tax is
> +1,879.3 and credits are -306.8 ($ millions). The estimate leaves out the provisions listed under
> "What is NOT scored".

The note describes the spec file that was scored, but its provision list comes from the
translation JSON: if a person edits the spec after translation, the note's "What is scored" no
longer matches it. The spec's sha256 is in the provenance lines so the edit is visible.

`diff OLD NEW` takes the directory-and-name prefix (or the `.score.json` path) of two drafts of the
same bill and prints a Markdown comparison: spec settings that changed, bracket rows that changed
(by filing status and vintage, matched by floor), provisions added, dropped or changed (matched by
section number and spec keys; when the match is not one to one both sides are listed), and the change
in the revenue table (MID by year and five-year, plus the LOW and HIGH five-year range).

Checking the scorer by hand (not a test, needs the population): score a spec both ways and compare.
`python -m fiscal_notes score X.spec.yaml` and `python analyses/forecast_custom.py --spec X.spec.yaml`
must print the same revenue, credits and distribution figures; a translation of current law (SB 3125
CD2, the enrolled text) must score exactly zero in every year and scenario.

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
  capital gains rate (`7.25%` or `7.25 per cent`) that is not written in the bill text the bill
  leaves in force ([bracketed] repealed material does not count);
- a modeled provision citing a spec key that is not there, and a spec key (`income_tax.*`,
  `credits.*`) that no modeled provision claims.

Do not catch:

- a real figure from the bill put in the wrong place (a floor from the head-of-household table in
  the single schedule, the wrong tax year);
- a provision the model left out, or a summary that misdescribes its quote;
- a figure that occurs in the bill for some unrelated reason.

So a person reads every spec against the bill before it is scored or cited.
`tests/fiscal_notes/test_fiscal_notes_translator.py` shows each check failing on a tampered copy of
a recorded answer.
