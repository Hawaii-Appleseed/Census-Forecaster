"""Compare two drafts of the same bill: a pure function over two score JSONs and two translation JSONs."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fiscal_notes.note import signed, table

#: spec keys that identify the draft rather than the policy
_IGNORED = {"name", "label", "baseline", "model_version", "metadata"}


def resolve_draft(arg: str | Path) -> tuple[Path, Path]:
    """``DIR/name`` or ``DIR/name.score.json`` -> (score json, translation json) paths."""
    s = str(arg)
    base = s[: -len(".score.json")] if s.endswith(".score.json") else s
    return Path(base + ".score.json"), Path(base + ".translation.json")


# --------------------------------------------------------------------------- spec


def _vintages(spec: dict) -> dict[int, dict[str, list]] | None:
    it = spec.get("income_tax", {})
    if "bracket_vintages" in it:
        return {int(v["from"]): v["brackets"] for v in it["bracket_vintages"]}
    b = it.get("brackets")
    if isinstance(b, dict):
        return {int(spec["first_year"]): b}
    return None


def _flat(spec: dict) -> dict[str, Any]:
    """Every non-bracket spec setting as a dotted key (brackets are compared row by row)."""
    out: dict[str, Any] = {}
    for k, v in spec.items():
        if k in _IGNORED:
            continue
        if k == "income_tax":
            for ik, iv in v.items():
                if ik not in ("brackets", "bracket_vintages"):
                    out[f"income_tax.{ik}"] = iv
        elif isinstance(v, dict):
            out.update({f"{k}.{ik}": iv for ik, iv in v.items()})
        else:
            out[k] = v
    return out


def _show(v: Any) -> str:
    return "(not set)" if v is None else str(v)


def _rate(rows: list, floor: float) -> str | None:
    for f, r in rows:
        if f == floor:
            return f"{r:g}%"
    return None


def spec_changes(old: dict, new: dict) -> list[str]:
    out: list[str] = []
    fo, fn = _flat(old), _flat(new)
    rows = [[f"`{k}`", _show(fo.get(k)), _show(fn.get(k))] for k in sorted(set(fo) | set(fn))
            if fo.get(k) != fn.get(k)]
    if rows:
        out += ["Settings that changed:", "", *table(["Spec key", "Old", "New"], rows), ""]

    vo, vn = _vintages(old), _vintages(new)
    if vo is None and vn is None:
        pass
    elif vo is None or vn is None:
        out += [f"Brackets: the old draft {'keeps current law' if vo is None else 'sets a schedule'}; "
                f"the new draft {'keeps current law' if vn is None else 'sets a schedule'}.", ""]
    else:
        brows = []
        notes = []
        for vint in sorted(set(vo) | set(vn)):
            if vint not in vo or vint not in vn:
                notes.append(f"- Bracket vintage from tax year {vint} is only in the "
                             f"{'new' if vint in vn else 'old'} draft.")
                continue
            for status in sorted(set(vo[vint]) | set(vn[vint])):
                ro, rn = vo[vint].get(status, []), vn[vint].get(status, [])
                for floor in sorted({f for f, _ in ro} | {f for f, _ in rn}):
                    a, b = _rate(ro, floor), _rate(rn, floor)
                    if a != b:
                        brows.append([status, f"from {vint}", f"${floor:,.0f}", a or "(no row)",
                                      b or "(no row)"])
        if brows:
            out += ["Bracket rows that changed (rows are matched by floor):", "",
                    *table(["Status", "Vintage", "Floor", "Old rate", "New rate"], brows), ""]
        if notes:
            out += [*notes, ""]
        if not brows and not notes:
            out += ["Brackets: unchanged.", ""]
    if not rows:
        out = ["Other settings: unchanged.", "", *out]
    return out


# --------------------------------------------------------------------------- provisions


def _key(p: dict) -> tuple[int, tuple[str, ...]]:
    return p["section"], tuple(sorted(p["spec_keys"]))


def _same_text(a: dict, b: dict) -> bool:
    return " ".join(a["quote"].split()) == " ".join(b["quote"].split())


def _line(p: dict) -> str:
    keys = f" [{', '.join(p['spec_keys'])}]" if p["spec_keys"] else ""
    state = "scored" if p["modeled"] and not p["blank"] else "not scored"
    return f"section {p['section']}: {' '.join(p['summary'].split())}{keys} ({state})"


def _changed(a: dict, b: dict) -> list[str]:
    out = []
    for f, name in (("modeled", "modeled flag"), ("blank", "blank flag"),
                    ("applies_from_tax_year", "first tax year")):
        if a[f] != b[f]:
            out.append(f"{name} {a[f]} -> {b[f]}")
    if not _same_text(a, b):
        out.append("quoted text differs")
    return out


def provision_changes(old: list[dict], new: list[dict]) -> list[str]:
    """Match by (section, spec keys). Where the match is not one to one, list both sides."""
    groups: dict[tuple, tuple[list, list]] = {}
    for p in old:
        groups.setdefault(_key(p), ([], []))[0].append(p)
    for p in new:
        groups.setdefault(_key(p), ([], []))[1].append(p)
    added, dropped, changed, unsure = [], [], [], []
    for key in sorted(groups):
        o, n = groups[key]
        # identical quotes pair off first
        for p in list(o):
            m = next((q for q in n if _same_text(p, q)), None)
            if m is not None:
                o.remove(p)
                n.remove(m)
                diffs = _changed(p, m)
                if diffs:
                    changed.append(f"{_line(m)}: {'; '.join(diffs)}")
        if len(o) == 1 and len(n) == 1:
            changed.append(f"{_line(n[0])}: {'; '.join(_changed(o[0], n[0]))}")
        elif o and n:
            unsure += [f"old: {_line(p)}" for p in o] + [f"new: {_line(p)}" for p in n]
        else:
            dropped += [_line(p) for p in o]
            added += [_line(p) for p in n]
    out: list[str] = []
    for title, items in (("Provisions added in the new draft", added),
                         ("Provisions dropped from the old draft", dropped),
                         ("Provisions in both drafts, changed", changed),
                         ("Provisions that could not be matched safely (both sides listed)", unsure)):
        if items:
            out += [f"{title}:", "", *(f"- {i}" for i in items), ""]
    if not out:
        out = ["Provisions: the same in both drafts.", ""]
    return out


# --------------------------------------------------------------------------- revenue


def revenue_changes(old: dict, new: dict) -> list[str]:
    years = sorted({str(y) for y in old["years"]} | {str(y) for y in new["years"]})

    def at(score: dict, y: str) -> float | None:
        return score["total"]["mid"].get(y)

    def delta(a: float | None, b: float | None) -> str:
        return "n/a" if a is None or b is None else signed(b - a)

    rows = [[y, signed(at(old, y)), signed(at(new, y)), delta(at(old, y), at(new, y))] for y in years]
    fo, fn = old["five_year"]["total"], new["five_year"]["total"]
    rows.append(["5-year", signed(fo["mid"]), signed(fn["mid"]), delta(fo["mid"], fn["mid"])])
    out = ["MID, total change vs current law ($ millions, after behavioral response, credits included):",
           "", *table(["Tax year", "Old", "New", "Difference"], rows), ""]
    rng = []
    for k, name in (("low", "LOW"), ("high", "HIGH")):
        rng.append([name, signed(fo[k]), signed(fn[k]), delta(fo[k], fn[k])])
    out += ["Five-year range ($ millions):", "", *table(["Scenario", "Old", "New", "Difference"], rng), ""]
    return out


# --------------------------------------------------------------------------- whole


def render_diff(old_score: dict, old_tr: dict | None, new_score: dict, new_tr: dict | None) -> str:
    ol, nl = old_score.get("label") or old_score["name"], new_score.get("label") or new_score["name"]
    out = [f"# Comparison of drafts: {ol} -> {nl}", ""]
    if old_tr and new_tr and old_tr["verdict"] != new_tr["verdict"]:
        out += [f"Verdict changed: `{old_tr['verdict']}` -> `{new_tr['verdict']}`.", ""]
    out += ["## Spec", "", *spec_changes(old_score["spec"], new_score["spec"])]
    out += ["## Provisions", ""]
    if old_tr and new_tr:
        out += provision_changes(old_tr["provisions"], new_tr["provisions"])
    else:
        out += ["A translation file is missing for at least one draft, so provisions are not compared.", ""]
    out += ["## Revenue", "", *revenue_changes(old_score, new_score)]
    return "\n".join(out)
