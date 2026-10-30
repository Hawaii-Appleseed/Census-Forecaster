"""_forecast_common.fingerprint, the key of the chain's cached step outputs."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from _forecast_common import fingerprint  # noqa: E402


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
    import _forecast_common

    seen = []
    real = Path.rglob

    def spy(self, pattern):
        seen.append(self)
        return real(self, pattern)

    monkeypatch.setattr(Path, "rglob", spy)
    _forecast_common.fingerprint(b"x")
    hashed = {p.relative_to(REPO).as_posix() for p in seen}
    for src in (REPO / "packages").glob("*/src"):
        assert src.relative_to(REPO).as_posix() in hashed


# -- the narrow key (fingerprint(code=...); CF_NARROW_FINGERPRINT=0 turns it off)

def _fake_repo(tmp_path, monkeypatch):
    """A workspace with one package: ``pkg.used`` imports ``pkg.helper``;
    ``pkg.unused`` is imported by nothing; a step script imports ``pkg.used``."""
    import _forecast_common

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
    (tmp_path / "step.py").write_text("import pkg.used\n")
    monkeypatch.setattr(_forecast_common, "REPO", tmp_path)
    monkeypatch.setattr(_forecast_common, "NARROW_FINGERPRINT", True)
    return src


def test_closure_follows_imports_inside_functions_and_packages(tmp_path, monkeypatch):
    import _forecast_common

    src = _fake_repo(tmp_path, monkeypatch)
    names = {f.name for f in _forecast_common.code_closure(tmp_path / "step.py")}
    assert names == {"step.py", "__init__.py", "used.py", "helper.py"}
    assert {f.name for f in _forecast_common.code_closure("pkg.used")} == \
        {"__init__.py", "used.py", "helper.py"}
    assert (src / "unused.py") not in _forecast_common.code_closure("pkg.used")


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
    import _forecast_common

    src = _fake_repo(tmp_path, monkeypatch)
    (src / "helper.py").write_text("import importlib\n\ndef g(name):\n    return importlib.import_module(name)\n")
    assert _forecast_common.code_closure("pkg.used") is None
    before = fingerprint(b"x", code=("pkg.used",))
    (src / "unused.py").write_text("X = 2\n")
    assert fingerprint(b"x", code=("pkg.used",)) != before


def test_narrow_key_switched_off_hashes_whole_packages(tmp_path, monkeypatch):
    import _forecast_common

    src = _fake_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(_forecast_common, "NARROW_FINGERPRINT", False)
    before = fingerprint(b"x", code=("pkg.used",))
    (src / "unused.py").write_text("X = 2\n")
    assert fingerprint(b"x", code=("pkg.used",)) != before


def test_whole_package_key_hashes_config(tmp_path, monkeypatch):
    import _forecast_common

    _fake_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(_forecast_common, "NARROW_FINGERPRINT", False)
    before = fingerprint(b"x")
    (tmp_path / "packages" / "pkg" / "config" / "params.json").write_text('{"a": 1}')
    assert fingerprint(b"x") != before


def test_guard_fails_when_the_step_imports_outside_its_closure(tmp_path, monkeypatch):
    import _forecast_common

    _fake_repo(tmp_path, monkeypatch)
    monkeypatch.syspath_prepend(str(tmp_path / "packages" / "pkg" / "src"))
    for m in [m for m in sys.modules if m == "pkg" or m.startswith("pkg.")]:
        monkeypatch.delitem(sys.modules, m)
    with _forecast_common.closure_guard(("pkg.used",)):
        import pkg.used
        pkg.used.f()
    try:
        with _forecast_common.closure_guard(("pkg.used",)):
            import pkg.unused  # noqa: F401
    except RuntimeError as e:
        assert "pkg.unused" in str(e)
    else:
        raise AssertionError("the guard let an import outside the closure through")


def test_chain_closures_trace():
    """The chain's two narrow keys trace (no dynamic import fell them back)."""
    import _forecast_common

    sys.path.insert(0, str(REPO / "scripts"))
    for code in (("poverty_impact_report", "tax_modeler.pipeline"),
                 ("tax_modeler.simulator.golden", "tax_modeler.simulator.population")):
        closure = _forecast_common.code_closure(*code)
        assert closure is not None and len(closure) > 50


def test_narrow_key_is_on_unless_switched_off():
    import os
    import subprocess

    def on(env):
        out = subprocess.run([sys.executable, "-c", "import _forecast_common as c; print(c.NARROW_FINGERPRINT)"],
                             cwd=REPO, env=env, capture_output=True, text=True, check=True)
        return out.stdout.strip() == "True"

    env = {k: v for k, v in os.environ.items() if k != "CF_NARROW_FINGERPRINT"}
    assert on(env)
    assert on({**env, "CF_NARROW_FINGERPRINT": "1"})
    assert not on({**env, "CF_NARROW_FINGERPRINT": "0"})
