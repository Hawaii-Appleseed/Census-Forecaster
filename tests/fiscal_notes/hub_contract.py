"""The hub contract (README.md, "Fetching a draft, and the staff hub client"), written out independently of the client so a test can
hold every body the client sends against it. Fields, types, enums and caps; nothing else."""
from __future__ import annotations

import json
import re

VERDICTS = ("full", "partial", "out_of_scope")
NOTE_CAP, JSON_CAP, DETAIL_CAP = 100_000, 400_000, 2_000
BILL = re.compile(r"^(HB|SB)\d{1,4}$")
DRAFT = re.compile(r"^(|[HS]D\d|CD\d)$")


def u16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _keys(obj, expected: set[str], where: str, problems: list[str]) -> bool:
    if not isinstance(obj, dict):
        problems.append(f"{where} is not an object")
        return False
    extra, missing = set(obj) - expected, expected - set(obj)
    if extra:
        problems.append(f"{where} has fields the contract does not: {sorted(extra)}")
    if missing:
        problems.append(f"{where} lacks fields the contract has: {sorted(missing)}")
    return not (extra or missing)


def validate_result(r) -> list[str]:
    """Problems with a ``Result`` as the local tool posts it (``posted_at`` is the hub's)."""
    p: list[str] = []
    if not _keys(r, {"verdict", "label", "markdown", "headline", "counts", "tool", "score",
                     "translation"}, "result", p):
        return p
    if r["verdict"] not in VERDICTS:
        p.append(f"verdict {r['verdict']!r} is not one of {VERDICTS}")
    if not (isinstance(r["label"], str) and r["label"]):
        p.append("label must be a non-empty string")
    if not isinstance(r["markdown"], str):
        p.append("markdown must be a string")
    elif u16(r["markdown"]) > NOTE_CAP:
        p.append(f"markdown is {u16(r['markdown'])} chars, cap {NOTE_CAP}")
    h = r["headline"]
    if r["verdict"] == "out_of_scope":
        if h is not None:
            p.append("headline must be null for out_of_scope")
        if r["score"] is not None:
            p.append("score must be null for out_of_scope")
    else:
        if _keys(h, {"five_year_mid", "five_year_low", "five_year_high", "first_year", "last_year"},
                 "headline", p):
            for k in ("five_year_mid", "five_year_low", "five_year_high"):
                if not _num(h[k]):
                    p.append(f"headline.{k} must be a number")
            for k in ("first_year", "last_year"):
                if not _int(h[k]):
                    p.append(f"headline.{k} must be an integer")
        if not isinstance(r["score"], dict):
            p.append("score must be an object for full and partial")
    if _keys(r["counts"], {"scored", "approximate", "not_scored"}, "counts", p):
        for k, v in r["counts"].items():
            if not _int(v) or v < 0:
                p.append(f"counts.{k} must be a non-negative integer")
    t = r["tool"]
    if _keys(t, {"commit", "model", "bill_sha256"}, "tool", p):
        if not (isinstance(t["commit"], str) and t["commit"]):
            p.append("tool.commit must be a non-empty string")
        if not (isinstance(t["model"], str) and t["model"]):
            p.append("tool.model must be a non-empty string")
        if not (isinstance(t["bill_sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", t["bill_sha256"])):
            p.append("tool.bill_sha256 must be 64 hex chars")
    for k in ("score", "translation"):
        if r[k] is not None:
            if not isinstance(r[k], dict):
                p.append(f"{k} must be an object or null")
            else:
                n = u16(json.dumps(r[k], ensure_ascii=False, separators=(",", ":"), allow_nan=False))
                if n > JSON_CAP:
                    p.append(f"{k} is {n} chars serialized, cap {JSON_CAP}")
    if not isinstance(r["translation"], dict):
        p.append("translation is required")
    return p


def validate_post(body) -> list[str]:
    """Problems with a body POSTed to /api/events-sync/fiscal-notes."""
    if not isinstance(body, dict) or not isinstance(body.get("id"), str) or not body["id"]:
        return ["body needs a string id"]
    status = body.get("status")
    p: list[str] = []
    if status == "running":
        _keys(body, {"id", "status"}, "running body", p)
    elif status == "ready":
        if _keys(body, {"id", "status", "result"}, "ready body", p):
            p += validate_result(body["result"])
    elif status == "failed":
        if _keys(body, {"id", "status", "detail"}, "failed body", p):
            d = body["detail"]
            if not (isinstance(d, str) and d):
                p.append("detail must be a non-empty string")
            elif u16(d) > DETAIL_CAP:
                p.append(f"detail is {u16(d)} chars, cap {DETAIL_CAP}")
    else:
        p.append(f"status {status!r} is not one a machine may post")
    try:
        json.dumps(body, allow_nan=False)
    except ValueError as e:
        p.append(f"body is not JSON-safe: {e}")
    return p
