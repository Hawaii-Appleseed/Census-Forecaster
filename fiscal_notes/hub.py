"""The hub client: read queued fiscal-note requests from the staff hub, make each note, post it back.

``python -m fiscal_notes hub`` runs this once over the queue. The wire format is the contract in
README.md ("Fetching a draft, and the staff hub client"); nothing here adds, renames or drops a field.

Network: ``urllib.request`` only. Credentials: the Cloudflare Access service token, never printed
or logged (see :func:`load_credentials`). Everything the loop touches is injectable (transport,
fetcher, translator, scorer, clock of provenance), so the tests run offline.
"""
from __future__ import annotations

import functools
import json
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fiscal_notes import _lrt_fetch
from fiscal_notes.cli import _write_note, parse_header, sha256, write_outputs
from fiscal_notes.hub_result import (
    ResultError,
    build_result,
    failed_detail,
    invalid_detail,
    serialized_len,
)
from fiscal_notes.score import ScoreError, model_provenance, runs_dir, score_spec_file
from fiscal_notes.translator import Translation, run_claude, translate_bill

DEFAULT_HUB = "https://staff-updates-internal.pages.dev"
ROUTE = "/api/events-sync/fiscal-notes"
#: The only hosts the stored service token is ever sent to. Any other hub URL (a local mock) gets
#: placeholder credentials, so a typo or a test server never receives the real secret.
TOKEN_HOSTS = frozenset({"staff-updates-internal.pages.dev", "staging.staff-updates-internal.pages.dev"})
PLACEHOLDER_CREDENTIALS = ("local-mock", "local-mock")
USER_AGENT = "Census-Forecaster-fiscal-notes/1 (Hawaii Appleseed shadow fiscal notes; hub client)"
ENV_FILE = Path("~/.config/fiscal-notes/hub.env")
KEYCHAIN_SERVICES = {"CF_ACCESS_CLIENT_ID": "apps-script:CF_ACCESS_CLIENT_ID",
                     "CF_ACCESS_CLIENT_SECRET": "apps-script:CF_ACCESS_CLIENT_SECRET"}
LRT_DEFAULT = "~/repos/Legislative-Research-Tool"

_BILL = re.compile(r"^(HB|SB)\d{1,4}$")
_DRAFT = re.compile(r"^(|[HS]D\d|CD\d)$")


# --------------------------------------------------------------------------- errors


class HubError(RuntimeError):
    """The hub could not be used. ``process_queue`` stops on these (it is not one request's fault)."""


class CredentialsError(HubError):
    """No usable Access service token."""


class TokenRefused(HubError):
    """The hub answered something that is not JSON: Access returns its sign-in page, with status
    200, for a missing or wrong service token."""


class HubUnreachable(HubError):
    """The network failed (after the allowed retry)."""


class HubRejected(RuntimeError):
    """The hub understood and said no: 400, 404, 409 or 5xx with a JSON ``error``. Per request."""

    def __init__(self, status: int, message: str):
        super().__init__(f"hub answered {status}: {message}")
        self.status, self.message = status, message


class NetworkError(OSError):
    """Raised by a transport. ``sent`` is False when the failure came before any request bytes
    could have left (DNS failure, refused connection), so a POST may safely be retried."""

    def __init__(self, message: str, sent: bool):
        super().__init__(message)
        self.sent = sent


class FetchError(RuntimeError):
    """The draft's text could not be fetched or parsed; the message becomes the ``failed`` detail."""


# --------------------------------------------------------------------------- credentials


def _parse_env_file(text: str) -> dict[str, str]:
    vals = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, eq, v = line.partition("=")
        if eq:
            vals[k.removeprefix("export ").strip()] = v.strip().strip("'\"")
    return vals


def _security(service: str, run: Callable) -> str:
    try:
        out = run(["security", "find-generic-password", "-s", service, "-w"],
                  capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return ""
    return (out.stdout or "").strip()


def load_credentials(environ: dict[str, str] | None = None, env_file: Path | None = None,
                     run: Callable | None = None, platform: str | None = None) -> tuple[str, str]:
    """The Access service token (id, secret). Order: the environment, then the env file
    (``~/.config/fiscal-notes/hub.env``, KEY=value lines, refused unless only its owner can read it),
    then the macOS Keychain items ``apps-script:CF_ACCESS_CLIENT_ID`` / ``..._SECRET``.
    Never echoes a value."""
    environ = os.environ if environ is None else environ
    run = run or subprocess.run
    cid, sec = environ.get("CF_ACCESS_CLIENT_ID", ""), environ.get("CF_ACCESS_CLIENT_SECRET", "")
    if cid and sec:
        return cid, sec
    path = (env_file or ENV_FILE).expanduser()
    if path.is_file():
        if path.stat().st_mode & 0o077:
            raise CredentialsError(f"{path} is readable by others: chmod 600 it first.")
        vals = _parse_env_file(path.read_text())
        cid, sec = vals.get("CF_ACCESS_CLIENT_ID", ""), vals.get("CF_ACCESS_CLIENT_SECRET", "")
        if cid and sec:
            return cid, sec
    if (platform or sys.platform) == "darwin":
        cid = _security(KEYCHAIN_SERVICES["CF_ACCESS_CLIENT_ID"], run)
        sec = _security(KEYCHAIN_SERVICES["CF_ACCESS_CLIENT_SECRET"], run)
        if cid and sec:
            return cid, sec
    raise CredentialsError(
        "No Access service token: set CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET, or put them "
        f"in {ENV_FILE} (chmod 600), or on a Mac keep them in the Keychain "
        f"({KEYCHAIN_SERVICES['CF_ACCESS_CLIENT_ID']} / {KEYCHAIN_SERVICES['CF_ACCESS_CLIENT_SECRET']}).")


# --------------------------------------------------------------------------- transport

#: ``transport(method, url, headers, body, timeout) -> (status, content_type, text)``; raises
#: :class:`NetworkError` when the network fails. An HTTP error status is a return, not a raise.
Transport = Callable[[str, str, dict, bytes | None, float], tuple[int, str, str]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Access answers with a redirect to its sign-in host; never follow it with the token attached."""

    def redirect_request(self, *args, **kwargs):
        return None


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None,
                     timeout: float) -> tuple[int, str, str]:
    req = urllib.request.Request(url, method=method, data=body, headers=headers)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, r.headers.get("content-type", ""), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("content-type", "") if e.headers else "", \
            e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        before_send = isinstance(e.reason, (socket.gaierror, ConnectionRefusedError))
        raise NetworkError(f"{type(e.reason).__name__}: {e.reason}", sent=not before_send) from e
    except (TimeoutError, ConnectionError, OSError) as e:
        raise NetworkError(f"{type(e).__name__}: {e}", sent=True) from e


class HubClient:
    """The two machine routes. One retry on a network error for GET; for POST only when the failure
    came before the request was sent (a ``ready`` that may have landed must not be sent twice)."""

    def __init__(self, hub: str = DEFAULT_HUB, credentials: tuple[str, str] | None = None,
                 transport: Transport = urllib_transport, timeout: float = 60.0):
        self.url = hub.rstrip("/") + ROUTE
        parts = urllib.parse.urlsplit(self.url)
        self.sends_token = parts.scheme == "https" and parts.hostname in TOKEN_HOSTS
        self._credentials = credentials
        self.transport = transport
        self.timeout = timeout

    def _headers(self) -> dict:
        cid, sec = self._credentials or (
            load_credentials() if self.sends_token else PLACEHOLDER_CREDENTIALS)
        self._credentials = (cid, sec)
        return {"CF-Access-Client-Id": cid, "CF-Access-Client-Secret": sec,
                "Content-Type": "application/json", "Accept": "application/json",
                "User-Agent": USER_AGENT}

    def _call(self, method: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = self._headers()
        for attempt in (1, 2):
            try:
                status, ctype, text = self.transport(method, self.url, headers, data, self.timeout)
                break
            except NetworkError as e:
                retry = attempt == 1 and (method == "GET" or not e.sent)
                if not retry:
                    raise HubUnreachable(f"{method} {ROUTE}: {e}") from e
        return self._read(method, status, ctype, text)

    @staticmethod
    def _read(method: str, status: int, ctype: str, text: str) -> dict:
        # Access answers a missing or wrong token with its sign-in page: any non-JSON body is that.
        parsed: Any = None
        if "json" in ctype.lower():
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
        if not isinstance(parsed, dict):
            if status >= 500:
                raise HubUnreachable(f"{method} {ROUTE}: the hub answered {status} with a "
                                     f"{ctype or 'no'} body, not JSON")
            raise TokenRefused(f"{method} {ROUTE}: the hub answered {status} {ctype or 'no type'}, "
                               "not JSON: the service token was refused (Access returns its sign-in "
                               "page)")
        if status >= 400:
            raise HubRejected(status, str(parsed.get("error") or text[:200]))
        return parsed

    def queue(self) -> list[dict]:
        """Every queued request, oldest first."""
        reqs = self._call("GET").get("requests")
        if not isinstance(reqs, list):
            raise HubError("the hub's queue answer has no `requests` list")
        return reqs

    def post(self, body: dict) -> dict:
        return self._call("POST", body)


# --------------------------------------------------------------------------- fetching a draft


def lrt_root() -> Path:
    return Path(os.environ.get("FISCAL_NOTES_LRT_ROOT") or LRT_DEFAULT).expanduser()


def fetch_draft(year: int, bill: str, draft: str, *, root: Path | None = None,
                run: Callable | None = None, timeout: int = 900) -> dict:
    """``{"text", "url", "title", "dir_year"}`` for one draft, from the Capitol, by running
    ``fiscal_notes/_lrt_fetch.py`` under Legislative-Research-Tool's interpreter (its paced client,
    its parser). Raises :class:`FetchError` with a message fit for the hub."""
    run = run or subprocess.run
    root = (root or lrt_root()).expanduser()
    python = root / ".venv" / "bin" / "python"
    if not python.exists():
        raise FetchError(f"Legislative-Research-Tool not found: no {python} (set FISCAL_NOTES_LRT_ROOT "
                         "to its checkout)")
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR") if k in os.environ}
    env["PYTHONPATH"] = str(root)  # no credentials go to the child
    cmd = [str(python), str(Path(_lrt_fetch.__file__)), "--year", str(year), "--bill", bill,
           "--draft", draft]
    try:
        proc = run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=str(root))
    except subprocess.TimeoutExpired as e:
        raise FetchError(f"fetching {bill} {draft or 'Introduced'} timed out after {timeout}s") from e
    except OSError as e:
        raise FetchError(f"could not run Legislative-Research-Tool's interpreter: {e}") from e
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        out = None
    if not isinstance(out, dict):
        raise FetchError(f"the fetch helper exited {proc.returncode} without JSON: "
                         f"{(proc.stderr or proc.stdout).strip()[-300:]}")
    if proc.returncode != 0 or "error" in out:
        raise FetchError(str(out.get("error") or f"the fetch helper exited {proc.returncode}"))
    if not (isinstance(out.get("text"), str) and out["text"].strip()):
        raise FetchError("the fetch helper returned no text")
    return out


# --------------------------------------------------------------------------- the loop


@dataclass
class Deps:
    """What the loop calls out to. The defaults are the real thing."""

    fetch: Callable[[int, str, str], dict] = fetch_draft
    translate: Callable[[str, str], Translation] | None = None  # (bill_text, name); None: run_claude
    score: Callable[[Path], dict] = score_spec_file
    commit: Callable[[], str | None] = lambda: model_provenance()["model_git_sha"]
    model: str = "sonnet"
    population: str | None = None


@dataclass
class Outcome:
    id: str
    label: str
    status: str                       # ready | failed | skipped | error
    detail: str = ""
    body: dict | None = field(default=None, repr=False)   # what was (or would be) posted last
    work_dir: Path | None = None


def _translator(deps: Deps) -> Callable[[str, str], Translation]:
    if deps.translate:
        return deps.translate
    runner = functools.partial(run_claude, model=deps.model)
    return lambda text, name: translate_bill(text, name, runner)


def _valid_request(r: Any) -> bool:
    return (isinstance(r, dict) and isinstance(r.get("id"), str) and isinstance(r.get("year"), int)
            and isinstance(r.get("bill"), str) and _BILL.match(r["bill"]) is not None
            and isinstance(r.get("draft"), str) and _DRAFT.match(r["draft"]) is not None)


def _label(r: dict) -> str:
    return f"{r.get('bill')} {r.get('draft') or 'Introduced'} ({r.get('year')})"


def work_dir_for(root: Path, year: int, name: str) -> Path:
    return root / str(year) / name


def make_note_and_result(req: dict, text: str, t: Translation, deps: Deps, out: Path) -> dict:
    """Write the request's files under ``out`` and return the body to post (ready or failed)."""
    header = parse_header(text)
    write_outputs(text, t, header, deps.model, out)
    translation = json.loads((out / f"{header['name']}.translation.json").read_text())
    verdict = translation["verdict"]
    if verdict == "invalid":
        return {"id": req["id"], "status": "failed", "detail": invalid_detail(translation["problems"])}
    spec_path = out / f"{header['name']}.spec.yaml"
    score = None
    if verdict != "out_of_scope":
        try:
            score = deps.score(spec_path)
        except ScoreError as e:
            return {"id": req["id"], "status": "failed", "detail": failed_detail(f"scoring failed: {e}")}
        except Exception as e:  # noqa: BLE001 - the contract: "scoring raises" is a failed request
            return {"id": req["id"], "status": "failed",
                    "detail": failed_detail(f"scoring raised {type(e).__name__}: {e}")}
    _write_note(translation, score, out, header["name"])
    note = (out / f"{header['name']}.note.md").read_text()
    try:
        result = build_result(translation, score, note, commit=deps.commit() or "unknown",
                              model=deps.model, bill_sha256=sha256(text))
    except ResultError as e:
        return {"id": req["id"], "status": "failed", "detail": failed_detail(str(e))}
    return {"id": req["id"], "status": "ready", "result": result}


def process_one(client: HubClient, req: dict, deps: Deps, work_root: Path, *,
                dry_run: bool = False) -> Outcome:
    label = _label(req)
    if not _valid_request(req):
        if isinstance(req, dict) and isinstance(req.get("id"), str) and not dry_run:
            body = {"id": req["id"], "status": "failed",
                    "detail": failed_detail(f"malformed request from the hub: {json.dumps(req)[:300]}")}
            client.post(body)
            return Outcome(req["id"], label, "failed", body["detail"], body)
        return Outcome(str(req.get("id") if isinstance(req, dict) else "?"), label, "error",
                       "malformed request, skipped")
    rid = req["id"]
    if not dry_run:
        try:
            client.post({"id": rid, "status": "running"})
        except HubRejected as e:
            if e.status == 409:  # withdrawn (or taken) meanwhile
                return Outcome(rid, label, "skipped", f"no longer queued ({e.message})")
            raise
    out: Path | None = None
    try:
        page = deps.fetch(req["year"], req["bill"], req["draft"])
        text = page["text"]
        name = parse_header(text)["name"]
        out = work_dir_for(work_root, req["year"], name)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{name}.txt").write_text(text)
        t = _translator(deps)(text, name)
        body = make_note_and_result(req, text, t, deps, out)
    except (FetchError, _lrt_fetch.DraftError) as e:
        body = {"id": rid, "status": "failed", "detail": failed_detail(str(e))}
    except SystemExit as e:  # parse_header exits on a header it does not recognise
        body = {"id": rid, "status": "failed", "detail": failed_detail(f"the fetched text: {e}")}
    except Exception as e:  # noqa: BLE001 - one request must not stop the next
        body = {"id": rid, "status": "failed",
                "detail": failed_detail(f"{type(e).__name__} while making the note: {e}")}
    work = out
    if work:
        (work / "posted.json").write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n")
    if dry_run:
        return Outcome(rid, label, body["status"], body.get("detail", ""), body, work)
    try:
        client.post(body)
    except HubRejected as e:
        if body["status"] == "ready" and e.status == 400:
            body = {"id": rid, "status": "failed",
                    "detail": failed_detail(f"the hub refused the result: {e.message}")}
            client.post(body)
        elif e.status == 409:
            return Outcome(rid, label, "skipped", f"the hub no longer accepts a result ({e.message})",
                           body, work)
        else:
            raise
    return Outcome(rid, label, body["status"], body.get("detail", ""), body, work)


def process_queue(client: HubClient, deps: Deps | None = None, *, work_root: Path | None = None,
                  dry_run: bool = False, limit: int | None = None,
                  report: Callable[[Outcome], None] = lambda o: None) -> list[Outcome]:
    """GET the queue; for each request, oldest first: ``running``, fetch, translate, score
    (full/partial), render the note, then ``ready`` or ``failed``. One request failing does not
    stop the others; a hub that cannot be reached or refuses the token (:class:`HubError`) does,
    and propagates. ``dry_run`` does everything except the POSTs."""
    deps = deps or Deps()
    work_root = work_root or runs_dir() / "fiscal_notes"
    queue = sorted(client.queue(), key=lambda r: str(r.get("at", "")) if isinstance(r, dict) else "")
    if limit is not None:
        queue = queue[:limit]
    outcomes = []
    for req in queue:
        try:
            o = process_one(client, req, deps, work_root, dry_run=dry_run)
        except HubRejected as e:
            o = Outcome(str(req.get("id")), _label(req), "error", str(e))
        outcomes.append(o)
        report(o)
    return outcomes


def describe(o: Outcome) -> str:
    """One line for a request; a dry run's line carries the verdict, headline, counts and sizes."""
    if o.status == "failed":
        return f"{o.label}: failed: {o.detail.splitlines()[0][:160] if o.detail else ''}"
    if o.status != "ready" or not o.body:
        return f"{o.label}: {o.status}: {o.detail}"
    r = o.body["result"]
    h = r["headline"]
    head = (f"MID {h['five_year_mid']:+,.1f}M (LOW {h['five_year_low']:+,.1f}, HIGH "
            f"{h['five_year_high']:+,.1f}) {h['first_year']}-{h['last_year']}") if h else "no headline"
    c = r["counts"]
    sizes = (f"note {len(r['markdown']):,}, score {serialized_len(r['score']) if r['score'] else 0:,}, "
             f"translation {serialized_len(r['translation']):,} chars")
    return (f"{o.label}: ready: {r['verdict']}; {head}; scored {c['scored']}, approximate "
            f"{c['approximate']}, not scored {c['not_scored']}; {sizes}")

