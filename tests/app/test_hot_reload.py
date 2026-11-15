"""Rechargement à chaud : un serveur resté ouvert ne doit pas exécuter un nouveau script avec d'anciens modules."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from alphaforge.app import _reload

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def pkg(tmp_path, monkeypatch):
    """Petit paquet jetable `hotpkg`, importable, dont on peut réécrire un module."""
    base = tmp_path / "hotpkg"
    base.mkdir()
    (base / "__init__.py").write_text("")
    (base / "mod.py").write_text("VERSION = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield base
    for name in [n for n in sys.modules if n == "hotpkg" or n.startswith("hotpkg.")]:
        del sys.modules[name]
    _reload._stamps.clear()


def _bump(path: Path, text: str, offset: float) -> None:
    import os

    path.write_text(text)
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + offset))  # mtime strictement plus récent, sans dépendre de l'horloge


def test_unchanged_code_keeps_loaded_modules(pkg):
    import hotpkg.mod as mod

    stamp = _reload.refresh(pkg, "hotpkg", frozenset())
    assert _reload.refresh(pkg, "hotpkg", frozenset()) == stamp
    assert sys.modules["hotpkg.mod"] is mod  # rien ne change : aucun rechargement inutile


def test_changed_code_makes_the_next_import_fresh(pkg):
    import hotpkg.mod as old

    _reload.refresh(pkg, "hotpkg", frozenset())
    _bump(pkg / "mod.py", "VERSION = 2\nNEW = True\n", offset=10)
    _reload.refresh(pkg, "hotpkg", frozenset())
    assert "hotpkg.mod" not in sys.modules
    import hotpkg.mod as new

    assert new is not old and new.VERSION == 2 and new.NEW is True and old.VERSION == 1


def test_stamp_changes_with_any_source_file_and_ignores_caches(pkg):
    s0 = _reload.code_stamp(pkg)
    (pkg / "__pycache__").mkdir()
    _bump(pkg / "__pycache__" / "x.py", "#", offset=100)  # un cache ne compte pas
    assert _reload.code_stamp(pkg) == s0
    _bump(pkg / "new_module.py", "#", offset=100)
    assert _reload.code_stamp(pkg) > s0


def test_kept_modules_and_other_packages_are_not_purged(pkg, tmp_path, monkeypatch):
    other = tmp_path / "otherpkg"
    other.mkdir()
    (other / "__init__.py").write_text("")
    import hotpkg  # noqa: F401
    import otherpkg  # noqa: F401

    _reload.refresh(pkg, "hotpkg", frozenset({"hotpkg"}))
    _bump(pkg / "mod.py", "VERSION = 3\n", offset=10)
    _reload.refresh(pkg, "hotpkg", frozenset({"hotpkg"}))
    assert "hotpkg" in sys.modules and "otherpkg" in sys.modules  # le paquet gardé et un autre paquet restent intacts
    sys.modules.pop("otherpkg", None)


def test_the_application_never_reuses_a_stale_class_after_the_code_changed():
    """Scénario de l'erreur « 'AlphaForge' object has no attribute 'profile_origin' », dans un processus séparé.

    Témoin : sans changement de code, la classe périmée est bien réutilisée et l'erreur se produit (c'est le bug d'origine).
    Correctif : si l'empreinte du code change, les modules sont réimportés et l'application repart d'une classe à jour.
    """
    code = textwrap.dedent('''
        import logging
        logging.disable(logging.CRITICAL)
        from streamlit.testing.v1 import AppTest
        import alphaforge.app._reload as r
        import alphaforge.app.pipeline as p

        at = AppTest.from_file("alphaforge/app/streamlit_app.py", default_timeout=60)
        at.run()
        assert not at.exception, at.exception

        del p.AlphaForge.profile_origin            # classe périmée, comme un serveur démarré avant la modification
        at.run()
        assert at.exception and "profile_origin" in at.exception[0].value, "témoin : l'erreur d'origine doit se reproduire"

        original = r.code_stamp
        r.code_stamp = lambda root: original(root) + 1   # le code du projet a changé
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        import sys
        assert hasattr(sys.modules["alphaforge.app.pipeline"].AlphaForge, "profile_origin")
        print("OK")
    ''')
    env = {**os.environ, "ALPHAFORGE_HOT_RELOAD": "1"}  # la suite le désactive ; ce test, isolé dans son processus, l'active
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=240, env=env)
    assert done.returncode == 0 and "OK" in done.stdout, done.stdout[-600:] + done.stderr[-1500:]


def test_purge_can_be_disabled_for_processes_that_hold_module_references(pkg):
    import hotpkg.mod as mod

    _reload.refresh(pkg, "hotpkg", frozenset())
    _bump(pkg / "mod.py", "VERSION = 9\n", offset=10)
    changed = _reload.refresh(pkg, "hotpkg", frozenset(), purge=False)
    assert sys.modules["hotpkg.mod"] is mod and changed > 0  # l'empreinte est mesurée, rien n'est purgé


def test_the_test_suite_runs_with_hot_reload_disabled():
    assert os.environ.get("ALPHAFORGE_HOT_RELOAD") == "0"


def test_from_kept_package_import_submodule_gets_the_new_module(pkg):
    """Cause de « module 'alphaforge.app.views' has no attribute 'conclusion_lines' » : le paquet gardé gardait l'ancien
    sous-module en attribut, et `from paquet import module` le relisait au lieu de réimporter la version à jour."""
    from hotpkg import mod as old

    _reload.refresh(pkg, "hotpkg", frozenset({"hotpkg"}))
    _bump(pkg / "mod.py", "VERSION = 4\nADDED = True\n", offset=10)
    _reload.refresh(pkg, "hotpkg", frozenset({"hotpkg"}))
    from hotpkg import mod as new

    assert new is not old and new.VERSION == 4 and new.ADDED is True
