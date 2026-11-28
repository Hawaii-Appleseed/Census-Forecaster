"""The hub's ``Result`` (see README.md, "Fetching a draft, and the staff hub client"): a pure function of the tool's
outputs, with the contract's size caps enforced here as well as on the hub.

No model calls, no file access, no network.
"""
from __future__ import annotations

import copy
import json

NOTE_CAP = 100_000       # chars of Markdown
JSON_CAP = 400_000       # chars of serialized score, and of serialized translation
DETAIL_CAP = 2_000       # chars of a ``failed`` detail
TRUNCATED_MARKER = ("[NOTE TRUNCATED: the draft note was cut here to fit the hub's 100,000-character "
                    "limit. The full note is in the work directory on the scoring machine.]")


class ResultError(RuntimeError):
    """The outputs cannot be made into a Result the hub will take; the message becomes the
    ``failed`` detail."""


def u16len(s: str) -> int:
    """Length as the hub counts it: JavaScript string length (UTF-16 code units)."""
    return len(s.encode("utf-16-le", errors="surrogatepass")) // 2


def cap_text(s: str, limit: int) -> str:
    """``s`` cut to at most ``limit`` UTF-16 units (a plain cut, no marker)."""
    if u16len(s) <= limit:
        return s
    s = s[:limit]
    while u16len(s) > limit:
        s = s[:-1]
    return s


def serialized_len(obj) -> int:
    """Length of the compact JSON text of ``obj``, in the hub's units."""
    return u16len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def cap_note(markdown: str) -> str:
    """The note, or its head plus a visible marker line when it is over the cap."""
    if u16len(markdown) <= NOTE_CAP:
        return markdown
    tail = f"\n\n---\n\n{TRUNCATED_MARKER}\n"
    head = cap_text(markdown, NOTE_CAP - u16len(tail))
    cut = head.rfind("\n")  # end on a whole line when one is near
    if cut > len(head) - 2_000:
        head = head[:cut]
    return head.rstrip() + tail


def failed_detail(text: str) -> str:
    return cap_text(text, DETAIL_CAP)


def invalid_detail(problems: list[str]) -> str:
    """The ``failed`` detail for an ``invalid`` translation: the problems, first 2,000 chars."""
    lines = "\n".join(f"- {p}" for p in problems) if problems else "(no problem was recorded)"
    return failed_detail("The bill text did not translate into a checked spec:\n" + lines)


# --------------------------------------------------------------------------- counts, headline


def _scored(p: dict) -> bool:
    return bool(p["modeled"]) and not p["blank"]


def counts(provisions: list[dict]) -> dict:
    """Same partition the note uses: scored (in the estimate, exactly), approximate (in the
    estimate, roughly), not scored (not modeled, or the amount is blank in the bill)."""
    approx = sum(1 for p in provisions if _scored(p) and p.get("approximate"))
    scored = sum(1 for p in provisions if _scored(p)) - approx
    return {"scored": scored, "approximate": approx, "not_scored": len(provisions) - scored - approx}


def headline(score: dict) -> dict:  # noqa: D401 - the income tax path's
    """Five-year totals, $ millions, vs current law; positive raises revenue. The ``total`` block
    is income tax plus credits, which is the note's bottom line."""
    total = score["five_year"]["total"]
    out = {}
    for key, field in (("mid", "five_year_mid"), ("low", "five_year_low"), ("high", "five_year_high")):
        v = total.get(key)
        if v is None:
            raise ResultError(f"the score has no five-year {key.upper()} total (a year is missing)")
        out[field] = round(float(v), 1) + 0.0  # + 0.0: no "-0.0"
    years = score["years"]
    out["first_year"], out["last_year"] = int(years[0]), int(years[-1])
    return out


_income_headline = headline      # build_result's keyword of the same name shadows it


# --------------------------------------------------------------------------- result


def build_result(translation: dict, score: dict | None, note: str, *, commit: str, model: str,
                 bill_sha256: str, headline: dict | None = None) -> dict:
    """The contract's ``Result`` for a ``full``, ``partial`` or ``out_of_scope`` translation.

    ``invalid`` is not a Result: the caller posts ``failed`` with :func:`invalid_detail`.
    Raises :class:`ResultError` when the outputs do not fit (score missing for a scored verdict,
    or still over a cap after dropping ``score.revenue_rows``)."""
    verdict = translation["verdict"]
    if verdict not in ("full", "partial", "out_of_scope"):
        raise ResultError(f"no Result for a {verdict!r} translation")
    if verdict == "out_of_scope":
        score = None
        head = None
    else:
        if score is None:
            raise ResultError(f"a {verdict} translation needs a score and none was produced")
        # a caller that scored another way (state taxes, or both paths) brings its own headline
        head = headline if headline is not None else _income_headline(score)

    translation = copy.deepcopy(translation)
    if score is not None:
        score = copy.deepcopy(score)
        if serialized_len(score) > JSON_CAP:
            score.pop("revenue_rows", None)
            score["revenue_rows_dropped"] = True  # inside the opaque score only: says why it is shorter
        if serialized_len(score) > JSON_CAP:
            raise ResultError(f"the score JSON is {serialized_len(score):,} characters even without "
                              f"revenue_rows; the hub takes at most {JSON_CAP:,}")
    if serialized_len(translation) > JSON_CAP:
        raise ResultError(f"the translation JSON is {serialized_len(translation):,} characters; "
                          f"the hub takes at most {JSON_CAP:,}")

    return {
        "verdict": verdict,
        "label": translation["label"],
        "markdown": cap_note(note),
        "headline": head,
        "counts": counts(translation["provisions"]),
        "tool": {"commit": commit, "model": model, "bill_sha256": bill_sha256},
        "score": score,
        "translation": translation,
    }
