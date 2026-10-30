"""common.fingerprint, the key of the chain's cached step outputs."""
from __future__ import annotations

import sys
from pathlib import Path

from common import fingerprint as fp
from common.fingerprint import REPO, fingerprint


def test_same_inputs_same_key():
    assert fingerprint(b"x", {"a": 1}) == fingerprint(b"x", {"a": 1})


def test_key_order_of_a_dict_does_not_matter():
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})


def test_any_part_changes_the_key(tmp_path):
    f = tmp_path / "input.csv"
    f.write_text("1\n")
    before = fingerprint(f, b"x")
    assert fingerprint(f, b"y") != before
    f.write_text("2\n")
    assert fingerprint(f, b"x") != before


def test_a_directory_hashes_its_files(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    before = fingerprint(tmp_path)
    (tmp_path / "b.txt").write_text("b")
    assert fingerprint(tmp_path) != before


def test_every_workspace_package_is_hashed(monkeypatch):
    """A change in any package the model imports (not only tax_modeler)
    changes the key."""
    seen = []
    real = Path.rglob

    def spy(self, pattern):
        seen.append(self)
        return real(self, pattern)

    monkeypatch.setattr(Path, "rglob", spy)
    fp.fingerprint(b"x")
    hashed = {p.relative_to(REPO).as_posix() for p in seen}
    for src in (REPO / "packages").glob("*/src"):
        assert src.relative_to(REPO).as_posix() in hashed


# -- the narrow key (fingerprint(code=...); CF_NARROW_FINGERPRINT=0 turns it off)

def _fake_repo(tmp_path, monkeypatch):
    """A workspace with one package: ``pkg.used`` imports ``pkg.helper``;
    ``pkg.unused`` is imported by nothing; a step script imports ``pkg.used``."""
    src = tmp_path / "packages" / "pkg" / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "used.py").write_text("def f():\n    from .helper import g\n    return g()\n")
    (src / "helper.py").write_text("def g():\n    return 1\n")
    (src / "unused.py").write_text("X = 1\n")
    (src / "table.json").write_text("{}")
    (tmp_path / "packages" / "pkg" / "config").mkdir()
    (tmp_path / "packages" / "pkg" / "config" / "params.json").write_text("{}")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "step.py").write_text("import pkg.used\n")
    monkeypatch.setattr(fp, "REPO", tmp_path)
    monkeypatch.setattr(fp, "NARROW_FINGERPRINT", True)
    return src


def test_closure_follows_imports_inside_functions_and_packages(tmp_path, monkeypatch):
    src = _fake_repo(tmp_path, monkeypatch)
    names = {f.name for f in fp.code_closure(tmp_path / "scripts" / "step.py")}
    assert names == {"step.py", "__init__.py", "used.py", "helper.py"}
    assert {f.name for f in fp.code_closure("pkg.used")} == \
        {"__init__.py", "used.py", "helper.py"}
    assert (src / "unused.py") not in fp.code_closure("pkg.used")


def test_narrow_key_ignores_code_the_step_cannot_reach(tmp_path, monkeypatch):
    src = _fake_repo(tmp_path, monkeypatch)
    before = fingerprint(b"x", code=("pkg.used",))
    (src / "unused.py").write_text("X = 2\n")
    assert fingerprint(b"x", code=("pkg.used",)) == before


def test_narrow_key_changes_with_code_the_step_reaches(tmp_path, monkeypatch):
    src = _fake_repo(tmp_path, monkeypatch)
    before = fingerprint(b"x", code=("pkg.used",))
    (src / "helper.py").write_text("def g():\n    return 2\n")
    assert fingerprint(b"x", code=("pkg.used",)) != before


def test_narrow_key_still_hashes_every_data_and_config_file(tmp_path, monkeypatch):
    src = _fake_repo(tmp_path, monkeypatch)
    before = fingerprint(b"x", code=("pkg.used",))
    (src / "table.json").write_text('{"a": 1}')
    middle = fingerprint(b"x", code=("pkg.used",))
    assert middle != before
    (tmp_path / "packages" / "pkg" / "config" / "params.json").write_text('{"a": 1}')
    assert fingerprint(b"x", code=("pkg.used",)) != middle


def test_untraceable_import_falls_back_to_the_whole_packages(tmp_path, monkeypatch):
    src = _fake_repo(tmp_path, monkeypatch)
    (src / "helper.py").write_text("import importlib\n\ndef g(name):\n    return importlib.import_module(name)\n")
    assert fp.code_closure("pkg.used") is None
    before = fingerprint(b"x", code=("pkg.used",))
    (src / "unused.py").write_text("X = 2\n")
    assert fingerprint(b"x", code=("pkg.used",)) != before


def test_narrow_key_switched_off_hashes_whole_packages(tmp_path, monkeypatch):
    src = _fake_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(fp, "NARROW_FINGERPRINT", False)
    before = fingerprint(b"x", code=("pkg.used",))
    (src / "unused.py").write_text("X = 2\n")
    assert fingerprint(b"x", code=("pkg.used",)) != before


def test_whole_package_key_hashes_config(tmp_path, monkeypatch):
    _fake_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(fp, "NARROW_FINGERPRINT", False)
    before = fingerprint(b"x")
    (tmp_path / "packages" / "pkg" / "config" / "params.json").write_text('{"a": 1}')
    assert fingerprint(b"x") != before


def test_guard_fails_when_the_step_imports_outside_its_closure(tmp_path, monkeypatch):
    _fake_repo(tmp_path, monkeypatch)
    monkeypatch.syspath_prepend(str(tmp_path / "packages" / "pkg" / "src"))
    for m in [m for m in sys.modules if m == "pkg" or m.startswith("pkg.")]:
        monkeypatch.delitem(sys.modules, m)
    with fp.closure_guard(("pkg.used",)):
        import pkg.used
        pkg.used.f()
    try:
        with fp.closure_guard(("pkg.used",)):
            import pkg.unused  # noqa: F401
    except RuntimeError as e:
        assert "pkg.unused" in str(e)
    else:
        raise AssertionError("the guard let an import outside the closure through")


def test_chain_closures_trace():
    """The chain's two narrow keys trace (no dynamic import fell them back)."""
    sys.path.insert(0, str(REPO / "scripts"))
    for code in (("poverty_impact_report", "tax_modeler.pipeline"),
                 ("tax_modeler.simulator.golden", "tax_modeler.simulator.population")):
        closure = fp.code_closure(*code)
        assert closure is not None and len(closure) > 50


def test_narrow_key_is_on_unless_switched_off():
    import os
    import subprocess

    def on(env):
        out = subprocess.run([sys.executable, "-c", "import common.fingerprint as c; print(c.NARROW_FINGERPRINT)"],
                             cwd=REPO, env=env, capture_output=True, text=True, check=True)
        return out.stdout.strip() == "True"

    env = {k: v for k, v in os.environ.items() if k != "CF_NARROW_FINGERPRINT"}
    assert on(env)
    assert on({**env, "CF_NARROW_FINGERPRINT": "1"})
    assert not on({**env, "CF_NARROW_FINGERPRINT": "0"})


def _lazy_pkg(tmp_path, monkeypatch, step_source):
    """``lazypkg`` exports ``Thing`` lazily (a ``_LAZY`` map read by
    ``__getattr__``), as tax_modeler's ``__init__`` does."""
    src = tmp_path / "packages" / "lazypkg" / "src" / "lazypkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text(
        "import importlib\n"
        "_LAZY = {'Thing': '.thing'}\n"
        "def __getattr__(name):\n"
        "    if name in _LAZY:\n"
        "        return getattr(importlib.import_module(_LAZY[name], __name__), name)\n"
        "    return importlib.import_module(f'.{name}', __name__)\n")
    (src / "thing.py").write_text("class Thing:\n    pass\n")
    (src / "other.py").write_text("X = 1\n")
    (src / "sub.py").write_text("Y = 1\n")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "step.py").write_text(step_source)
    monkeypatch.setattr(fp, "REPO", tmp_path)
    return {f.name for f in fp.code_closure(tmp_path / "scripts" / "step.py") or []}


def test_closure_follows_a_lazy_export(tmp_path, monkeypatch):
    names = _lazy_pkg(tmp_path, monkeypatch, "from lazypkg import Thing\n")
    assert names == {"step.py", "__init__.py", "thing.py"}


def test_closure_follows_a_lazy_export_by_attribute(tmp_path, monkeypatch):
    names = _lazy_pkg(tmp_path, monkeypatch, "import lazypkg as lp\n\ndef f():\n    return lp.Thing, lp.sub.Y\n")
    assert names == {"step.py", "__init__.py", "thing.py", "sub.py"}


def test_closure_star_import_takes_every_lazy_export(tmp_path, monkeypatch):
    names = _lazy_pkg(tmp_path, monkeypatch, "from lazypkg import *\n")
    assert "thing.py" in names and "other.py" not in names


def test_lazy_getattr_is_not_an_untraceable_import(tmp_path, monkeypatch):
    """The ``import_module`` in a lazy package's own ``__getattr__`` is
    covered by its ``_LAZY`` map, so the closure still traces."""
    assert _lazy_pkg(tmp_path, monkeypatch, "import lazypkg\n")


def test_chain_closures_skip_the_lazy_init_exports():
    """With tax_modeler's and its calibration's ``__init__`` lazy, the chain's
    keys leave out calibration modules neither build uses."""
    sys.path.insert(0, str(REPO / "scripts"))
    closure = {f.name for f in fp.code_closure(
        "tax_modeler.simulator.golden", "tax_modeler.simulator.population")}
    assert "irs_bracket_calibration.py" not in closure


def test_repo_root_is_checked_at_import_and_can_be_overridden(tmp_path):
    import os
    import subprocess

    def run(root):
        env = {**os.environ, "CF_REPO_ROOT": str(root)}
        return subprocess.run([sys.executable, "-c", "import common.fingerprint as c; print(c.REPO)"],
                              env=env, capture_output=True, text=True)

    bad = run(tmp_path)
    assert bad.returncode != 0 and "not the Census-Forecaster repo root" in bad.stderr
    (tmp_path / "packages").mkdir()
    (tmp_path / "pyproject.toml").write_text("")
    good = run(tmp_path)
    assert good.returncode == 0 and good.stdout.strip() == str(tmp_path.resolve())
    assert (REPO / "pyproject.toml").exists() and (REPO / "packages").is_dir()
