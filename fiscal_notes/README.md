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
python -m fiscal_notes fetch --year 2026 --bill HB2306 [--draft HD1] [--out FILE]
python -m fiscal_notes hub [--hub URL] [--once] [--dry-run] [--limit N] [--model sonnet] [--work-dir DIR]
```

`translate` writes `<name>.spec.yaml` (only for a `full` or `partial` verdict; a header comment says it is
machine-translated and unreviewed; for `out_of_scope` and `invalid` none is written and a stale one of
the same name from an earlier run is removed) and `<name>.translation.json` (verdict, provisions, notes,
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
and who gains; what is scored (section, summary, verbatim quote); scored approximately (only when a
provision is: the same, plus what the spec gets wrong and in which direction); what is NOT scored (the estimate
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
- `out_of_scope`: the spec is null, sets no `income_tax.*` or `credits.*` key (a stub such as
  `{"first_year": 2027}` counts as null), or no provision is modeled (for example, amounts indexed by a
  factor the bill does not state).
- `partial`: a spec, but some provision is not modeled, has a blank amount (for example, a credit
  the spec cannot express, or a sunset past 2031), or is modeled only approximately.
- `full`: every provision is modeled exactly and none is blank. Rare; read it with suspicion.

A provision the spec can only express roughly (HB 1850 HD2 taxes capital gains as ordinary income except
gains on a principal home; the spec has one rate) is still modeled, with `approximate: true` and an
`approximation` text saying what is lost and whether the estimate is too high or too low. `approximate`
is only allowed on a modeled provision and needs non-empty text; an answer without the two fields reads
as not approximate. The note lists these under "Scored approximately" and `diff` shows changes to them.

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

## Fetching a draft, and the staff hub client

`fetch` writes the translator's input for one draft of a bill (header lines, a blank line, sections
separated by blank lines; `--draft` omitted is the introduced draft, labelled `Introduced`), so
`fetch --out BILL.txt` then `run BILL.txt` works by hand. It does not reimplement Legislative-Research-Tool's
(LRT) parser: it runs `fiscal_notes/_lrt_fetch.py` under LRT's own interpreter
(`$FISCAL_NOTES_LRT_ROOT/.venv/bin/python`, default `~/repos/Legislative-Research-Tool`, with
`PYTHONPATH` set to that checkout), which uses `testimony.drafttext.parse` and LRT's paced, cached
client. The URL is built directly, `https://data.capitol.hawaii.gov/sessions/session<year>/bills/<BILL>_<DRAFT>_.HTM`
(introduced: `<BILL>_.HTM`), not from LegiScan's list, which can lag or lack a draft. `data.capitol`,
never `www`, which refuses scripts. A bill carried over the biennium can keep its files in the first
(odd) year's directory: when an even year's URL is a 404 the previous year is tried, and the page must
name the bill or it is refused. The helper's cache entries are `fiscal-notes-draft-<dir year>-<file>`
in LRT's `data/cache/`, so they cannot collide with LRT's own (which are keyed by LegiScan's session).
Not found in either directory, LRT missing, or a page with no sections is an error whose text is what the hub shows.

`hub` is the staff hub client, meant to be run by hand or by a scheduler on a staff Mac (scheduling is
left to the owner: nothing here creates a job, and there is no daemon loop). One run reads the queue
once, oldest request first, and for each: posts `running`, fetches the draft, translates, scores a
`full` or `partial` verdict, renders the note, posts `ready` (the contract's `Result`) or `failed` (the
draft could not be fetched, the translation is `invalid` (its problems, first 2,000 characters), the
translator could not run, or scoring raised). A 409 on `running` means the request was withdrawn
meanwhile: it is skipped. One request failing never stops the next; a hub that cannot be reached, or
that refuses the token, does. Exit 0 when the queue was processed (including requests reported
`failed`), 2 when the hub could not be used. `--dry-run` does everything except the POSTs and prints what
it would post (verdict, headline, counts, sizes); `--limit N` takes the N oldest.

Each request's files are kept in `runs/fiscal_notes/<year>/<name>/` (`CF_RUNS_DIR` or `--work-dir` moves
the root; `runs/` is gitignored): `<name>.txt`, `.spec.yaml`, `.translation.json`, `.score.json`,
`.note.md`, and `posted.json`, the last body sent, so a person can inspect what the hub received.

Credentials, the Cloudflare Access service token, looked up in this order and never printed or logged:
the environment (`CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET`); `~/.config/fiscal-notes/hub.env`
(`KEY=value` lines), only if it exists and is `chmod 600` (otherwise the run stops); the macOS Keychain
items `apps-script:CF_ACCESS_CLIENT_ID` and `apps-script:CF_ACCESS_CLIENT_SECRET`. The default hub is
production; `--hub` points at another. Access answers a bad token with its sign-in page and status 200,
so any non-JSON answer is reported as a refused token.

The contract with the hub (the hub side is built to the same text):

- `GET /api/events-sync/fiscal-notes` returns `{"requests": [{id, bill, year, draft, by, at}]}`, every
  queued request, oldest first. `draft` is `""` for the introduced draft.
- `POST` the same route with `{"id", "status": "running"}`, or `{"id", "status": "ready", "result": Result}`,
  or `{"id", "status": "failed", "detail": "<= 2,000 chars"}`. Answers: 200, 400 bad body, 404 unknown id,
  409 not in an allowed state.
- `Result` is `verdict` (`full`, `partial`, `out_of_scope`), `label`, `markdown` (the note, at most 100,000
  characters; longer is cut with a visible marker line), `headline` (`five_year_mid`, `five_year_low`,
  `five_year_high` in $ millions, positive raises revenue, from the score's five-year totals with
  credits; `first_year`, `last_year`; null for `out_of_scope`), `counts` (`scored`: modeled with an amount
  and exact; `approximate`; `not_scored`: not modeled or the amount is blank), `tool` (`commit`, `model`,
  `bill_sha256`), `score` (the score JSON, null for `out_of_scope`) and `translation` (the translation
  JSON), each at most 400,000 serialized characters. A score over the cap loses `revenue_rows` first
  (`revenue_rows_dropped: true` is added inside it); still over, or a translation over, fails the request.

## State taxes: general excise, transient accommodations, excises

A second path for bills that change the general excise tax, the transient accommodations tax, the
cigarette, fuel and liquor taxes or the rental vehicle surcharge (`state_taxes.py`,
`prompt_state_taxes.md`). Same pipeline, another model: the spec is scored through `state_revenue`
(`STATE_REVENUE_SCOPE.md`), not the tax-unit simulator, so it needs no population file.

```
python -m fiscal_notes state-run BILL.txt [--out DIR] [--model sonnet]
python -m fiscal_notes state-score NAME.state_spec.yaml [--translation FILE] [--out DIR]
python -m fiscal_notes state-eval [--record] [--model sonnet]
```

`state-run` writes `<name>.state_spec.yaml`, `<name>.state_translation.json`,
`<name>.state_score.json` and `<name>.state_note.md`. On the command line the path is the caller's
choice; the hub client routes (next section).

### Routing in the hub client

`python -m fiscal_notes hub` reads every bill with the income tax path, as before, and also with the
state-tax path when the bill AMENDS a chapter that path models (237 general excise, 237D transient
accommodations, 243 fuel, 244D liquor, 245 tobacco, 251 rental vehicles; `routing.mentions_state_taxes`).
A mention is not enough: income tax bills cite "general excise tax license number" without changing
the tax, and none of the income tax fixtures trips the gate. `--no-state-taxes` turns routing off.

| Income tax path | State-tax path | Posted |
|---|---|---|
| scored | not run, or nothing to score | the income tax note, as before |
| nothing to score, or invalid | scored | the state-tax note |
| scored | scored | one combined note: a combined bottom line, then each part in full |
| nothing / invalid | nothing / fails | what the income tax path alone decides |

The wire contract is unchanged: no field is added. For a state-tax note the headline's years are
fiscal years and `five_year_low` is the total under the largest sourced response where one applies
(the transient accommodations tax), else the static figure. For a combined note the headlines are
added, the year labels are the income tax path's, the provision list is merged (each provision
tagged `path`), and the state-tax score sits under `score.state_taxes`. A failure on the state-tax
path never fails the request: the income tax result is posted and the reason is left in
`<name>.state_path.txt` in the work directory.

The spec (every key optional; `validate_spec` is the schema):

| Key | Meaning |
|---|---|
| `effective` | first day the provisions apply, `YYYY-MM-DD`, or null |
| `general_excise.exempt` | consumer categories exempted: `groceries`, `restaurants`, `apparel`, `medical_services` |
| `general_excise.rates` | `{activity: percent}` for DOTAX's activities (`retailing`, `services`, `contracting`, ...) |
| `general_excise.repeal_exemptions` | Schedule GE exemption codes repealed in full (`"130"` nonprofits, ...) |
| `transient_accommodations.rate_change_points` or `.rate` | points added to the rate in force, or the new rate |
| `cigarettes.cents_per_cigarette` | tax per cigarette |
| `fuel.cents_per_gallon`, `fuel.dollars_per_barrel` | state fuel tax by fuel; the barrel tax |
| `liquor.dollars_per_gallon` | `spirits`, `wine`, `beer` |
| `rental_vehicle.dollars_per_day` | the surcharge |

Checks, as on the income tax path: the spec validates; every quote is verbatim in the bill; every
figure in the spec appears in the bill's in-force text, as digits or as the statute spells it ("one
percentage point", "four and one-half per cent"); every spec key is claimed by a modeled provision.
The verdict is derived from those, never taken from the model.

Scoring: five fiscal years from the one holding `effective`, the first prorated by days. A date
that is absent or already past is replaced by the start of the baseline's second fiscal year, and
the note says so. Static, one tax at a time, state revenue with the county surcharge beside it.

Fixtures are six 2026 bills in `tests/fiscal_notes/bills_state/` with recorded answers in
`responses_state/`; expectations in `state_expectations.py`. Re-record with `state-eval --record`
after any change to `prompt_state_taxes.md`.
