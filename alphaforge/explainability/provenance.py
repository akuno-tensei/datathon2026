"""Contrôle structurel de provenance : chaque mot de l'explication doit venir d'un gabarit de rédaction ou du registre.

Le contrôle de ton (`verifier.advice_wording`) est une liste de mots interdits : une tournure absente de la liste, une faute
de frappe ou un caractère d'apparence identique (« аchetez » avec un « а » cyrillique) passe. Ce contrôle-ci prend le
problème à l'envers : il ne cherche pas ce qui est interdit, il exige que tout soit autorisé.

Vocabulaire autorisé, construit pour chaque explication :
- les gabarits de rédaction de la langue (`config.TEMPLATES`) ; un emplacement `{...}` devient un jeton générique « § » ;
- les chaînes du registre (messages de la gouvernance, hypothèses de l'analyste, noms d'actifs...), SANS le texte libre de la
  demande : sinon un utilisateur qui écrit « vous devriez acheter » ferait entrer ces mots dans le vocabulaire.

Règles, appliquées à un énoncé :
1. chaque mot hors citation appartient au vocabulaire ;
2. chaque paire de mots consécutifs (dans un même segment entre deux signes de ponctuation) existe dans un gabarit ou dans le
   registre, ou suit/précède un emplacement d'un gabarit ;
3. chaque citation entre guillemets reprend une chaîne du registre, une chaîne déclarée ou un texte de gabarit.
Les nombres et les chaînes déclarées (`Statement.literals`, `Figure.shown`) valent « § » : leur exactitude est contrôlée ailleurs.

Limite : la règle 2 tolère n'importe quel mot du vocabulaire juste après ou juste avant un emplacement. Un assemblage de mots
tous autorisés, dans un ordre que les gabarits permettent, n'est pas détecté. Les deux contrôles restent donc actifs ensemble :
la liste de mots interdits « dit », la provenance « bloque ».
"""
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Set, Tuple

SLOT = "§"
_PLACEHOLDER = re.compile(r"\{[^{}]*\}")
_QUOTED = re.compile(r"«\s*([^»]*?)\s*»|“\s*([^”]*?)\s*”|\"\s*([^\"]*?)\s*\"")
_BREAK = re.compile(r"[.,;:!?()\[\]\n—–/=+×÷*^<>|→]+")  # la ponctuation coupe les paires : deux gabarits se suivent sans paire commune
_TOKEN = re.compile(r"\S*\d\S*|§|['’]s\b|[^\W\d_]+(?:['’·][^\W\d_]+)*", re.U)
_SYMBOL = re.compile(r"\b[A-Z0-9]+(?:[.\-][A-Z0-9]+)+\b")  # LOW.TO, BTC-USD : un symbole composé est un seul jeton
_POSSESSIVE = re.compile(r"(?<=\w|§)['’]s\b")  # « NVDA's » et « {t}'s » : le possessif devient un mot à part


def _tokens(segment: str) -> List[str]:
    out = []
    for t in _TOKEN.findall(segment):
        out.append(SLOT if (t == SLOT or any(c.isdigit() for c in t)) else t.lower())
    return out


def _segments(text: str) -> List[List[str]]:
    text = _POSSESSIVE.sub(" 's", _SYMBOL.sub(lambda m: m.group(0).replace(".", "·").replace("-", "·"), text))
    return [toks for seg in _BREAK.split(text) if (toks := _tokens(seg))]


def _strip_quotes(text: str) -> str:
    return _QUOTED.sub(f" {SLOT} ", text)


def _string_leaves(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _string_leaves(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _string_leaves(v)


@dataclass
class Vocabulary:
    words: Set[str] = field(default_factory=set)
    pairs: Set[Tuple[str, str]] = field(default_factory=set)
    before_slot: Set[str] = field(default_factory=set)  # mots suivis d'un emplacement dans un gabarit
    after_slot: Set[str] = field(default_factory=set)  # mots précédés d'un emplacement dans un gabarit
    quotable: Set[str] = field(default_factory=set)  # textes qu'une citation peut reprendre
    atoms: Set[str] = field(default_factory=set)  # valeurs d'un seul mot du registre (symboles, codes) : peuvent entourer un nombre
    corpus: str = ""

    def _learn(self, text: str, from_template: bool) -> None:
        for m in _QUOTED.finditer(text):
            inner = next(g for g in m.groups() if g is not None)
            if inner and not _PLACEHOLDER.search(inner):
                self.quotable.add(inner)
        body = _strip_quotes(_PLACEHOLDER.sub(f" {SLOT} ", text) if from_template else text)
        for toks in _segments(body):
            self.words.update(t for t in toks if t != SLOT)
            for a, b in zip(toks, toks[1:]):
                self.pairs.add((a, b))
                if from_template and b == SLOT and a != SLOT:
                    self.before_slot.add(a)
                if from_template and a == SLOT and b != SLOT:
                    self.after_slot.add(b)

    @classmethod
    def build(cls, templates: Any, corpus_strings: Iterable[str]) -> "Vocabulary":
        v = cls()
        for s in _string_leaves(templates):
            v._learn(s, from_template=True)
            if not _PLACEHOLDER.search(s):
                v.quotable.add(s)  # une valeur de gabarit (statut, intention...) peut être citée
        strings = list(corpus_strings)
        for s in strings:
            v._learn(s, from_template=False)
            toks = [t for seg in _segments(s) for t in seg]
            if len(toks) == 1 and toks[0] != SLOT:
                v.atoms.add(toks[0])
        v.corpus = "\n".join(strings)
        return v


def _mask(text: str, literals: Iterable[str]) -> str:
    for lit in sorted({x for x in literals if x}, key=len, reverse=True):
        text = text.replace(lit, f" {SLOT} ")
    return text


def provenance_issues(text: str, literals: Iterable[str], vocab: Vocabulary) -> List[str]:
    """Mots, paires et citations de `text` sans provenance. Liste vide : tout l'énoncé vient d'un gabarit ou du registre."""
    lits = list(literals)
    problems: List[str] = []
    for m in _QUOTED.finditer(text):
        inner = next(g for g in m.groups() if g is not None)
        if not inner or inner in vocab.quotable or inner in lits or inner in vocab.corpus:
            continue
        # Citation composée (« sector: Technologie ») : acceptée si chacun de ses mots est connu ; jamais un mot nouveau.
        unknown = [t for seg in _segments(_mask(inner, lits)) for t in seg if t != SLOT and t not in vocab.words and t not in vocab.atoms]
        if unknown:
            problems.append(f"citation sans source : {m.group(0)!r}")
    body = _strip_quotes(_mask(text, lits))
    for toks in _segments(body):
        for t in toks:
            if t != SLOT and t not in vocab.words:
                problems.append(f"mot hors gabarit et hors registre : {t!r}")
        for a, b in zip(toks, toks[1:]):
            if SLOT in (a, b) and (a == b or (a in vocab.before_slot) or (b in vocab.after_slot) or (a, b) in vocab.pairs
                                   or (a if b == SLOT else b) in vocab.atoms):  # « SPY 60 % » : un symbole suivi de son poids
                continue
            if (a, b) not in vocab.pairs and a not in vocab.before_slot and b not in vocab.after_slot:
                problems.append(f"enchaînement absent des gabarits et du registre : {a!r} {b!r}")
    return list(dict.fromkeys(problems))
