# Paquet `alphaforge`

Ce paquet contient le code d'AlphaForge AI, une simulation d'aide à la décision d'investissement (Python 3.9). Ce document est une carte : il dit à quoi sert chaque sous-paquet, dans quel ordre ils interviennent pendant une analyse, qui écrit et qui lit le registre de preuves, et comment lancer une analyse en Python. Le détail de chaque sous-paquet se trouve dans son propre README.

Contexte global, à ne pas chercher ici : [`../README.md`](../README.md) (démarrage, données, tests, AWS) et [`../Agents.md`](../Agents.md) (rôle, outils et garde-fous de chaque agent, boucles de retour).

## Les sous-paquets

| Sous-paquet | Rôle | Point d'entrée principal | Documentation |
|---|---|---|---|
| `quant/` | Agent 2, analyste quantitatif. Outils déterministes (huit, voir `quant.TOOLS`) qui calculent et renvoient des `Evidence`. Aucun appel à un modèle de langage. | `DataStore`, `run_tool`, `TOOLS` | [`quant/README.md`](quant/README.md) |
| `orchestrator/` | Agent 1. Charge et valide le profil, interprète la demande, planifie, appelle les autres agents, tient le registre de preuves (`Ledger`). | `Orchestrator`, `QuantAnalyst`, `load_profile` | [`orchestrator/README.md`](orchestrator/README.md) |
| `enrichment/` | Agent 2.1, facultatif. Va chercher des données publiques absentes du jeu de départ, les date, les source et les écrit au registre. | `EnrichmentAgent`, `detect_needs` | [`enrichment/README.md`](enrichment/README.md) |
| `governance/` | Agent 3. Dérive des limites du profil, les compare aux preuves, rend un verdict, demande des alternatives à chiffrer. | `Governance` | [`governance/README.md`](governance/README.md) |
| `explainability/` | Agent 4. Rédige l'explication à partir du seul registre et vérifie chaque chiffre. | `Explainer` | [`explainability/README.md`](explainability/README.md) |
| `app/` | Assemblage des agents (`pipeline.py`), vues testables, interface Streamlit, archivage S3 facultatif. | `AlphaForge` (dans `app/pipeline.py`) | [`app/README.md`](app/README.md) |

Dépendances entre sous-paquets (lues dans les `import` du code) :
- `orchestrator/` importe `quant/` (pour `DataStore`, `Evidence`, `run_tool`, `TOOLS`).
- `governance/`, `explainability/` et `enrichment/` importent les modèles et le registre de `orchestrator/` (`Ledger`, `InvestorProfile`, `ParsedRequest`). `governance/` importe aussi `GovernanceOutcome` et `ToolCall` depuis `orchestrator/orchestrator.py`.
- `enrichment/` importe aussi `quant/datastore.py` (`DataStore`).
- `app/pipeline.py` importe tous les autres.
- `orchestrator/orchestrator.py` n'importe ni `governance/` ni `explainability/` ni `enrichment/` : ces agents lui sont injectés au constructeur.

## Ce qui se passe pendant une analyse

`AlphaForge.analyze(...)` (`app/pipeline.py`) assemble les agents, puis appelle `Orchestrator.run(...)`. Dans `run`, l'ordre est le suivant :

1. **Profil** : `load_profile` puis `validate_profile`. Une entrée `profile` est écrite. Un profil introuvable, illisible, non conforme ou contradictoire arrête l'exécution (`status="profile_invalid"`).
2. **Demande** : le parseur (règles, ou LLM avec repli sur les règles) produit un `ParsedRequest`. Une entrée `request` est écrite. Sans actif nommé, l'exécution s'arrête avec `status="needs_clarification"`.
3. **Plan** : `build_plan` construit un plan déterministe. Une entrée `plan` est écrite.
4. **Enrichissement** (si l'agent est fourni, c'est-à-dire avec `enrichment=True`) : l'orchestrateur appelle `detect_needs`. S'il trouve des besoins comblables, l'agent d'enrichissement écrit données, lacunes et fiches source au registre. Sans besoin, rien n'est écrit.
5. **Analyste** : chaque étape `analyste` du plan appelle un outil de `quant/`. L'`Evidence` obtenue est écrite au registre, puis une entrée `step` la référence.
6. **Gouvernance** : elle lit le registre et renvoie un `GovernanceOutcome` (verdict et éventuelles demandes de chiffrage). L'orchestrateur écrit le verdict. S'il y a des demandes, l'analyste les exécute et la gouvernance est rappelée, avec une limite de deux retours (`MAX_FEEDBACK_ITERATIONS`, `orchestrator/orchestrator.py`).
7. **Relance de l'enrichissement** (une seule) pour les alternatives que l'analyste a chiffrées, puis nouvel appel de la gouvernance si de nouvelles données ou lacunes ont été écrites.
8. **Explicabilité** : l'explicateur lit le registre, rédige, vérifie, et écrit une note de vérification. Le texte devient `RunResult.answer`.

Le détail de chaque étape, les notes écrites (`needs_detected`, `feedback_limit_reached`, etc.) et les statuts de `RunResult` sont dans [`orchestrator/README.md`](orchestrator/README.md). Quand la gouvernance ou l'explicabilité n'est pas fournie, le résultat est `status="partial"` et `pending_agents` liste ce qui manque.

## Le registre de preuves : qui écrit, qui lit

Le registre est un objet `Ledger` créé à chaque exécution (`orchestrator/ledger.py`). Il est en ajout seul, ordonné par un compteur `seq` (aucune horloge système). Chaque entrée est une `LedgerEntry` avec un `kind`, un `agent` et un `payload`.

Qui écrit, d'après les appels à `ledger.write`, `write_evidence` et `ledger_write` du code :

| Entrée (`kind`) | Valeur de `agent` | Écrit par |
|---|---|---|
| `profile`, `request`, `plan`, `step`, `note` | `orchestrateur` | l'orchestrateur |
| `evidence` | `analyste` | l'orchestrateur, avec `Ledger.write_evidence`, à partir de l'`Evidence` renvoyée par `quant/` (l'analyste n'écrit pas lui-même dans le registre) |
| `verdict` | `gouvernance` | l'orchestrateur, qui recopie le verdict renvoyé par la gouvernance (la gouvernance n'écrit pas elle-même) |
| `enrichment`, `source_card` | `enrichissement` | l'agent d'enrichissement, directement (`enrichment/ledger_write.py`) |
| `note` (`explanation_verified` ou `explanation_repaired`) | `explicabilite` | l'explicabilité, directement (`explainability/explainer.py`) |

Qui lit :
- **Gouvernance** : `ledger.evidence()`, les entrées `enrichment` et `source_card`, et les notes `tool_unavailable` (`governance/governance.py`).
- **Explicabilité** : `ledger_read` (`explainability/ledger_read.py`) est son seul accès aux faits. Elle ne recalcule rien.
- **Enrichissement** : relit ses propres entrées `enrichment` pour ne pas écrire deux fois une même donnée (`enrichment/needs.py`, `enrichment/ledger_write.py`).
- **Application** (`app/views.py`, `app/archive.py`) : affichage de la trace, des preuves et des sources ; export du registre en JSONL.
- **Orchestrateur** : lit les preuves `compute_metrics` pour repérer les alternatives avant la relance de l'enrichissement.

Les preuves de l'analyste sont idempotentes : une preuve avec le même `evidence_id` n'est écrite qu'une fois. Le registre peut s'exporter avec `Ledger.to_jsonl(path)`.

## Lancer une analyse en Python

La classe `AlphaForge` de `alphaforge/app/pipeline.py` assemble le noyau. Les exemples ci-dessous ont été exécutés depuis la racine du dépôt avec `cd /workshop/datathon2026 && ALPHAFORGE_LLM=0 python3 -W ignore -c "…"`. `ALPHAFORGE_LLM=0` force l'interprétation par règles : aucun appel à Bedrock, aucun compte AWS requis.

### Analyse simple

```python
from alphaforge.app.pipeline import AlphaForge

af = AlphaForge.from_directory()  # charge alphaforge_starter/ ; l'analyse hors ligne fonctionne sans réseau
print([p.profile_id for p in af.list_profiles()])

an = af.analyze("Compare NVDA et BTC-USD", "amira", enrichment=False)
print(an.result.status, an.as_of, an.base_currency, an.verdict["status"])
print(sorted({(e.kind, e.agent) for e in an.result.ledger.entries()}))
print(an.explanation.text.splitlines()[0])
```

Sortie obtenue :

```
['amira', 'mateo', 'robert']
complete 2026-11-12 CAD conforme
[('evidence', 'analyste'), ('note', 'explicabilite'), ('plan', 'orchestrateur'), ('profile', 'orchestrateur'), ('request', 'orchestrateur'), ('step', 'orchestrateur'), ('verdict', 'gouvernance')]
# Explication AlphaForge (simulation, aucune transaction réelle)
```

`analyze` renvoie une `Analysis` : `result` (le `RunResult` de l'orchestrateur, avec `ledger`), `verdict` (le dernier verdict du registre, ou `None`), `explanation` (l'`Explanation` structurée), `profile`, `as_of`, `base_currency`, `enrichment_enabled` et `archive_notes`. Sans `as_of`, la date d'analyse est `store.last_date`.

### Scénario avec poids et hypothèse de rééquilibrage

Les poids sont des fractions et ne sont jamais normalisés. `weights` et `rebalancing` ajoutent à ce que le texte contient et prennent le dessus en cas de conflit. `compare` exécute la même demande avec deux profils.

```python
from alphaforge.app.pipeline import AlphaForge

af = AlphaForge.from_directory()
an = af.analyze("Évalue SPY et AGG", "robert", weights={"SPY": 0.6, "AGG": 0.4},
                rebalancing="buy_and_hold", enrichment=False)
print(an.result.request.intent, an.result.request.weights, an.result.request.rebalancing)
step = [s for s in an.result.plan.steps if s.action == "portfolio_metrics"][0]
print(step.arguments["weights_basis"], step.arguments["rebalancing"])

cmp = af.compare("Évalue NVDA", "amira", "robert")  # même demande, deux profils ; enrichissement activé par défaut
print(cmp.left.verdict["status"], cmp.right.verdict["status"])
```

```
build_scenario {'SPY': 0.6, 'AGG': 0.4} buy_and_hold
user buy_and_hold
conforme avertissement
```

### Avec l'agent d'enrichissement

`enrichment=True` est la valeur par défaut de `analyze`. Les sources viennent de `build_enrichment_agent` : SEC EDGAR en direct seulement si un identifiant est fourni (`sec_user_agent`, ou la variable `ALPHAFORGE_SEC_USER_AGENT`), sinon le cache local, puis l'instantané versionné `alphaforge_starter/enrichment_snapshot/`. L'exemple ci-dessous n'a donc pas fait d'appel réseau.

```python
from alphaforge.app.pipeline import AlphaForge

af = AlphaForge.from_directory()
an = af.analyze("Évalue AAPL", "mateo", enrichment=True)
print(an.result.status, an.enrichment_enabled)
print(sorted({(e.kind, e.agent) for e in an.result.ledger.entries()}))
print([e.payload["code"] for e in an.result.ledger.entries("note")])
```

```
complete True
[('enrichment', 'enrichissement'), ('evidence', 'analyste'), ('note', 'explicabilite'), ('note', 'orchestrateur'), ('plan', 'orchestrateur'), ('profile', 'orchestrateur'), ('request', 'orchestrateur'), ('source_card', 'enrichissement'), ('step', 'orchestrateur'), ('verdict', 'gouvernance')]
['needs_detected', 'enrichment_done', 'needs_relaunched', 'enrichment_done', 'explanation_verified']
```

### Paramètres de `analyze`

`analyze(request_text, profile, as_of=None, base_currency=None, language=None, weights=None, rebalancing=None, on_entry=None, enrichment=True, sec_user_agent=None)`.
- `profile` : identifiant d'un profil d'exemple ou créé, `dict`, ou `InvestorProfile`.
- `base_currency` et `language` s'appliquent à une copie du profil pour cette analyse seulement.
- `on_entry` reçoit chaque `LedgerEntry` au moment de son écriture (trace en direct).

### Variables d'environnement lues par `app/`

| Variable | Effet | Fichier |
|---|---|---|
| `ALPHAFORGE_LLM` | `0` désactive le LLM : interprétation par règles. | `app/pipeline.py` (`build_parser`) |
| `ALPHAFORGE_LLM_MODEL`, `AWS_REGION`, `AWS_DEFAULT_REGION` | Modèle Bedrock et région du `BedrockClient`. | `app/pipeline.py` |
| `ALPHAFORGE_SEC_USER_AGENT` | Identification SEC EDGAR pour l'enrichissement en direct. | `app/pipeline.py` |
| `ALPHAFORGE_S3_BUCKET` | Active l'archivage du registre dans Amazon S3. | `app/archive.py` |

Sans identifiants AWS, `build_parser` choisit les règles même si `ALPHAFORGE_LLM` n'est pas `0`.

## Où modifier quoi

- Ajouter un persona : déposer un JSON dans `orchestrator/profiles/`, ou le créer depuis l'interface (enregistré dans `app/profiles/user/`). Aucun code à changer (`orchestrator/tools.py`, `app/pipeline.py`).
- Ajouter un outil de calcul : `quant/tools/` et son enregistrement dans `quant/registry.py`. L'orchestrateur ne l'appelle que si le plan le prévoit.
- Changer les limites de risque ou les contrôles : `governance/`.
- Changer le texte ou la vérification de l'explication : `explainability/`.
- Changer l'interface : `app/` (`views.py` pour la logique d'affichage, testable sans Streamlit ; `streamlit_app.py` pour le rendu).

## Tests

Les tests vivent hors du paquet, dans [`../tests/`](../tests/), un dossier par sous-paquet (`quant`, `orchestrator`, `enrichment`, `governance`, `explainability`, `app`) plus `integration`. La commande `python3 -m pytest` depuis la racine les lance tous ; le détail, dossier par dossier, est dans [`../tests/README.md`](../tests/README.md).
