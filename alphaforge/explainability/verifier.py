"""Outil `claim_verifier` : chaque nombre et chaque affirmation doit correspondre à une entrée du registre.

Contrôles par énoncé :
1. toute référence existe dans le registre ;
2. un énoncé « fait » cite au moins une référence valide ;
3. chaque `Figure` se retrouve (en valeur absolue) parmi les nombres de l'entrée citée, et son texte affiché est l'arrondi de cette valeur ;
4. le texte ne contient aucun autre nombre : après retrait des chiffres déclarés, des chaînes autorisées et des dates, il ne reste aucun chiffre ;
5. les chaînes autorisées et les dates du texte figurent dans le registre.

Issue : un énoncé fautif est retiré. Un énoncé « fait » sans preuve est rétrogradé en hypothèse s'il ne contient aucun chiffre, sinon retiré.
"""
import re
from typing import List, Tuple

from .formatting import parse_shown, shown_value
from .ledger_read import LedgerView, numeric_leaves
from .models import Issue, Statement, VerificationReport

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_REL_TOL = 1e-9


def _leaf_match(payload, value: float) -> bool:
    target = abs(value)
    return any(abs(abs(leaf) - target) <= _REL_TOL * max(1.0, target) for leaf in numeric_leaves(payload))


def _residual(s: Statement) -> str:
    text = _ISO_DATE.sub(" ", s.text)  # les dates d'abord : une fenêtre « 2022 » ne doit pas entamer « 2022-01-03 »
    for lit in sorted({_ISO_DATE.sub(" ", x) for x in s.literals}, key=len, reverse=True):
        text = text.replace(lit, " ")
    for shown in sorted({f.shown for f in s.figures}, key=len, reverse=True):
        text = re.sub(r"(?<![\d.,])" + re.escape(shown) + r"(?!\d|[.,]\d)", " ", text)
    return text


def check_statement(view: LedgerView, s: Statement) -> List[Issue]:
    issues: List[Issue] = []

    def bad(code: str, message: str) -> None:
        issues.append(Issue(sid=s.sid, code=code, message=message))

    if s.label is None:
        bad("unlabeled", "Énoncé sans étiquette.")
    unknown = [r for r in s.refs if not view.has_ref(r)]
    for r in unknown:
        bad("unknown_ref", f"Référence absente du registre : {r}.")
    if s.label == "fait" and not unknown and not s.refs:
        bad("no_evidence", "Énoncé étiqueté « fait » sans preuve citée.")

    for f in s.figures:
        payload = view.refs.get(f.ref)
        if payload is None:
            bad("unknown_ref", f"Chiffre {f.shown} : référence absente du registre ({f.ref}).")
            continue
        if not _leaf_match(payload, f.value):
            bad("figure_mismatch", f"Chiffre {f.shown} : la valeur {f.value} ne figure pas dans l'entrée {f.ref}.")
        try:
            ok = abs(parse_shown(f.shown) - shown_value(f.value, f.unit)) < 1e-9
        except ValueError:
            ok = False
        if not ok:
            bad("figure_format", f"Chiffre {f.shown} : ne correspond pas à la valeur {f.value} arrondie.")
        if f.shown not in s.text:
            bad("figure_format", f"Chiffre {f.shown} déclaré mais absent du texte.")

    for lit in s.literals:
        if lit not in view.corpus:
            bad("unknown_literal", f"Chaîne absente du registre : {lit!r}.")
    for d in _ISO_DATE.findall(s.text):
        if d not in view.corpus:
            bad("unknown_literal", f"Date absente du registre : {d}.")
    if re.search(r"\d", _residual(s)):
        bad("unsupported_number", "Le texte contient un nombre qui n'est rattaché à aucune preuve.")
    return issues


def claim_verifier(view: LedgerView, statements: List[Statement], attempts: int = 1) -> Tuple[List[Statement], VerificationReport]:
    kept: List[Statement] = []
    removed: List[str] = []
    downgraded: List[str] = []
    issues: List[Issue] = []
    for s in statements:
        found = check_statement(view, s)
        issues += found
        hard = [i for i in found if i.code != "no_evidence"]
        if hard:
            removed.append(s.sid)
        elif found:  # seul manque : la preuve
            if s.figures or re.search(r"\d", s.text):
                removed.append(s.sid)
            else:
                downgraded.append(s.sid)
                kept.append(s.model_copy(update={"label": "hypothese", "note": "downgraded_no_evidence"}))
        else:
            kept.append(s)
    report = VerificationReport(
        approved=not issues, attempts=attempts, verified=len(kept) - len(downgraded), removed=removed, downgraded=downgraded, issues=issues
    )
    return kept, report
