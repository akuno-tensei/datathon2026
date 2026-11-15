"""Rechargement à chaud : quand le code du projet change, l'interface repart des modules à jour.

Streamlit relit le script de l'interface à chaque action, mais garde en mémoire les modules déjà importés et les objets mis en
cache. Après une modification de `pipeline.py` (ou de n'importe quel agent), un serveur resté ouvert exécutait donc le nouveau
script avec l'ancienne classe, d'où des erreurs du type « 'AlphaForge' object has no attribute '...' ».

`refresh` compare l'empreinte du code (date de modification la plus récente des fichiers `.py`) à celle du dernier appel. Si elle
a changé, il oublie les modules du paquet pour qu'ils soient réimportés à jour ; l'empreinte sert aussi de clé au cache de
l'instance (`forge(stamp)`). Sans modification du code, il ne fait rien.
"""
import sys
from pathlib import Path
from typing import Dict

PACKAGE = "alphaforge"
# Ces modules restent en place : `alphaforge` et `alphaforge.app` n'ont pas d'état, et ce module-ci garde la mémoire des empreintes.
KEEP = {PACKAGE, f"{PACKAGE}.app", f"{PACKAGE}.app._reload"}

_stamps: Dict[str, float] = {}


def code_stamp(root: Path) -> float:
    """Date de modification la plus récente des fichiers Python sous `root`."""
    return max((p.stat().st_mtime for p in Path(root).rglob("*.py") if "__pycache__" not in p.parts), default=0.0)


def refresh(root: Path, package: str = PACKAGE, keep: frozenset = frozenset(KEEP), purge: bool = True) -> float:
    """Retourne l'empreinte courante du code ; purge les modules du paquet si elle a changé depuis le dernier appel.

    `purge=False` ne fait que mesurer : à réserver aux processus où d'autres objets gardent des références aux modules déjà
    importés (suite de tests), qu'une purge en cours de route rendrait incohérents.
    """
    stamp = code_stamp(root)
    key = f"{package}:{Path(root).resolve()}"
    previous = _stamps.get(key)
    if purge and previous is not None and previous != stamp:
        for name in [n for n in sys.modules if (n == package or n.startswith(package + ".")) and n not in keep]:
            del sys.modules[name]
    _stamps[key] = stamp
    return stamp
