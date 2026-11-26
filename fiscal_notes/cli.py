"""``python -m fiscal_notes translate|eval``."""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import logging
import re
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from fiscal_notes.translator import (
    PROMPT_PATH,
    Translation,
    run_claude,
    translate_bill,
)

REPO = Path(__file__).resolve().parents[1]

_HEADER = re.compile(r"^BILL:\s*([A-Za-z]+)\s*(\d+)\s*(.*?)\s*\((\d{4}) session\)\s*$")
_SPEC_COMMENT = ("# Machine-translated from the bill text by `python -m fiscal_notes translate`.\n"
                 "# NOT reviewed: a person must check every figure and provision against the bill\n"
                 "# before this spec is scored or cited. See fiscal_notes/README.md.\n")


def parse_header(bill_text: str) -> dict:
    """``BILL: HB2306 HD1 (2026 session)`` -> name, label, session."""
    first = bill_text.lstrip().splitlines()[0] if bill_text.strip() else ""
    m = _HEADER.match(first)
    if not m:
        raise SystemExit(f"bill header not recognised: {first!r} "
                         "(expected 'BILL: HB2306 HD1 (2026 session)')")
    kind, number, rest, session = m.groups()
    slug = "_".join(p for p in re.split(r"[^a-z0-9]+", f"{kind}{number} {rest}".lower()) if p)
    label = " ".join(p for p in (f"{kind.upper()} {number}", rest) if p) + f" ({session})"
    return {"name": f"{slug}_{session}", "label": label, "session": session}


def sha256(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def write_outputs(bill_text: str, t: Translation, header: dict, model: str, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    res = t.result
    written = []
    if res.spec is not None:
        body = {"name": header["name"], "label": header["label"], **res.spec}
        path = out / f"{header['name']}.spec.yaml"
        path.write_text(_SPEC_COMMENT + yaml.safe_dump(body, sort_keys=False, allow_unicode=True))
        written.append(path)
    path = out / f"{header['name']}.translation.json"
    path.write_text(json.dumps({
        "name": header["name"],
        "label": header["label"],
        "verdict": res.verdict,
        "provisions": res.provisions,
        "notes": res.notes,
        "problems": res.problems,
        "model": model,
        "attempts": t.attempts,
        "bill_sha256": sha256(bill_text),
        "prompt_sha256": sha256(PROMPT_PATH.read_bytes()),
    }, indent=2, ensure_ascii=False) + "\n")
    written.append(path)
    return written


def summary(t: Translation, header: dict) -> str:
    res = t.result
    lines = [f"{header['label']}: {res.verdict} (attempts: {t.attempts})"]
    for modeled in (True, False):
        group = [p for p in res.provisions if p["modeled"] is modeled]
        if group:
            lines.append("modeled:" if modeled else "not modeled:")
            for p in group:
                blank = " [blank amount]" if p["blank"] else ""
                lines.append(f"  s{p['section']}: {p['summary']}{blank}")
    if res.problems:
        lines.append("problems:")
        lines.extend(f"  - {p}" for p in res.problems)
    return "\n".join(lines)


def cmd_translate(args, runner: Callable[[str], str] | None = None) -> int:
    bill_path = Path(args.bill)
    bill_text = bill_path.read_text()
    header = parse_header(bill_text)
    runner = runner or functools.partial(run_claude, model=args.model)
    t = translate_bill(bill_text, header["name"], runner)
    out = Path(args.out) if args.out else REPO / "reforms" / "bills" / header["session"]
    written = write_outputs(bill_text, t, header, args.model, out)
    print(summary(t, header))
    for p in written:
        print(f"wrote {p}")
    return 1 if t.result.verdict == "invalid" else 0


def cmd_eval(args, runner: Callable[[str], str] | None = None) -> int:
    from fiscal_notes import expectations as ex

    runner = runner or functools.partial(run_claude, model=args.model)

    def one(case: str) -> tuple[str, Translation]:
        text = ex.bill_text(case)
        return case, translate_bill(text, parse_header(text)["name"], runner)

    cases = list(ex.CASES)
    with ThreadPoolExecutor(max_workers=3) as pool:
        done = dict(pool.map(one, cases))
    failures = 0
    for case in cases:
        t = done[case]
        ev = ex.evaluate(case, t.result)
        failures += not ev.ok
        print(f"{case}: verdict {t.result.verdict}, attempts {t.attempts}, "
              f"{'ALL MET' if ev.ok else 'FAILED'}")
        for e in ev.expectations:
            print(f"    {'ok  ' if e.ok else 'FAIL'} {e.label}" + (f" ({e.detail})" if e.detail else ""))
        if args.record:
            ex.RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
            (ex.RESPONSES_DIR / f"{case}.json").write_text(t.responses[-1].strip() + "\n")
    if args.record:
        print(f"recorded {len(cases)} responses under {ex.RESPONSES_DIR}")
    return 1 if failures else 0


def main(argv: list[str] | None = None, runner: Callable[[str], str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fiscal_notes", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    tr = sub.add_parser("translate", help="translate one bill draft into a spec")
    tr.add_argument("bill", help="bill text file")
    tr.add_argument("--out", help="output directory (default reforms/bills/<session>/)")
    tr.add_argument("--model", default="sonnet")
    ev = sub.add_parser("eval", help="run the real translator over the fixture bills")
    ev.add_argument("--record", action="store_true", help="rewrite the recorded responses")
    ev.add_argument("--model", default="sonnet")
    args = ap.parse_args(argv)
    logging.disable(logging.INFO)  # tax_modeler logs every schedule it loads
    return cmd_translate(args, runner) if args.cmd == "translate" else cmd_eval(args, runner)


if __name__ == "__main__":
    sys.exit(main())
