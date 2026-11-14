"""Agent explicabilité : rédaction à partir du seul registre de preuves, vérification de chaque chiffre, limites, étiquettes."""
from .compose import compose_explanation, render
from .config import ExplainConfig
from .explainer import Explainer
from .label_content import label_content
from .ledger_read import LedgerView, ledger_read
from .limits import limits_builder
from .models import Explanation, Figure, Limits, Statement, VerificationReport
from .verifier import claim_verifier

__all__ = [
    "ExplainConfig", "Explainer", "Explanation", "Figure", "LedgerView", "Limits", "Statement", "VerificationReport",
    "claim_verifier", "compose_explanation", "label_content", "ledger_read", "limits_builder", "render",
]
