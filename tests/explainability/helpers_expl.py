"""Aides partagées des tests d'explicabilité (nom unique : pas de __init__.py dans tests/)."""
from alphaforge.explainability import Explainer
from alphaforge.orchestrator.tools import load_profile


def explain(result, **over):
    """Ré-exécute l'explicabilité sur le registre d'un run, avec un profil éventuellement modifié (langue, expérience)."""
    profile = load_profile({**result.ledger.entries("profile")[0].payload["profile"], **over})
    return Explainer().explain(profile, result.ledger)


def body_lines(text):
    return [line for line in text.splitlines() if line.startswith("- [")]
