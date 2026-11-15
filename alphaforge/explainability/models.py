"""Modèles typés de l'explicabilité : énoncés étiquetés, chiffres rattachés à une preuve, rapport de vérification."""
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Label = Literal["fait", "hypothese", "scenario_fictif"]
Basis = Literal["evidence", "assumption", "scenario"]  # origine d'un énoncé ; `label_content` en déduit l'étiquette
Unit = Literal["pct", "years", "ratio", "corr"]  # corr : coefficient de corrélation, deux décimales, signe conservé


class Figure(StrictModel):
    """Un nombre affiché dans un énoncé. `ref` désigne l'entrée du registre (evidence_id ou `seq:N`) qui le contient."""

    value: float
    unit: Unit
    shown: str  # texte exact inséré dans l'énoncé
    ref: str


class Statement(StrictModel):
    sid: str
    section: str
    text: str
    basis: Basis = "evidence"
    label: Optional[Label] = None
    refs: List[str] = []  # evidence_id ou `seq:N`
    figures: List[Figure] = []
    literals: List[str] = []  # chaînes à chiffres autorisées dans le texte (tickers, fenêtres), vérifiées contre le registre
    note: Optional[str] = None  # ex. `downgraded_no_evidence`


class Issue(StrictModel):
    sid: str
    code: str  # unknown_ref, no_evidence, figure_mismatch, figure_format, unsupported_number, unknown_literal, unlabeled, advice_wording, unsourced_wording
    message: str


class VerificationReport(StrictModel):
    approved: bool
    attempts: int = 1
    verified: int = 0
    removed: List[str] = []
    downgraded: List[str] = []
    issues: List[Issue] = []


LIMIT_SECTIONS = ("assumptions", "limits", "uncertainties", "not_covered")


class Limits(StrictModel):
    assumptions: List[Statement] = []
    limits: List[Statement] = []
    uncertainties: List[Statement] = []
    not_covered: List[Statement] = []

    def statements(self) -> List[Statement]:
        return self.assumptions + self.limits + self.uncertainties + self.not_covered

    @classmethod
    def from_statements(cls, statements: List[Statement]) -> "Limits":
        groups: Dict[str, List[Statement]] = {k: [] for k in LIMIT_SECTIONS}
        for s in statements:
            if s.section in groups:
                groups[s.section].append(s)
        return cls(**groups)


class Explanation(StrictModel):
    language: str
    experience: str
    statements: List[Statement]  # énoncés du corps (hors limites), vérifiés
    limits: Limits
    report: VerificationReport
    text: str
