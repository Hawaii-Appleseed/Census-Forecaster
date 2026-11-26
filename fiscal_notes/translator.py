"""Translate the text of a Hawaii bill draft into an ``IncomeTaxSpec``-shaped spec.

The LLM reads the bill and returns JSON; everything around the call is
deterministic and never trusts it: the spec is run through the model's own
validator, every quoted passage must appear in the bill, every figure in the
spec must appear in the bill text, and the provisions must account for every
spec key. The verdict is derived here, not taken from the model.

Pure functions except :func:`run_claude` (subprocess) and the prompt read.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROMPT_PATH = Path(__file__).with_name("prompt.md")

VERDICTS = ("full", "partial", "out_of_scope", "invalid")


class TranslationError(ValueError):
    """The model's answer is not a response of the expected shape."""


class RunnerError(RuntimeError):
    """The LLM call itself failed (non-zero exit, timeout, missing binary)."""


# --------------------------------------------------------------------------- prompt


def prompt_rules() -> str:
    return PROMPT_PATH.read_text()


def build_prompt(bill_text: str) -> str:
    return f"{prompt_rules().rstrip()}\n\n## The bill\n\n{bill_text.strip()}\n"


# --------------------------------------------------------------------------- parsing

_FENCE = re.compile(r"^```[A-Za-z]*\s*\n?(.*?)\n?\s*```$", re.DOTALL)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse_response(raw: str) -> dict:
    """Parse and structurally validate the model's JSON answer."""
    text = raw.strip()
    m = _FENCE.match(text)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise TranslationError(f"response is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise TranslationError(f"response must be a JSON object, got {type(data).__name__}")
    missing = {"spec", "provisions", "notes"} - set(data)
    if missing:
        raise TranslationError(f"response is missing keys {sorted(missing)}")
    extra = set(data) - {"spec", "provisions", "notes"}
    if extra:
        raise TranslationError(f"response has unexpected keys {sorted(extra)}")
    if data["spec"] is not None and not isinstance(data["spec"], dict):
        raise TranslationError("`spec` must be an object or null")
    if not isinstance(data["notes"], str):
        raise TranslationError("`notes` must be a string")
    provs = data["provisions"]
    if not isinstance(provs, list):
        raise TranslationError("`provisions` must be a list")
    for i, p in enumerate(provs):
        where = f"provisions[{i}]"
        if not isinstance(p, dict):
            raise TranslationError(f"{where} must be an object")
        for key, ok, want in (
            ("section", _is_int, "an integer"),
            ("summary", lambda v: isinstance(v, str), "a string"),
            ("modeled", lambda v: isinstance(v, bool), "a boolean"),
            ("spec_keys", lambda v: isinstance(v, list) and all(isinstance(k, str) for k in v),
             "a list of strings"),
            ("blank", lambda v: isinstance(v, bool), "a boolean"),
            ("applies_from_tax_year", lambda v: v is None or _is_int(v), "an integer or null"),
            ("quote", lambda v: isinstance(v, str), "a string"),
        ):
            if key not in p:
                raise TranslationError(f"{where} is missing `{key}`")
            if not ok(p[key]):
                raise TranslationError(f"{where}.{key} must be {want}, got {p[key]!r}")
        # added later than the other fields: older recorded answers lack them
        p.setdefault("approximate", False)
        p.setdefault("approximation", "")
        if not isinstance(p["approximate"], bool):
            raise TranslationError(f"{where}.approximate must be a boolean, got {p['approximate']!r}")
        if not isinstance(p["approximation"], str):
            raise TranslationError(f"{where}.approximation must be a string, got {p['approximation']!r}")
        if p["approximate"]:
            if not p["modeled"]:
                raise TranslationError(f"{where}: `approximate` is only allowed on a modeled provision")
            if not p["approximation"].strip():
                raise TranslationError(f"{where}: `approximate` is true, so `approximation` must say "
                                       "what the spec gets wrong and in which direction")
        elif p["approximation"].strip():
            raise TranslationError(f"{where}: `approximation` is set but `approximate` is false")
    return data


# --------------------------------------------------------------------------- checks


@dataclass
class CheckResult:
    verdict: str
    spec: dict | None
    provisions: list[dict]
    notes: str
    problems: list[str] = field(default_factory=list)


def _collapse(s: str) -> str:
    return " ".join(s.split())


_REPEALED = re.compile(r"\[[^\[\]]*\]")


def in_force(text: str) -> str:
    """The bill text without its [bracketed] repealed material, innermost first."""
    prev = None
    while prev != text:
        prev, text = text, _REPEALED.sub(" ", text)
    return text


def _num_text(v: float) -> str:
    """12 -> '12', 7.25 -> '7.25' (no trailing zeros)."""
    return f"{v:.6f}".rstrip("0").rstrip(".")


def _dollar_in(text: str, amount: float) -> bool:
    """``$9,600`` (thousands separators), also ``$9,600.00`` / ``$9,600.50``."""
    forms = [f"{amount:,.0f}"] if float(amount).is_integer() else []
    forms.append(f"{amount:,.2f}")
    return any(re.search(rf"(?<![\d,.])\${re.escape(f)}(?![\d]|,\d)", text) for f in forms)


def _bracket_rate_in(text: str, rate: float) -> bool:
    """Bracket rates are written ``3.20%`` in HRS 235-51's tables."""
    return re.search(rf"(?<![\d.]){re.escape(f'{rate:.2f}')}%", text) is not None


def _cg_rate_in(text: str, rate: float) -> bool:
    """A rate in prose: ``7.25%``, ``7.25 per cent``, ``.5 per cent``, ``9 percent``."""
    n = _num_text(rate)
    lead = rf"0?{re.escape(n[1:])}" if n.startswith("0.") else re.escape(n)
    pat = rf"(?<![\d.]){lead}(?:\.0+)?\s*(?:%|per\s?cent)"
    return re.search(pat, text, re.IGNORECASE) is not None


def _walk(spec: dict, dotted: str) -> bool:
    cur: Any = spec
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def spec_leaves(spec: dict) -> list[str]:
    """The ``income_tax.*`` and ``credits.*`` keys the spec sets."""
    out = []
    for block in ("income_tax", "credits"):
        sub = spec.get(block)
        if isinstance(sub, dict):
            out.extend(f"{block}.{k}" for k in sub)
    return out


def _schedules(spec: dict) -> list[list[list[float]]]:
    """Every explicit bracket schedule in the spec (vintages or ``brackets``)."""
    it = spec.get("income_tax") or {}
    sources = [v.get("brackets") for v in (it.get("bracket_vintages") or []) if isinstance(v, dict)]
    sources.append(it.get("brackets"))
    out = []
    for b in sources:
        if isinstance(b, dict):
            out.extend(rows for rows in b.values() if isinstance(rows, list))
    return out


def _figure_problems(spec: dict, text: str) -> list[str]:
    problems = []
    seen: set[tuple[str, float]] = set()

    def need(kind: str, value: float, where: str, ok: bool) -> None:
        if not ok and (kind, value) not in seen:
            seen.add((kind, value))
            shown = f"${value:,.0f}" if kind == "dollar" else (
                f"{value:.2f}%" if kind == "bracket_rate" else f"{_num_text(value)}%")
            problems.append(f"figure {shown} ({where}) does not appear in the bill text")

    for rows in _schedules(spec):
        for row in rows:
            if not (isinstance(row, list) and len(row) == 2):
                continue
            floor, rate = row
            if isinstance(floor, (int, float)) and floor > 0:
                need("dollar", float(floor), "bracket floor", _dollar_in(text, float(floor)))
            if isinstance(rate, (int, float)):
                need("bracket_rate", float(rate), "bracket rate", _bracket_rate_in(text, float(rate)))
    it = spec.get("income_tax") or {}
    sd = it.get("standard_deduction")
    if isinstance(sd, dict):
        for fs, v in sd.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                need("dollar", float(v), f"standard deduction, {fs}", _dollar_in(text, float(v)))
    pe = it.get("personal_exemption")
    if isinstance(pe, (int, float)) and not isinstance(pe, bool):
        need("dollar", float(pe), "personal exemption", _dollar_in(text, float(pe)))
    cg = it.get("capital_gains_rate")
    if isinstance(cg, (int, float)) and not isinstance(cg, bool):
        need("cg_rate", float(cg), "capital gains rate", _cg_rate_in(text, float(cg)))
    return problems


def check(response: dict, bill_text: str, name: str) -> CheckResult:
    """Run every deterministic check on a parsed response; never trusts the model."""
    from tax_modeler.errors import ConfigError
    from tax_modeler.reform.income_tax_spec import IncomeTaxSpec

    spec = response["spec"]
    provisions = response["provisions"]
    problems: list[str] = []
    if spec is not None and not spec_leaves(spec):
        spec = None  # nothing modeled (e.g. only first_year): no spec, not a spec to check

    if spec is not None:
        try:
            IncomeTaxSpec.from_dict({"name": name, **spec})
        except ConfigError as e:
            problems.append(f"spec is not valid: {e}")
        except (TypeError, ValueError, AttributeError) as e:
            problems.append(f"spec is not valid: {type(e).__name__}: {e}")

    haystack = _collapse(bill_text)
    for p in provisions:
        quote = _collapse(p["quote"])
        if not quote:
            problems.append(f"section {p['section']}: the provision has no quote")
        elif quote not in haystack:
            problems.append(f"section {p['section']}: quote is not verbatim in the bill: "
                            f"{p['quote'][:80]!r}")

    if spec is not None:
        # a figure only in repealed text is not a figure the bill enacts
        problems.extend(_figure_problems(spec, _collapse(in_force(bill_text))))
        claimed: set[str] = set()
        for p in provisions:
            if not p["modeled"]:
                continue
            for key in p["spec_keys"]:
                claimed.add(key)
                if not _walk(spec, key):
                    problems.append(f"section {p['section']}: modeled provision cites spec key "
                                    f"`{key}`, which is not in the spec")
        for leaf in spec_leaves(spec):
            if not any(k == leaf or k.startswith(leaf + ".") for k in claimed):
                problems.append(f"spec key `{leaf}` is not claimed by any modeled provision")

    if problems:
        verdict = "invalid"
    elif spec is None or not any(p["modeled"] for p in provisions):
        verdict = "out_of_scope"
    elif any((not p["modeled"]) or p["blank"] or p.get("approximate") for p in provisions):
        verdict = "partial"
    else:
        verdict = "full"
    return CheckResult(verdict=verdict, spec=spec, provisions=provisions,
                       notes=response["notes"], problems=problems)


def effective_brackets(spec: dict, year: int, name: str = "x") -> dict[str, list[list[float]]] | None:
    """The spec's schedules for ``year``: the latest vintage whose ``from`` is <= year
    (None if the spec has no schedule in force that year)."""
    from tax_modeler.reform.income_tax_spec import IncomeTaxSpec

    sched = IncomeTaxSpec.from_dict({"name": name, **spec}).brackets_for_year(year)
    if sched is None:
        return None
    return {fs: [[float(f), float(r)] for f, r in rows] for fs, rows in sched.items()}


# --------------------------------------------------------------------------- LLM runner


def run_claude(prompt: str, model: str = "sonnet", timeout: int = 600) -> str:
    """Run the ``claude`` CLI once (no tools), prompt on stdin, answer from stdout."""
    binary = os.environ.get("CLAUDE_BIN") or "claude"
    cmd = [binary, "-p", "--model", model, "--tools", ""]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RunnerError(f"{binary} timed out after {timeout}s") from e
    except FileNotFoundError as e:
        raise RunnerError(f"claude binary not found: {binary} (set CLAUDE_BIN)") from e
    if proc.returncode != 0:
        raise RunnerError(f"{binary} exited {proc.returncode}: "
                          f"{(proc.stderr or proc.stdout).strip()[:500]}")
    return proc.stdout


# --------------------------------------------------------------------------- translate


@dataclass
class Translation:
    result: CheckResult
    responses: list[str]
    attempts: int


def retry_prompt(prompt: str, previous: str, problems: list[str]) -> str:
    listed = "\n".join(f"- {p}" for p in problems)
    return (f"{prompt.rstrip()}\n\n## Your previous answer\n\n{previous.strip()}\n\n"
            f"Your previous answer had these problems:\n{listed}\n\n"
            "Return the corrected JSON object.\n")


def translate_bill(bill_text: str, name: str,
                   runner: Callable[[str], str] = run_claude) -> Translation:
    """Prompt, run, parse, check; one retry with the problems appended."""
    prompt = build_prompt(bill_text)
    responses: list[str] = []
    result: CheckResult | None = None
    for attempt in (1, 2):
        raw = runner(prompt if attempt == 1 else retry_prompt(prompt, responses[-1], result.problems))
        responses.append(raw)
        try:
            result = check(parse_response(raw), bill_text, name)
        except TranslationError as e:
            result = CheckResult(verdict="invalid", spec=None, provisions=[], notes="",
                                 problems=[f"response could not be parsed: {e}"])
        if result.verdict != "invalid":
            break
    return Translation(result=result, responses=responses, attempts=len(responses))
