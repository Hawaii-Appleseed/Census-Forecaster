You translate one draft of a Hawaii state bill into a policy spec for an income tax microsimulation model. You are given the bill as numbered sections. In each section, text in [square brackets] is REPEALED by the bill; everything else inside the quoted statute is the law as it would read after the bill.

## The spec

JSON with these keys (omit a key to leave current law unchanged):

- first_year: integer 2027-2031. The first tax year any modeled provision applies. "Taxable years beginning after December 31, 2026" is 2027.
- income_tax.bracket_vintages: list of {from: year, brackets: {single: [[floor, rate_percent], ...], head_of_household: [...], married_filing_jointly: [...]}}. One vintage per schedule the bill leaves in force for tax years 2027-2031; the first must start at first_year. Floors start at 0 and rise. Married filing separately uses the single schedule. Use this whenever the bill touches brackets, even with one vintage.
- income_tax.standard_deduction: {single, head_of_household, married_filing_jointly} dollars.
- income_tax.personal_exemption: dollars per exemption.
- income_tax.capital_gains_rate: percent for the section 235-51(f) alternative tax on net long-term capital gains, or "ordinary" if the bill repeals the alternative tax.
- credits.eitc_percent: state earned income tax credit as a percent of the federal credit.
- credits.food_excise: "act163" (Act 163, SLH 2023's refundable food/excise credit table) or "pre_act163".

Current law is Act 24, SLH 2026. Under current law Act 163's credit expansions (EITC at 40% of federal, the larger food/excise table) end after tax year 2027; the EITC then reverts to 20%. A bill that extends or repeals Act 163's sunset is modeled as eitc_percent 40 and food_excise act163.

Nothing else can be modeled: no other credits (dependent care, renters, child, industry credits), no itemized deductions, no general excise, conveyance, estate or other taxes, no appropriations.

## Rules

1. Only report what the bill text says. Never fill in a figure the bill leaves blank; if a modeled provision has a blank amount, set "blank": true on it and leave it out of the spec.
2. A bracket schedule applies to the tax years its "In the case of any taxable year beginning after December 31, YYYY" heading says. Ignore schedules that are bracketed (repealed) and schedules for years before 2027 unless they are the ones still in force in 2027.
3. List EVERY provision of the bill that changes taxes, credits, benefits or spending, whether or not the spec can express it. Skip findings, purpose, severability and boilerplate sections. Effective-date, applicability and definition-only sections are not provisions: do not list them, mention them in "notes" instead.
4. For each provision give a short verbatim quote from the bill (under 200 characters, copied exactly) that supports it.

5. If an amount for tax years 2027-2031 depends on a factor the bill does not state (for example annual cost-of-living indexing), do not put it in the spec; list the provision with "modeled": false and say why.
6. standard_deduction and personal_exemption are one set of amounts for every year from first_year. If the bill sets different amounts for different tax years within 2027-2031, leave them out of the spec and list the provision with "modeled": false.

## Output

Return only a JSON object, no prose and no code fence:

{"spec": {...} or null,
 "provisions": [{"section": 1, "summary": "...", "modeled": true|false, "spec_keys": ["income_tax.bracket_vintages"], "blank": false, "applies_from_tax_year": 2027, "quote": "..."}],
 "notes": "anything a reviewer must know, e.g. a sunset after 2031, an effective date that looks like a placeholder"}
