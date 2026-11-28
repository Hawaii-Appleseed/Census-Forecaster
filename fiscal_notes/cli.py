"""``python -m fiscal_notes translate|eval|score|run|diff|fetch|hub|state-run|state-score|state-eval``."""
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

from fiscal_notes.diff import render_diff, resolve_draft
from fiscal_notes.note import render_note, render_out_of_scope_note
from fiscal_notes.score import ScoreError, dumps, score_spec_file
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
    spec_path = out / f"{header['name']}.spec.yaml"
    if res.verdict in ("out_of_scope", "invalid"):
        # nothing to score: also remove a spec an earlier draft of the same name left behind
        spec_path.unlink(missing_ok=True)
    elif res.spec is not None:
        body = {"name": header["name"], "label": header["label"], **res.spec}
        spec_path.write_text(_SPEC_COMMENT + yaml.safe_dump(body, sort_keys=False, allow_unicode=True))
        written.append(spec_path)
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
                blank += " [approximate]" if p.get("approximate") else ""
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


def spec_name(spec_path: Path) -> str:
    """``hb2306_hd1_2026.spec.yaml`` -> ``hb2306_hd1_2026``."""
    n = spec_path.name
    return n[: -len(".spec.yaml")] if n.endswith(".spec.yaml") else spec_path.stem


def _write_note(translation: dict, score: dict | None, out: Path, name: str) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    if score is None:
        note = render_out_of_scope_note(translation)
    else:
        note = render_note(translation, score)
        path = out / f"{name}.score.json"
        path.write_text(dumps(score))
        written.append(path)
    path = out / f"{name}.note.md"
    path.write_text(note)
    return [*written, path]


def _score_and_write(translation: dict, spec_path: Path | None, out: Path, name: str,
                     scorer: Callable[[Path], dict] | None, population: str | None) -> int:
    """Shared by ``score`` and ``run``: refuse invalid, write the short note for out_of_scope,
    otherwise score the spec and write the note and score JSON."""
    verdict = translation["verdict"]
    if verdict == "invalid":
        print(f"refusing to score {translation['label']}: the translation is invalid", file=sys.stderr)
        for p in translation.get("problems", []):
            print(f"  - {p}", file=sys.stderr)
        return 1
    if verdict == "out_of_scope":
        written = _write_note(translation, None, out, name)
    else:
        scorer = scorer or functools.partial(score_spec_file, population_dir=population)
        try:
            score = scorer(spec_path)
        except ScoreError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        written = _write_note(translation, score, out, name)
    for p in written:
        print(f"wrote {p}")
    return 0


def cmd_score(args, scorer: Callable[[Path], dict] | None = None) -> int:
    spec_path = Path(args.spec)
    name = spec_name(spec_path)
    tr_path = Path(args.translation) if args.translation else spec_path.with_name(f"{name}.translation.json")
    if not tr_path.exists():
        print(f"ERROR: translation file not found: {tr_path} (pass --translation)", file=sys.stderr)
        return 2
    translation = json.loads(tr_path.read_text())
    out = Path(args.out) if args.out else spec_path.parent
    return _score_and_write(translation, spec_path, out, name, scorer, args.population)


def cmd_run(args, runner: Callable[[str], str] | None = None,
            scorer: Callable[[Path], dict] | None = None) -> int:
    bill_text = Path(args.bill).read_text()
    header = parse_header(bill_text)
    runner = runner or functools.partial(run_claude, model=args.model)
    t = translate_bill(bill_text, header["name"], runner)
    out = Path(args.out) if args.out else REPO / "reforms" / "bills" / header["session"]
    written = write_outputs(bill_text, t, header, args.model, out)
    print(summary(t, header))
    for p in written:
        print(f"wrote {p}")
    translation = json.loads((out / f"{header['name']}.translation.json").read_text())
    spec_path = out / f"{header['name']}.spec.yaml"
    return _score_and_write(translation, spec_path, out, header["name"], scorer, args.population)


# ---- state taxes (general excise, transient accommodations, excises) ---------------------------


def _state_outputs(bill_text: str, t: Translation, header: dict, model: str, out: Path) -> dict:
    """Write the spec and translation JSON for a state-tax bill; return the translation dict."""
    from fiscal_notes import state_taxes as st

    out.mkdir(parents=True, exist_ok=True)
    res = t.result
    spec_path = out / f"{header['name']}.state_spec.yaml"
    if res.verdict in ("out_of_scope", "invalid") or res.spec is None:
        spec_path.unlink(missing_ok=True)
    else:
        body = {"name": header["name"], "label": header["label"], **res.spec}
        spec_path.write_text(_SPEC_COMMENT + yaml.safe_dump(body, sort_keys=False, allow_unicode=True))
        print(f"wrote {spec_path}")
    translation = {
        "name": header["name"], "label": header["label"], "verdict": res.verdict,
        "provisions": res.provisions, "notes": res.notes, "problems": res.problems,
        "model": model, "attempts": t.attempts, "bill_sha256": sha256(bill_text),
        "prompt_sha256": sha256(st.PROMPT_PATH.read_bytes()),
    }
    path = out / f"{header['name']}.state_translation.json"
    path.write_text(json.dumps(translation, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {path}")
    return translation


def _state_note(translation: dict, spec: dict | None, out: Path, name: str,
                scorer: Callable[[dict], dict] | None) -> int:
    from fiscal_notes import state_taxes as st

    if translation["verdict"] == "invalid":
        print(f"refusing to score {translation['label']}: the translation is invalid", file=sys.stderr)
        for p in translation.get("problems", []):
            print(f"  - {p}", file=sys.stderr)
        return 1
    score = None
    if translation["verdict"] != "out_of_scope":
        try:
            score = (scorer or st.score_spec)(spec)
        except st.SpecError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        path = out / f"{name}.state_score.json"
        path.write_text(st.dumps(score))
        print(f"wrote {path}")
    path = out / f"{name}.state_note.md"
    path.write_text(st.render_note(translation, score))
    print(f"wrote {path}")
    return 0


def cmd_state_run(args, runner: Callable[[str], str] | None = None,
                  scorer: Callable[[dict], dict] | None = None) -> int:
    from fiscal_notes import state_taxes as st

    bill_text = Path(args.bill).read_text()
    header = parse_header(bill_text)
    runner = runner or functools.partial(run_claude, model=args.model)
    t = st.translate_bill(bill_text, runner)
    out = Path(args.out) if args.out else REPO / "reforms" / "bills" / header["session"]
    print(summary(t, header))
    translation = _state_outputs(bill_text, t, header, args.model, out)
    return _state_note(translation, t.result.spec, out, header["name"], scorer)


def cmd_state_score(args, scorer: Callable[[dict], dict] | None = None) -> int:
    spec_path = Path(args.spec)
    name = spec_path.name.removesuffix(".state_spec.yaml")
    tr_path = (Path(args.translation) if args.translation
               else spec_path.with_name(f"{name}.state_translation.json"))
    if not tr_path.exists():
        print(f"ERROR: translation file not found: {tr_path} (pass --translation)", file=sys.stderr)
        return 2
    spec = yaml.safe_load(spec_path.read_text())
    out = Path(args.out) if args.out else spec_path.parent
    return _state_note(json.loads(tr_path.read_text()), spec, out, name, scorer)


def cmd_state_eval(args, runner: Callable[[str], str] | None = None) -> int:
    from fiscal_notes import state_expectations as ex
    from fiscal_notes import state_taxes as st

    runner = runner or functools.partial(run_claude, model=args.model)
    cases = list(ex.CASES)
    with ThreadPoolExecutor(max_workers=3) as pool:
        done = dict(zip(cases, pool.map(lambda c: st.translate_bill(ex.bill_text(c), runner), cases),
                        strict=True))
    failures = 0
    for case in cases:
        t = done[case]
        checks = ex.evaluate(case, t.result)
        ok = all(passed for _, passed in checks)
        failures += not ok
        print(f"{case}: verdict {t.result.verdict}, attempts {t.attempts}, {'ALL MET' if ok else 'FAILED'}")
        for label, passed in checks:
            print(f"    {'ok  ' if passed else 'FAIL'} {label}")
        for prob in t.result.problems:
            print(f"    problem: {prob}")
        if args.record:
            ex.RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
            (ex.RESPONSES_DIR / f"{case}.json").write_text(t.responses[-1].strip() + "\n")
    if args.record:
        print(f"recorded {len(cases)} responses under {ex.RESPONSES_DIR}")
    return 1 if failures else 0


def cmd_diff(args) -> int:
    paths = [resolve_draft(a) for a in (args.old, args.new)]
    for score_path, _ in paths:
        if not score_path.exists():
            print(f"ERROR: score file not found: {score_path}", file=sys.stderr)
            return 2
    loaded = []
    for score_path, tr_path in paths:
        tr = json.loads(tr_path.read_text()) if tr_path.exists() else None
        loaded.append((json.loads(score_path.read_text()), tr))
    text = render_diff(loaded[0][0], loaded[0][1], loaded[1][0], loaded[1][1])
    print(text)
    if args.out:
        Path(args.out).write_text(text if text.endswith("\n") else text + "\n")
        print(f"wrote {args.out}")
    return 0


def cmd_fetch(args, fetcher: Callable[[int, str, str], dict] | None = None) -> int:
    from fiscal_notes import hub

    fetcher = fetcher or hub.fetch_draft
    try:
        page = fetcher(args.year, args.bill.upper(), args.draft.upper())
    except (hub.FetchError, hub._lrt_fetch.DraftError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.out:
        Path(args.out).write_text(page["text"])
        print(f"wrote {args.out} (from {page['url']})")
    else:
        sys.stdout.write(page["text"])
        print(f"from {page['url']}", file=sys.stderr)
    return 0


def cmd_hub(args, hub_client=None, deps=None) -> int:
    from fiscal_notes import hub

    try:
        client = hub_client or hub.HubClient(args.hub)
        deps = deps or hub.Deps(model=args.model)
        work = Path(args.work_dir) if args.work_dir else None
        print(f"{'DRY RUN, nothing is posted: ' if args.dry_run else ''}hub {args.hub}")
        outcomes = hub.process_queue(client, deps, work_root=work, dry_run=args.dry_run,
                                     limit=args.limit,
                                     report=lambda o: print(hub.describe(o), flush=True))
    except hub.HubError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    tally: dict[str, int] = {}
    for o in outcomes:
        tally[o.status] = tally.get(o.status, 0) + 1
    shown = ", ".join(f"{n} {k}" for k, n in sorted(tally.items())) or "nothing queued"
    print(f"{len(outcomes)} request(s): {shown}")
    return 0


def main(argv: list[str] | None = None, runner: Callable[[str], str] | None = None,
         scorer: Callable[[Path], dict] | None = None, fetcher: Callable[[int, str, str], dict] | None = None,
         hub_client=None, deps=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fiscal_notes", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    tr = sub.add_parser("translate", help="translate one bill draft into a spec")
    tr.add_argument("bill", help="bill text file")
    tr.add_argument("--out", help="output directory (default reforms/bills/<session>/)")
    tr.add_argument("--model", default="sonnet")
    ev = sub.add_parser("eval", help="run the real translator over the fixture bills")
    ev.add_argument("--record", action="store_true", help="rewrite the recorded responses")
    ev.add_argument("--model", default="sonnet")
    sc = sub.add_parser("score", help="score a spec and write the draft note and score JSON")
    sc.add_argument("spec", help="spec file (.spec.yaml)")
    sc.add_argument("--translation", help="translation JSON (default: <name>.translation.json beside the spec)")
    sc.add_argument("--out", help="output directory (default: beside the spec)")
    sc.add_argument("--population", help="simulator population directory (default runs/tax_simulator)")
    rn = sub.add_parser("run", help="translate a bill draft, score it, write the note")
    rn.add_argument("bill", help="bill text file")
    rn.add_argument("--out", help="output directory (default reforms/bills/<session>/)")
    rn.add_argument("--model", default="sonnet")
    rn.add_argument("--population", help="simulator population directory (default runs/tax_simulator)")
    df = sub.add_parser("diff", help="compare two drafts of one bill")
    df.add_argument("old", help="DIR/name prefix or .score.json of the older draft")
    df.add_argument("new", help="DIR/name prefix or .score.json of the newer draft")
    df.add_argument("--out", help="also write the comparison to this Markdown file")
    fe = sub.add_parser("fetch", help="fetch a bill draft's text from the Capitol (via LRT) in the input format")
    fe.add_argument("--year", type=int, required=True, help="session year")
    fe.add_argument("--bill", required=True, help="HB2306")
    fe.add_argument("--draft", default="", help="HD1, SD2, CD1; omit for the introduced draft")
    fe.add_argument("--out", help="write the text to this file (default: stdout)")
    hb = sub.add_parser("hub", help="process the staff hub's queued fiscal-note requests once")
    hb.add_argument("--hub", default="https://staff-updates-internal.pages.dev", help="hub base URL")
    hb.add_argument("--once", action="store_true", help="process the queue once and exit (the default)")
    hb.add_argument("--dry-run", action="store_true", help="do everything except the POSTs; print them")
    hb.add_argument("--limit", type=int, help="process at most N requests, oldest first")
    hb.add_argument("--model", default="sonnet")
    hb.add_argument("--work-dir", help="where each request's files go (default runs/fiscal_notes/)")
    sr = sub.add_parser("state-run", help="translate a general excise, transient accommodations "
                                          "or excise bill, score it, write the note")
    sr.add_argument("bill", help="bill text file")
    sr.add_argument("--out", help="output directory (default reforms/bills/<session>/)")
    sr.add_argument("--model", default="sonnet")
    ss = sub.add_parser("state-score", help="score a state-tax spec and write the draft note")
    ss.add_argument("spec", help="spec file (.state_spec.yaml)")
    ss.add_argument("--translation", help="translation JSON (default: beside the spec)")
    ss.add_argument("--out", help="output directory (default: beside the spec)")
    se = sub.add_parser("state-eval", help="run the real state-tax translator over its fixture bills")
    se.add_argument("--record", action="store_true", help="rewrite the recorded responses")
    se.add_argument("--model", default="sonnet")
    args = ap.parse_args(argv)
    logging.disable(logging.INFO)  # tax_modeler logs every schedule it loads
    if args.cmd == "translate":
        return cmd_translate(args, runner)
    if args.cmd == "eval":
        return cmd_eval(args, runner)
    if args.cmd == "score":
        return cmd_score(args, scorer)
    if args.cmd == "run":
        return cmd_run(args, runner, scorer)
    if args.cmd == "fetch":
        return cmd_fetch(args, fetcher)
    if args.cmd == "hub":
        return cmd_hub(args, hub_client, deps)
    if args.cmd == "state-run":
        return cmd_state_run(args, runner)
    if args.cmd == "state-score":
        return cmd_state_score(args)
    if args.cmd == "state-eval":
        return cmd_state_eval(args, runner)
    return cmd_diff(args)


if __name__ == "__main__":
    sys.exit(main())
