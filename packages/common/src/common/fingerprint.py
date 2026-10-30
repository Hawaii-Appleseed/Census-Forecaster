"""The cache key of the chain's reusable step outputs, and the code-closure it hashes.

``fingerprint`` keys a cached output on the model code, the interpreter and
library versions, and the step's own inputs; ``code_closure`` traces, from the
source alone, which workspace modules a step can import so an unrelated edit
keeps the cache. ``closure_guard`` fails a cache miss that imports outside its
declared closure.

Dependency-free on purpose (``census-common`` has none): numpy and pandas are
read lazily inside :func:`fingerprint`. The repo root is derived from this
file's location (``packages/common/src/common/fingerprint.py``); set
``CF_REPO_ROOT`` to override it for an installed, non-editable copy.
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path


def _find_repo() -> Path:
    env = os.environ.get("CF_REPO_ROOT")
    root = Path(env).resolve() if env else Path(__file__).resolve().parents[4]
    missing = [n for n in ("packages", "pyproject.toml") if not (root / n).exists()]
    if missing:
        raise RuntimeError(
            f"common.fingerprint: {root} is not the Census-Forecaster repo root "
            f"(no {' or '.join(missing)}). It is derived from this file's location, which "
            "only holds for a checkout / editable install; set CF_REPO_ROOT to the checkout."
        )
    return root


REPO = _find_repo()

# Directories (relative to REPO) whose ``*.py`` are importable by bare name: the scripts put
# them on ``sys.path``. The packages under ``packages/*/src`` are always included.
SCRIPT_DIRS = ("chain", "analyses", "scripts")

# The narrow cache key (fingerprint(code=...)); CF_NARROW_FINGERPRINT=0 hashes the whole packages.
NARROW_FINGERPRINT = os.environ.get("CF_NARROW_FINGERPRINT", "1") != "0"


def _workspace_modules() -> dict[str, Path]:
    """Every importable workspace module, by dotted name: the packages under
    ``packages/*/src`` and the ``*.py`` of each of :data:`SCRIPT_DIRS` (the
    steps put those directories on ``sys.path``)."""
    mods: dict[str, Path] = {}
    for d in SCRIPT_DIRS:
        for f in sorted((REPO / d).glob("*.py")):
            mods.setdefault(f.stem, f.resolve())
    for src in sorted((REPO / "packages").glob("*/src")):
        for f in sorted(src.rglob("*.py")):
            parts = f.relative_to(src).with_suffix("").parts
            mods[".".join(parts[:-1] if parts[-1] == "__init__" else parts)] = f.resolve()
    return mods


def code_closure(*entries: str | Path) -> list[Path] | None:
    """The source files of every workspace module *entries* (dotted module
    names, or script Paths) can import, or None if one of them imports code
    in a way this cannot follow.

    It reads the source, it does not run it, and errs toward too much: an
    import anywhere in a file counts (inside a function, under
    ``TYPE_CHECKING``), as do the ``__init__`` of each package on the way and,
    for ``from a import b``, both ``a`` and ``a.b``. ``importlib.import_module``
    or ``__import__`` with a non-literal name, ``spec_from_file_location``,
    ``runpy`` and ``exec`` make it give up. Code that reaches a step some other
    way — the methods of an object the caller passes in, a class a pickle
    names — is the caller's to list in *entries*; :func:`closure_guard`
    catches what is imported while the step runs.

    A package whose ``__init__`` loads its exports lazily (a ``_LAZY`` dict
    of name -> module, read by ``__getattr__``) is followed through that map:
    ``from pkg import X`` and ``pkg.X`` (on a name bound by an import) count
    as importing ``_LAZY["X"]``, and ``pkg.sub`` as importing ``pkg.sub``.
    """
    import ast

    mods = _workspace_modules()
    by_file = {f: m for m, f in mods.items()}

    def resolve(name: str) -> str | None:
        while name:
            if name in mods:
                return name
            name = name.rpartition(".")[0]
        return None

    def absolute(package: str, level: int, module: str | None) -> str:
        if not level:
            return module or ""
        up = package.split(".")[:len(package.split(".")) - (level - 1)]
        return ".".join(up + ([module] if module else []))

    def lazy_map(name: str, tree) -> dict[str, str] | None:
        """A package ``__init__``'s ``_LAZY`` map, with absolute module names."""
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and getattr(node.targets[0], "id", None) == "_LAZY"
                    and isinstance(node.value, ast.Dict)):
                return {k.value: absolute(name, len(v.value) - len(v.value.lstrip(".")),
                                          v.value.lstrip(".") or None)
                        for k, v in zip(node.value.keys, node.value.values, strict=True)}
        return None

    trees = {}

    def tree_of(name: str):
        if name not in trees:
            f = mods[name]
            trees[name] = ast.parse(f.read_bytes(), filename=str(f))
        return trees[name]

    lazy = {m: lm for m, f in mods.items() if f.name == "__init__.py" and b"_LAZY" in f.read_bytes()
            and (lm := lazy_map(m, tree_of(m))) is not None}

    def expand(dotted: str) -> set[str]:
        """*dotted*, plus the module a lazy package's ``_LAZY`` maps a name in it to."""
        parts = dotted.split(".")
        out = {dotted}
        for i in range(1, len(parts)):
            target = lazy.get(".".join(parts[:i]), {}).get(parts[i])
            if target:
                out.add(target)
        return out

    def imports(name: str) -> set[str] | None:
        f = mods[name]
        tree = tree_of(name)
        package = name if f.name == "__init__.py" else name.rpartition(".")[0]
        out: set[str] = set()
        bound: dict[str, str] = {}   # local name -> the module an import bound it to
        lazy_getattr = set()         # nodes of a lazy package's own __getattr__
        if name in lazy:
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and node.name == "__getattr__":
                    lazy_getattr.update(ast.walk(node))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    out.add(a.name)
                    if a.asname:
                        bound[a.asname] = a.name
                    else:
                        top = a.name.split(".")[0]
                        bound[top] = top
            elif isinstance(node, ast.ImportFrom):
                base = absolute(package, node.level, node.module)
                out.add(base)
                for a in node.names:
                    if a.name == "*":
                        out.update(lazy.get(base, {}).values())
                        continue
                    out |= expand(f"{base}.{a.name}")
                    bound[a.asname or a.name] = f"{base}.{a.name}"
            elif isinstance(node, ast.Call) and node not in lazy_getattr:
                fn = node.func
                called = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
                if called in ("import_module", "__import__"):
                    arg = node.args[0] if node.args else None
                    if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                        return None
                    out.add(arg.value)
                elif called in ("spec_from_file_location", "run_path", "run_module", "exec"):
                    return None
        # pkg.X on a name an import bound: X may be a lazy export or a submodule
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                attrs = []
                base = node
                while isinstance(base, ast.Attribute):
                    attrs.append(base.attr)
                    base = base.value
                if isinstance(base, ast.Name) and base.id in bound:
                    out |= expand(".".join([bound[base.id], *reversed(attrs)]))
        return out

    todo = []
    for e in entries:
        if isinstance(e, Path):
            name = by_file.get(e.resolve())
            if name is None:
                raise ValueError(f"{e} is not a workspace module")
        else:
            name = resolve(e)
            if name != e:
                raise ValueError(f"{e} is not a workspace module")
        todo.append(name)
    seen: set[str] = set()
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        parts = name.split(".")
        todo.extend(p for i in range(1, len(parts)) if (p := resolve(".".join(parts[:i]))))
        found = imports(name)
        if found is None:
            return None
        todo.extend(r for i in found if (r := resolve(i)) and r not in seen)
    return sorted(mods[m] for m in seen)


def _is_workspace_file(f: Path) -> bool:
    return (any(f.parent == (REPO / d).resolve() for d in SCRIPT_DIRS)
            or any(f.is_relative_to(src) for src in (REPO / "packages").glob("*/src")))


@contextmanager
def closure_guard(code: tuple):
    """Around a cached step's computation (a cache miss): fail if it imports
    a workspace module that ``code_closure(*code)`` left out, since a change
    there would not have changed the key. Off with the narrow key
    (``CF_NARROW_FINGERPRINT=0``); a module loaded before the step began is
    not seen, so ``scripts/check_code_closure.py`` runs each step in a fresh
    process."""
    files = code_closure(*code) if NARROW_FINGERPRINT else None
    before = set(sys.modules)
    yield
    if files is None:
        return
    allowed = set(files)
    missed = sorted(
        f"{name} ({f.relative_to(REPO)})"
        for name in set(sys.modules) - before - {"__main__", "__mp_main__"}  # the step's script
        if (path := getattr(sys.modules[name], "__file__", None))
        and _is_workspace_file(f := Path(path).resolve()) and f not in allowed
    )
    if missed:
        raise RuntimeError("the cache key's code closure missed modules this step imported: "
                           + ", ".join(missed) + ". Add them to its entries.")


def fingerprint(*parts, code: tuple | None = None) -> str:
    """A sha256 key for a cached step output: the model code, the Python,
    numpy and pandas versions, and *parts* — Paths (a file's bytes, a
    directory's files), bytes, or anything ``json.dumps`` takes.

    The model code is every file under each workspace package's ``src``
    (``tax_modeler`` and the ``census_forecaster``, ``pums_estimator`` and
    ``common`` code it calls) and ``config``: any model change misses every
    cache, slower but never stale. (Until 2026-10-29 only ``tax_modeler``
    was hashed, so a change in the packages it imports kept a stale cache;
    until the narrow key, ``packages/*/config`` was left out.)

    Given *code* (the step's entry modules, see :func:`code_closure`), the
    Python files are only those *code* can
    import, so an edit elsewhere keeps the cache; every non-Python file
    under ``src`` and ``config`` is still hashed. If the closure cannot be
    traced, or ``CF_NARROW_FINGERPRINT=0``, it hashes the whole packages
    as above.
    """
    import hashlib
    import json

    import numpy as np
    import pandas as pd

    h = hashlib.sha256()

    def add_file(f: Path) -> None:
        h.update(str(f.relative_to(REPO) if f.is_relative_to(REPO) else f).encode())
        h.update(f.read_bytes())

    def add_path(p: Path) -> None:
        files = sorted(f for f in p.rglob("*") if f.is_file() and "__pycache__" not in f.parts) \
            if p.is_dir() else [p]
        for f in files:
            add_file(f)

    closure = code_closure(*code) if NARROW_FINGERPRINT and code else None
    trees = sorted([*(REPO / "packages").glob("*/src"), *(REPO / "packages").glob("*/config")])
    if closure is None:
        for tree in trees:
            add_path(tree)
    else:
        h.update(b"narrow")
        for f in closure:
            add_file(f)
        for tree in trees:
            for f in sorted(f for f in tree.rglob("*") if f.is_file() and f.suffix not in (".py", ".pyc")
                            and "__pycache__" not in f.parts):
                add_file(f)
    h.update(f"{sys.version}|numpy {np.__version__}|pandas {pd.__version__}".encode())
    for part in parts:
        if isinstance(part, Path):
            add_path(part)
        elif isinstance(part, bytes):
            h.update(part)
        else:
            h.update(json.dumps(part, sort_keys=True, default=str).encode())
    return h.hexdigest()
