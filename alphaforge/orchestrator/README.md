# Orchestrateur (`alphaforge/orchestrator/`)

L'orchestrateur est l'agent 1 du noyau d'AlphaForge AI. Il charge le profil de l'investisseur, le valide, interprète la demande en texte libre, construit un plan, lance l'analyste, appelle les autres agents dans l'ordre et consigne chaque étape dans le registre de preuves.

Ce document décrit le code de ce dossier. Pour l'architecture globale, voir [`../../Agents.md`](../../Agents.md) (section 1.2, agent 1, et section 1.3 pour la boucle de retour) et [`../../README.md`](../../README.md). Pour la vue d'ensemble du paquet, voir [`../README.md`](../README.md).

## 1. Rôle et limites

**Ce que fait l'orchestrateur.**
- Charger et valider un profil (`tools.py`, `profile.py`).
- Transformer la demande en `ParsedRequest` (`request.py`).
- Construire un plan déterministe (`Orchestrator.build_plan`).
- Appeler l'enrichissement à la demande, exécuter les outils de l'analyste, appeler la gouvernance et l'explicabilité, et écrire le registre (`Orchestrator.run`).

**Ce qu'il ne fait jamais.** Il ne calcule aucun chiffre financier et ne rend aucun verdict (docstring de `orchestrator.py` et de `tools.py`). Les calculs viennent de l'analyste (`alphaforge/quant`), le verdict de la gouvernance. L'orchestrateur écrit pourtant les entrées `verdict` dans le registre, mais seulement en recopiant le dictionnaire que la gouvernance lui renvoie (`GovernanceOutcome.verdict`). Le test `test_orchestrator_never_computes_or_judges` vérifie que, sans autre agent, le registre ne contient que des entrées des agents `orchestrateur` et `analyste` et aucune entrée `verdict`.

L'orchestrateur ne dépend d'aucun persona. Le plan ne change pas avec le profil, sauf la devise de base transmise aux outils (test `test_same_request_same_plan_for_different_profiles`). Les limites de risque dérivées du profil relèvent de la gouvernance.

## 2. Modules

| Fichier | Rôle |
|---|---|
| `__init__.py` | Réexporte l'API publique : `Orchestrator`, `QuantAnalyst`, `RunResult`, `Plan`, `PlanStep`, `ToolCall`, `GovernanceOutcome`, `InvestorProfile`, `ProfileIssue`, `ProfileValidation`, `ProfileError`, `ParsedRequest`, `RuleBasedParser`, `LLMParser`, `Ledger`, `LedgerEntry`, `load_profile`, `validate_profile`, `ledger_write`. |
| `profile.py` | Modèles pydantic du profil : `InvestorProfile`, `Criterion`, `Preference`, `ProfileIssue`, `ProfileValidation`. |
| `tools.py` | `load_profile`, `validate_profile`, `ledger_write`, `ProfileError`, seuils de cohérence, dossier `PROFILES_DIR`. |
| `request.py` | `ParsedRequest`, `RuleBasedParser`, `LLMParser`, `BedrockClient`, repérage des actifs, signaux de périmètre, extraction des poids. |
| `orchestrator.py` | `Orchestrator`, `QuantAnalyst`, `Plan`, `PlanStep`, `ToolCall`, `GovernanceOutcome`, `RunResult`, constantes `MAX_FEEDBACK_ITERATIONS` et `DEFAULT_WINDOWS`. |
| `ledger.py` | `Ledger`, `LedgerEntry`, `EntryKind` : le registre de preuves en ajout seul. |
| `profiles/` | Trois profils d'exemple : `amira.json`, `mateo.json`, `robert.json`. |

## 3. Le profil investisseur (`profile.py`, `tools.py`)

### 3.1 Schéma

`InvestorProfile` hérite de `StrictModel` (`extra="forbid"`) : un champ inconnu est refusé.

| Champ | Type | Défaut | Valeurs permises |
|---|---|---|---|
| `profile_id` | `str` | obligatoire | À charger par identifiant, il doit correspondre à `^[A-Za-z0-9_-]+$` (voir 3.2). |
| `display_name` | `Optional[str]` | `None` | libre |
| `language` | `Literal` | `"fr"` | `"fr"`, `"en"` |
| `experience` | `Literal` | `"debutant"` | `"debutant"`, `"intermediaire"`, `"avance"` |
| `horizon_years_min` | `int` | obligatoire | `>= 0` |
| `horizon_years_max` | `int` | obligatoire | `>= 0`, et `horizon_years_min <= horizon_years_max` (validateur `_horizon_order`) |
| `risk_tolerance` | `RiskTolerance` | obligatoire | `"faible"`, `"faible_modere"`, `"modere"`, `"modere_eleve"`, `"eleve"` |
| `objectives` | `List[Objective]` | `[]` | `"preservation_capital"`, `"revenu"`, `"croissance_moderee"`, `"croissance_elevee"` |
| `base_currency` | `Literal` | `"CAD"` | `"CAD"`, `"USD"` |
| `hard_exclusions` | `List[Criterion]` | `[]` | voir ci-dessous |
| `soft_preferences` | `List[Preference]` | `[]` | voir ci-dessous |

Le rang numérique de la tolérance au risque est `RISK_RANK` : `faible` 0, `faible_modere` 1, `modere` 2, `modere_eleve` 3, `eleve` 4.

**Critères.**
- `Criterion` : `kind` (`"asset_class"`, `"sector"`, `"ticker"` ou `"theme"`), `value` (`str`), `reason` (`Optional[str]`). Le docstring précise que `kind` et `value` sont comparés par la gouvernance, pas dans ce paquet.
- `Preference` hérite de `Criterion` et ajoute `weight` (`float`, défaut 0.5, borné à `0.0 <= weight <= 1.0`). C'est une préférence souple : elle oriente sans bloquer.
- `hard_exclusions` contient des `Criterion` (exclusions strictes) et `soft_preferences` des `Preference`.

### 3.2 Chargement : `load_profile(source, profiles_dir=None)`

`source` peut être :
- une instance `InvestorProfile`, renvoyée telle quelle ;
- un `str` : identifiant d'un fichier `profiles/<id>.json` (dossier `PROFILES_DIR`, ou `profiles_dir` s'il est fourni) ;
- un `dict`, validé par `InvestorProfile.model_validate`.

Un nouveau persona s'ajoute donc en déposant un fichier JSON ou en passant un dict, sans modifier le code (docstring de `load_profile`).

En cas d'échec, la fonction lève `ProfileError(reason, message)`. `reason` est un code stable :

| `reason` | Cas |
|---|---|
| `invalid_profile_id` | L'identifiant ne correspond pas à `^[A-Za-z0-9_-]+$` (par exemple `../x`, `a/b`, `robert.json`, chaîne vide). Cela empêche toute sortie du dossier. |
| `profile_not_found` | Le fichier `<id>.json` n'existe pas. |
| `profile_unreadable` | Le fichier contient un JSON invalide. |
| `profile_schema_invalid` | Le contenu ne respecte pas le schéma (champ inconnu, horizon inversé, poids hors de `[0, 1]`, etc.). |

### 3.3 Validation : `validate_profile(profile)`

La fonction renvoie un `ProfileValidation` (`profile_id`, `valid`, `issues`). Chaque `ProfileIssue` a un `code`, une `severity` (`"error"` ou `"warning"`) et un `message`. `valid` est faux dès qu'une erreur est présente. Les avertissements ne bloquent pas : ils sont consignés puis transmis à la gouvernance, qui les reprend comme raisons globales (`governance.py`).

Seuils (dans `tools.py`) : `HIGH_RISK_MIN_RANK` = rang de `modere_eleve` (3), `LOW_RISK_MAX_RANK` = rang de `faible_modere` (1), `SHORT_HORIZON_MAX_YEARS` = 2.

| Code | Sévérité | Condition |
|---|---|---|
| `NO_OBJECTIVES` | warning | `objectives` est vide. |
| `RISK_HORIZON_CONFLICT` | warning | Rang de risque `>= 3` et `horizon_years_max <= 2`. |
| `RISK_OBJECTIVE_CONFLICT` | warning | Rang de risque `<= 1` et `croissance_elevee` dans les objectifs. |
| `RISK_OBJECTIVE_CONFLICT` | warning | Rang de risque `>= 3` et `objectives == ["preservation_capital"]` (seul objectif). |
| `EXCLUSION_PREFERENCE_CONTRADICTION` | error | Une préférence a le même `kind` et la même `value` (comparée après `strip().lower()`) qu'une exclusion. |

Deux valeurs identiques avec des `kind` différents ne sont pas une contradiction (test `test_same_value_different_kind_is_not_a_contradiction`).

### 3.4 Les trois profils d'exemple

Les trois profils chargent et valident sans aucun avertissement (vérifié, voir l'exemple 1 pour Amira ; test `test_shipped_example_profiles_load_and_validate`). Ils partagent `language: "fr"`, `base_currency: "CAD"`, `experience: "intermediaire"` pour Amira et Mateo, `"avance"` pour Robert, et aucune `hard_exclusions`.

| Profil | Horizon (ans) | Tolérance au risque | Objectifs | Préférences souples |
|---|---|---|---|---|
| `amira` | 15 à 30 | `eleve` | `croissance_elevee` | secteur `Information Technology` (poids 0.9), classe d'actif `crypto` (0.6) |
| `mateo` | 8 à 12 | `modere` | `croissance_moderee` | thèmes `esg` (0.9) et `eviter_controverses` (0.9) |
| `robert` | 3 à 5 | `faible_modere` | `preservation_capital`, `croissance_moderee` | aucune |

Dans `amira.json`, la raison de la préférence sectorielle précise que le secteur sert de substitut vérifiable à un thème « IA » pour lequel aucune donnée n'existe.

## 4. Interprétation de la demande (`request.py`)

### 4.1 `ParsedRequest`

| Champ | Type | Sens |
|---|---|---|
| `raw_text` | `str` | Texte d'origine. |
| `intent` | `Intent` | `evaluate_asset`, `compare_assets`, `build_scenario`, `explain_risk` ou `unknown`. |
| `tickers` | `List[str]` | Tickers de l'univers, dans l'ordre d'apparition. |
| `unknown_tickers` | `List[str]` | Symboles repérés mais absents de l'univers. |
| `scope_flags` | `List[ScopeFlag]` | `asks_prediction`, `asks_transaction`, `asks_personal_advice`. |
| `parser` | `str` | `"rules"` par défaut ; `"llm"` ; `"rules(fallback)"` si le LLM a échoué. |
| `weights` | `Dict[str, float]` | Allocation décrite dans la demande (ticker vers fraction). |
| `rebalancing` | `Optional[Literal["rebalanced", "buy_and_hold"]]` | Hypothèse de rééquilibrage si la demande la précise. |

Le modèle est en `extra="forbid"`. Le parseur ne décide rien : il signale (`scope_flags`) ce que la gouvernance devra examiner. Les refus de périmètre (prédiction, transaction, conseil personnalisé) appartiennent à la gouvernance (`check_scope_limits`).

### 4.2 Repérage des actifs

`find_assets(text, assets)` renvoie `(tickers connus, symboles inconnus)`, dans l'ordre d'apparition. L'index d'alias (`_universe_index`) contient :
- le ticker complet (`RY.TO`) ;
- sa racine, avant le premier `.` ou `-` (`RY`, `BTC`) ;
- le premier mot du nom de l'actif, s'il compte au moins `_NAME_MIN_LEN` = 5 caractères, désigne un seul actif et n'entre pas en collision avec un ticker.

Un mot du texte est accepté s'il a une forme de symbole (préfixe `$`, majuscules, `.` ou `-`) ou si l'alias compte au moins 5 caractères. Il devient un symbole inconnu s'il n'est pas dans l'index, n'est pas dans la liste `_NOT_TICKERS` (sigles comme `ESG`, `ETF`, `REER`, `CELI`, `TSX`) et s'écrit avec `$`, avec un point, ou en majuscules sur au moins 3 lettres.

### 4.3 Intention et signaux de périmètre

`RuleBasedParser.parse` applique les motifs `_INTENT_PATTERNS` dans cet ordre, le premier qui correspond gagne : `compare_assets`, `build_scenario`, `explain_risk`, `evaluate_asset`. Ensuite :
- si aucune intention ne correspond, deux actifs ou plus donnent `compare_assets`, un seul donne `evaluate_asset`, zéro laisse `unknown` ;
- `compare_assets` avec un seul actif devient `evaluate_asset` ;
- si la demande contient des poids et au moins deux actifs, l'intention devient `build_scenario`, même sans le mot « scénario ».

Les signaux de périmètre viennent des expressions régulières `_PATTERNS` (insensibles à la casse) :
- `asks_prediction` : par exemple « prédis », « prévois », « predict », « forecast », « l'an prochain », « next year », « va monter », « vaudra » ;
- `asks_transaction` : « achète », « achetez », « vends », « vendez », « passer un ordre », « buy », « sell », « place an order ». La forme « acheter » à l'infinitif n'y figure pas ;
- `asks_personal_advice` : « dois-je », « devrais-je », « devrais », « conseille », « recommande », « should I », « do you recommend », « what should I ».

### 4.4 Extraction des poids et rééquilibrage

`extract_weights(text, assets)` lit des pourcentages (symbole `%`, « pour cent » ou « percent ») dans les deux ordres : « 60 % SPY » (avec un éventuel mot de liaison comme `en`, `de`, `of`) et « SPY 60 % » (aussi « SPY à 60 % », « SPY: 60 % »). Elle garde l'ordre qui explique le plus de pourcentages, et en cas d'égalité le pourcentage placé avant l'actif. La virgule décimale est acceptée. Un actif cité deux fois garde sa première valeur. Les poids sont en fractions (60 % devient 0.6). **Rien n'est complété ni normalisé** : une allocation incomplète ou de somme différente de 1 est transmise telle quelle, puis refusée par l'analyste (voir 5.2).

`detect_rebalancing(text)` renvoie `"buy_and_hold"` si le texte contient « achat et conserv… », « buy and hold », « sans rééquilibr… », « without rebalanc… » ou « no rebalanc… ». Sinon, il renvoie `"rebalanced"` si le texte contient « rééquilibr… » ou « rebalanc… », sinon `None`.

### 4.5 `LLMParser` et `BedrockClient`

`LLMParser(client, fallback=None)` demande à un modèle de proposer une intention en JSON, puis revérifie la réponse avec le code. Le client est tout objet qui a une méthode `complete(system, user) -> str` (protocole `LLMClient`). La consigne système (`_llm_system`) donne au modèle l'univers d'actifs (symbole, nom, classe) pour qu'il relie par exemple « l'or » à `GLD`, et lui interdit de répondre, de calculer ou de recommander.

**Ce que le code revérifie, étape par étape :**
1. Le JSON est extrait entre la première `{` et la dernière `}` de la réponse. Une intention absente de `INTENTS` lève une erreur.
2. Les `scope_flags` du modèle sont filtrés : seuls les trois signaux connus sont gardés.
3. Les `mentioned_symbols` du modèle sont repassés par `find_assets` : un symbole qui n'est pas dans l'univers ne devient jamais un ticker. Il ne peut finir que dans `unknown_tickers`, ou être ignoré.
4. Les actifs repérés par règles dans le texte d'origine sont ajoutés : le modèle ne peut pas en retirer.
5. Les signaux détectés par règles dans le texte d'origine sont ajoutés : le modèle ne peut pas en désactiver.
6. Les poids viennent uniquement du texte d'origine (`extract_weights`), comme le `rebalancing` (`detect_rebalancing`). Le modèle ne peut ni en inventer ni en retirer.
7. Si des poids existent et qu'il y a au moins deux actifs, l'intention est forcée à `build_scenario`.
8. Si le modèle répond `unknown` alors que des actifs sont cités, l'intention devient `compare_assets` (deux actifs ou plus) ou `evaluate_asset` (un seul).
9. `raw_text` et `parser="llm"` sont posés par le code.

**Ce que le modèle décide encore.** Il choisit l'intention parmi les valeurs permises, sauf les cas forcés aux étapes 7 et 8. Il peut aussi ajouter des tickers présents dans l'univers que le texte ne nomme pas explicitement (par exemple « l'or » vers `GLD`). Ces tickers ne sont revérifiés que contre l'univers, pas contre le sens de la demande. La variante LLM ne rétrograde pas `compare_assets` en `evaluate_asset` quand il n'y a qu'un actif (seul `RuleBasedParser` le fait).

**Repli.** Toute exception (réponse invalide, JSON illisible, intention inconnue, panne réseau, quota, accès refusé) déclenche `RuleBasedParser`, et le résultat porte `parser="rules(fallback)"`.

**`BedrockClient(model_id, region_name="us-east-1", timeout_s=8.0)`** appelle l'API Converse de `bedrock-runtime`. `boto3` est importé à l'instanciation seulement. Valeurs du code (`request.py`) :
- `connect_timeout=3`, `read_timeout=timeout_s` (8.0 par défaut), `retries={"max_attempts": 2, "mode": "standard"}` ;
- `inferenceConfig={"temperature": 0.0, "maxTokens": 400}`.

Le choix du client et du modèle se fait dans `alphaforge/app/pipeline.py` (`build_parser`) : la variable `ALPHAFORGE_LLM=0` désactive le LLM, l'absence d'identifiants AWS donne aussi `RuleBasedParser`, le modèle par défaut est `DEFAULT_LLM_MODEL` et se remplace par `ALPHAFORGE_LLM_MODEL`.

## 5. Plan et déroulé (`orchestrator.py`)

### 5.1 Construction : `Orchestrator(...)`

```
Orchestrator(analyst, governance=None, explainer=None, parser=None,
             as_of=None, on_entry=None, enrichment=None)
```

- `analyst` : un objet qui expose `tools`, `window_aliases` et `run(ToolCall) -> Evidence`. `QuantAnalyst(store)` est l'adaptateur vers `alphaforge.quant` (il expose aussi `store`).
- `governance` : fonction `(profile, request, ledger) -> GovernanceOutcome`. `GovernanceOutcome` contient `verdict` (dictionnaire) et `follow_up` (liste de `ToolCall` à faire exécuter par l'analyste).
- `explainer` : fonction `(profile, ledger) -> str`.
- `parser` : `RuleBasedParser()` par défaut.
- `as_of` : date transmise à tous les outils.
- `on_entry` : observateur du registre (voir 6.4).
- `enrichment` : agent d'enrichissement facultatif, avec `detect_needs(...)` et `run(...)`.

### 5.2 Le plan : `build_plan(request, profile)`

Le plan est déterministe : même demande, mêmes outils disponibles, même plan. Chaque `PlanStep` a un `step_id` (`s1`, `s2`...), un `agent` (`analyste`, `gouvernance` ou `explicabilite`), une `action`, des `arguments` et `depends_on`. Les étapes sont créées dans cet ordre :

1. Pour chaque symbole de `tickers + unknown_tickers` : `asset_lookup` avec `query`. Les symboles hors univers passent aussi par cet outil, ce qui transforme le refus en preuve visible.
2. Pour chaque ticker de l'univers :
   - `compute_metrics` pour chaque fenêtre de `DEFAULT_WINDOWS` (`("complet", "2022")`) que l'analyste connaît (`window_aliases`), avec `{"window": {"alias": w}}` ;
   - puis `stress_window`.
3. Si au moins deux tickers et `intent` vaut `compare_assets` ou `build_scenario` : `correlation_matrix` sur la fenêtre `complet`.
4. Si au moins deux tickers et `intent == "build_scenario"` : `portfolio_metrics` sur la fenêtre `complet`. Si la demande donne des poids, ils sont transmis tels quels (`weights_basis: "user"`). Sinon, la pondération est égale (`1/n`, `weights_basis: "assumed_equal"`), posée comme hypothèse de scénario. `rebalancing` vaut la valeur de la demande, ou `"rebalanced"` par défaut.
5. Une étape `gouvernance` `review` (argument `scope_flags`) qui dépend de toutes les étapes de l'analyste.
6. Une étape `explicabilite` `explain` qui dépend de la gouvernance.

Les arguments des outils chiffrés contiennent `base_currency` (devise du profil, si un profil est fourni) et `as_of` (si l'orchestrateur en a un). Si un outil voulu n'est pas dans `analyst.tools`, il est ajouté à `Plan.unavailable_tools`, sans substitut.

Seules les étapes de l'analyste sont exécutées d'après le plan. Les étapes `gouvernance` et `explicabilite` décrivent l'ordre attendu : `run` les appelle directement.

### 5.3 Déroulé de `run(request_text, profile_source, assets)`

`assets` est la liste des lignes de l'univers (au moins `ticker` et `name`). Le registre est créé à chaque appel, avec `on_write=on_entry`.

1. **Profil.** `load_profile` ; en cas de `ProfileError`, une note `{code: e.reason, message}` est écrite et le résultat est `profile_invalid`. Sinon `validate_profile`, puis une entrée `profile` (profil et validation). Si la validation contient une erreur, le résultat est `profile_invalid` avec les messages d'erreur dans `message`.
2. **Demande.** `parser.parse`, puis une entrée `request`. Sans ticker ni symbole inconnu, une note `needs_clarification` est écrite et le résultat est `needs_clarification`. Une demande qui ne contient qu'un symbole inconnu continue : le refus de l'analyste devient une preuve.
3. **Plan.** `build_plan`, entrée `plan`, puis une note `tool_unavailable` par outil manquant.
4. **Enrichissement** (seulement si un agent est fourni). `_enrich(ledger, profile, request)`.
5. **Analyste.** Chaque étape `analyste` du plan s'exécute (`_run_analyst`).
6. **Gouvernance** et boucle de retour (5.4), s'il y a une gouvernance.
7. **Explicabilité**, si l'explicateur et la gouvernance sont présents.

`_run_analyst` écrit l'`Evidence` avec `Ledger.write_evidence("analyste", ev)`, puis une entrée `step` `{step_id, tool, arguments, status, evidence_id}` où `status` est celui de la preuve (`ok`, `warning` ou `refused`). Si l'outil est inconnu ou les arguments invalides (`KeyError` ou `ValueError`), il n'y a pas de preuve et l'entrée `step` porte `status: "error"` et `error`. Les autres exceptions ne sont pas interceptées.

### 5.4 Enrichissement : `_enrich`

Il ne tourne que si un agent d'enrichissement est fourni, et il n'écrit rien s'il ne détecte aucun besoin.

1. L'analyste doit exposer `store`. Sinon, une note `enrichment_failed` est écrite et l'enrichissement est ignoré.
2. `detect_needs(profile, request, store, as_of, ledger, **extra)` est appelé. S'il n'y a aucun écart (`needs.gaps` vide), la fonction retourne sans rien écrire.
3. Une note `needs_detected` est écrite (ou `needs_relaunched` pour la relance), avec le nombre de besoins comblables et hors périmètre, et la liste `gaps`.
4. S'il y a des besoins comblables, `enrichment.run(needs, ledger, needs.as_of)` s'exécute, puis une note `enrichment_done` indique le nombre de données écrites, de lacunes non comblées et d'éléments postérieurs à `as_of` écartés, avec `written`, `gaps`, `excluded_after_as_of`, `rejected_facts` et `degraded`.
5. Toute exception de l'agent donne une note `enrichment_failed` : l'analyse continue sans lui.
6. La fonction retourne vrai si le registre a reçu des données ou des lacunes nouvelles.

**Relance unique pour les alternatives.** Après la boucle de retour, si un agent d'enrichissement est présent, l'orchestrateur collecte les tickers des preuves `compute_metrics` du registre qui ne figurent pas dans la demande (ce sont les alternatives chiffrées à la demande de la gouvernance). S'il y en a, il rappelle `_enrich` une seule fois, avec une copie de la demande dont `tickers` est cette liste, et avec `relaunch=True`. `relaunch=True` passe `auto_context=False` à `detect_needs` : les alternatives ne reçoivent pas les données de contexte automatiques (le paramètre `auto_context` de `alphaforge/enrichment/needs.py` recherche, par défaut, les fondamentaux d'une action américaine demandée même si la demande ne les évoque pas). Si `_enrich` retourne vrai, la gouvernance est rappelée et un nouveau `verdict` est écrit.

### 5.5 Boucle de retour gouvernance vers analyste

`MAX_FEEDBACK_ITERATIONS = 2` (`orchestrator.py`, ligne 19 ; Agents.md 1.3).

1. La gouvernance est appelée, son verdict est écrit (`kind="verdict"`, `agent="gouvernance"`).
2. Tant que `outcome.follow_up` est non vide et que le compteur est sous 2 : chaque `ToolCall` s'exécute via `_run_analyst` avec l'identifiant `fb<itération>.<n>` (`fb1.1`, `fb2.1`...), puis la gouvernance est rappelée et un nouveau verdict est écrit.
3. La relance de l'enrichissement (5.4) a lieu ensuite.
4. Si le dernier `outcome.follow_up` est encore non vide, une note `feedback_limit_reached` est écrite (« Limite de 2 itérations atteinte ; la tension reste non résolue »).

Au maximum, la gouvernance est donc appelée 3 fois dans la boucle, plus une fois après la relance d'enrichissement (test `test_feedback_loop_is_bounded`).

### 5.6 `RunResult` et statuts

Champs : `status`, `message`, `profile_validation`, `request`, `plan`, `pending_agents`, `answer`, et `ledger` (exclu de la sérialisation, `Field(exclude=True)`).

| `status` | Quand |
|---|---|
| `profile_invalid` | Profil introuvable, illisible, non conforme, ou contradictoire (erreur de validation). |
| `needs_clarification` | La demande ne nomme aucun actif, connu ou inconnu. |
| `partial` | Le plan et l'analyste ont tourné, mais la gouvernance ou l'explicabilité manque. `pending_agents` liste les agents non exécutés (`"gouvernance"`, `"explicabilite"`). Sans gouvernance, l'explicabilité n'est jamais appelée, même fournie. |
| `complete` | Gouvernance et explicateur sont présents : `answer` contient le texte de l'explication. |

### 5.7 Notes écrites par l'orchestrateur (champ `payload["code"]`)

| Code | Quand |
|---|---|
| `invalid_profile_id`, `profile_not_found`, `profile_unreadable`, `profile_schema_invalid` | Échec de `load_profile` (voir 3.2). |
| `needs_clarification` | Aucun actif dans la demande. |
| `tool_unavailable` | Un outil du plan n'existe pas chez l'analyste. |
| `needs_detected`, `needs_relaunched` | Besoins de données détectés (première passe, relance). |
| `enrichment_done` | Enrichissement exécuté. |
| `enrichment_failed` | Pas de magasin de données exposé, ou exception de l'agent. |
| `feedback_limit_reached` | La boucle de retour s'arrête avec un `follow_up` encore non vide. |

## 6. Le registre de preuves (`ledger.py`)

### 6.1 `LedgerEntry` et `EntryKind`

`LedgerEntry` (`extra="forbid"`) : `seq` (`int`), `kind` (`EntryKind`), `agent` (`str`), `payload` (`Dict[str, Any]`), `evidence_id` (`Optional[str]`). `seq` commence à 1 et augmente de 1 à chaque entrée. Il n'y a aucune horloge : l'ordre vient de `seq`, ce qui garde les traces reproductibles.

`EntryKind` accepte neuf valeurs. Un autre `kind` est refusé (test `test_unknown_kind_rejected`).

| `kind` | Contenu | Écrit par (valeur de `agent`) |
|---|---|---|
| `profile` | Profil et validation | `orchestrateur` |
| `request` | `ParsedRequest` sérialisé | `orchestrateur` |
| `plan` | `Plan` sérialisé | `orchestrateur` |
| `step` | Exécution d'un appel d'outil | `orchestrateur` |
| `evidence` | `Evidence` de l'analyste, avec `evidence_id` | `analyste` (écrit par l'orchestrateur) |
| `verdict` | Dictionnaire renvoyé par la gouvernance | `gouvernance` (écrit par l'orchestrateur) |
| `note` | Code et message (voir 5.7) | `orchestrateur` ; l'explicabilité y écrit aussi `explanation_verified` ou `explanation_repaired` (`agent="explicabilite"`) |
| `enrichment` | Donnée ou lacune enrichie | `enrichissement` |
| `source_card` | Fiche source d'une donnée enrichie | `enrichissement` |

### 6.2 API

- `write(kind, agent, payload, evidence_id=None)` : ajoute une entrée et la renvoie.
- `write_evidence(agent, evidence)` : écrit une preuve avec `model_dump(mode="json")`. **Idempotent** : si l'`evidence_id` est déjà connu, rien n'est écrit et la méthode renvoie `None`. Seul ce chemin vérifie les doublons ; un `write("evidence", ...)` direct ne le fait pas. Quand une boucle de retour redemande une preuve déjà présente, le registre ne la duplique pas, mais une entrée `step` est tout de même écrite (test `test_repeated_follow_up_does_not_duplicate_evidence`).
- `entries(kind=None)` : toutes les entrées, ou celles d'un `kind`.
- `evidence()` : la liste des `payload` des entrées `evidence`.
- `to_jsonl(path)` : une ligne JSON par entrée (`model_dump(mode="json")`, `ensure_ascii=False`, `sort_keys=True`, UTF-8). Deux registres identiques donnent des fichiers identiques octet par octet (test `test_jsonl_roundtrip_and_determinism`). Les clés d'une ligne sont `agent`, `evidence_id`, `kind`, `payload`, `seq`.
- `len(ledger)` : nombre d'entrées.

### 6.3 Qui écrit, qui lit

Le registre est en ajout seul : aucune méthode ne modifie ou supprime une entrée. L'orchestrateur écrit profil, demande, plan, étapes, notes, et recopie preuves et verdicts. L'enrichissement écrit directement `enrichment` et `source_card`. L'explicabilité écrit une note de vérification. La gouvernance et l'explicabilité lisent le registre (`ledger.evidence()`, `ledger.entries(...)`). Voir [`../README.md`](../README.md) pour la vue d'ensemble.

### 6.4 Observateur `on_write`

`Ledger(on_write=callback)` appelle `callback(entry)` juste après chaque ajout, y compris par `write_evidence`. `Orchestrator(on_entry=...)` le transmet au registre : l'interface s'en sert pour afficher la trace en direct. Le docstring précise que l'observateur ne doit pas modifier le registre. Le code ne l'impose pas : l'entrée n'est pas figée, et une exception de l'observateur remonte à l'appelant après l'ajout de l'entrée.

## 7. Outils exposés (`tools.py`)

| Fonction | Rôle |
|---|---|
| `load_profile(source, profiles_dir=None)` | Voir 3.2. |
| `validate_profile(profile)` | Voir 3.3. |
| `ledger_write(ledger, kind, agent, payload, evidence_id=None)` | Raccourci pour `ledger.write(...)` : consigne un profil, une demande, un plan, une étape, un verdict ou une note. |

`ProfileError` et les constantes `PROFILES_DIR`, `HIGH_RISK_MIN_RANK`, `LOW_RISK_MAX_RANK`, `SHORT_HORIZON_MAX_YEARS` sont aussi définies dans ce module. Le nom `parse_request` d'Agents.md correspond à la méthode `parse` des parseurs de `request.py`. Il n'existe pas de fonction de ce nom dans le code.

Le dossier `alphaforge/enrichment/` a son propre `ledger_write` (`enrichment/ledger_write.py`), qui écrit des enregistrements enrichis. Il ne faut pas le confondre avec celui-ci.

## 8. Garde-fous

- **Pas de calcul, pas de verdict** dans l'orchestrateur (section 1).
- **Schémas stricts** : `extra="forbid"` sur le profil, la demande, le plan, le résultat et les entrées du registre.
- **Identifiant de profil contrôlé** par une expression régulière : pas de `../`.
- **Profil contradictoire** (même élément exclu et préféré) : arrêt avant tout calcul. Les autres incohérences ne sont que des avertissements transmis.
- **Le modèle propose, le code dispose** : voir 4.5.
- **Poids jamais corrigés** : transmis tels quels, l'analyste refuse (`INVALID_WEIGHTS`, test `test_incomplete_or_wrong_weights_are_refused_by_the_analyst_not_corrected`) ; sans poids, la pondération égale est déclarée comme hypothèse (`weights_basis: "assumed_equal"`).
- **Symbole hors univers** : transmis à `asset_lookup`, le refus (`NOT_IN_UNIVERSE`) devient une preuve visible, sans valeur inventée.
- **Outil manquant** : consigné par `tool_unavailable`, sans substitut.
- **Boucle de retour bornée** à `MAX_FEEDBACK_ITERATIONS` (2) et enrichissement relancé une seule fois.
- **Enrichissement facultatif** : sa panne ne bloque pas l'analyse (`enrichment_failed`).
- **Preuves non dupliquées** et ordre porté par `seq`, sans horloge.
- **Pas de réseau pour les règles** : `RuleBasedParser` est déterministe. Une panne de Bedrock retombe sur les règles.

## 9. Exemples vérifiés

Chaque exemple a été exécuté depuis `/workshop/datathon2026` avec `ALPHAFORGE_LLM=0 python3 -W ignore -c "…"` (Python 3.9.25), sans réseau ni AWS. Les sorties ci-dessous sont celles obtenues.

### Exemple 1 : charger et valider un profil

```python
from alphaforge.orchestrator import load_profile, validate_profile, ProfileError

amira = load_profile("amira")
print(amira.risk_tolerance, amira.horizon_years_min, amira.horizon_years_max)
print(validate_profile(amira).valid, validate_profile(amira).issues)

incoherent = load_profile({
    "profile_id": "demo", "horizon_years_min": 0, "horizon_years_max": 1,
    "risk_tolerance": "eleve", "objectives": [],
    "hard_exclusions": [{"kind": "sector", "value": "Energy"}],
    "soft_preferences": [{"kind": "sector", "value": " energy "}],
})
v = validate_profile(incoherent)
print(v.valid, [(i.code, i.severity) for i in v.issues])

try:
    load_profile("../etc/passwd")
except ProfileError as e:
    print(e.reason)
```

```
eleve 15 30
True []
False [('NO_OBJECTIVES', 'warning'), ('RISK_HORIZON_CONFLICT', 'warning'), ('EXCLUSION_PREFERENCE_CONTRADICTION', 'error')]
invalid_profile_id
```

### Exemple 2 : interpréter une demande par règles

```python
from alphaforge.quant import DataStore
from alphaforge.orchestrator import RuleBasedParser

assets = DataStore.from_directory().assets.to_dict("records")
parser = RuleBasedParser()

r = parser.parse("Construis un portefeuille avec 60 % SPY et 40 % AGG sans rééquilibrage", assets)
print(r.intent, r.tickers, r.weights, r.rebalancing, r.parser)

r = parser.parse("Prédis NVDA l'an prochain", assets)
print(r.intent, r.tickers, r.scope_flags)

r = parser.parse("Évalue TSLA et ZZZZ", assets)
print(r.tickers, r.unknown_tickers)
```

```
build_scenario ['SPY', 'AGG'] {'SPY': 0.6, 'AGG': 0.4} buy_and_hold rules
evaluate_asset ['NVDA'] ['asks_prediction']
['TSLA'] ['ZZZZ']
```

### Exemple 3 : `LLMParser` avec un client factice, et repli

Le client factice répond sans signal de périmètre et cite un symbole inventé. Le code ajoute `SPY` depuis le texte, conserve `NOTREAL` comme symbole inconnu (jamais comme ticker) et rétablit `asks_prediction`.

```python
from alphaforge.quant import DataStore
from alphaforge.orchestrator import LLMParser

assets = DataStore.from_directory().assets.to_dict("records")

class FakeClient:  # même interface que BedrockClient : complete(system, user) -> texte
    def complete(self, system, user):
        return '{"intent": "compare_assets", "mentioned_symbols": ["GLD", "NOTREAL"], "scope_flags": []}'

class BrokenClient:
    def complete(self, system, user):
        raise RuntimeError("réseau indisponible")

r = LLMParser(FakeClient()).parse("Quel est le plus risqué entre l'or et SPY, prévois la suite", assets)
print(r.parser, r.intent, r.tickers, r.unknown_tickers, r.scope_flags)

r = LLMParser(BrokenClient()).parse("Évalue NVDA", assets)
print(r.parser, r.tickers)
```

```
llm compare_assets ['GLD', 'SPY'] ['NOTREAL'] ['asks_prediction']
rules(fallback) ['NVDA']
```

### Exemple 4 : plan et exécution sans gouvernance ni explicabilité

```python
from alphaforge.quant import DataStore
from alphaforge.orchestrator import Orchestrator, QuantAnalyst

store = DataStore.from_directory()
assets = store.assets.to_dict("records")
result = Orchestrator(QuantAnalyst(store)).run("Compare NVDA et BTC-USD", "robert", assets)

print(result.status, result.pending_agents)
for s in result.plan.steps:
    print(s.step_id, s.agent, s.action, s.depends_on)
print({k: len(result.ledger.entries(k)) for k in ("profile", "request", "plan", "step", "evidence", "note")})

print(Orchestrator(QuantAnalyst(store)).run("bonjour", "robert", assets).status)
print(Orchestrator(QuantAnalyst(store)).run("Évalue NVDA", "personne", assets).status)
```

```
partial ['gouvernance', 'explicabilite']
s1 analyste asset_lookup []
s2 analyste asset_lookup []
s3 analyste compute_metrics []
s4 analyste compute_metrics []
s5 analyste stress_window []
s6 analyste compute_metrics []
s7 analyste compute_metrics []
s8 analyste stress_window []
s9 analyste correlation_matrix []
s10 gouvernance review ['s1', 's2', 's3', 's4', 's5', 's6', 's7', 's8', 's9']
s11 explicabilite explain ['s10']
{'profile': 1, 'request': 1, 'plan': 1, 'step': 9, 'evidence': 9, 'note': 0}
needs_clarification
profile_invalid
```

### Exemple 5 : boucle de retour bornée, observateur et export JSONL

La gouvernance factice demande toujours une vérification de plus. La boucle s'arrête après 2 retours, soit 3 appels de la gouvernance. La preuve `asset_lookup` sur `SPY` n'est écrite qu'une fois, malgré deux demandes. L'explicabilité reste en attente, faute d'explicateur.

```python
from alphaforge.quant import DataStore
from alphaforge.orchestrator import GovernanceOutcome, Orchestrator, QuantAnalyst, ToolCall
from alphaforge.orchestrator.orchestrator import MAX_FEEDBACK_ITERATIONS

store = DataStore.from_directory()
assets = store.assets.to_dict("records")
calls = []

def governance(profile, request, ledger):  # gouvernance factice : demande toujours une vérification de plus
    calls.append(1)
    return GovernanceOutcome(verdict={"status": "bloque"},
                             follow_up=[ToolCall(tool="asset_lookup", arguments={"query": "SPY"})])

seen = []
result = Orchestrator(QuantAnalyst(store), governance=governance, on_entry=lambda e: seen.append(e.seq)).run(
    "Évalue NVDA", "robert", assets)

print(MAX_FEEDBACK_ITERATIONS, len(calls), result.status, result.pending_agents)
print([e.payload["step_id"] for e in result.ledger.entries("step") if e.payload["step_id"].startswith("fb")])
print([e.payload["code"] for e in result.ledger.entries("note")])
print(len(seen) == len(result.ledger), len(result.ledger.entries("evidence")))
result.ledger.to_jsonl("/tmp/ledger_demo.jsonl")
print(open("/tmp/ledger_demo.jsonl", encoding="utf-8").readline()[:60])
```

```
2 3 partial ['explicabilite']
['fb1.1', 'fb2.1']
['feedback_limit_reached']
True 5
{"agent": "orchestrateur", "evidence_id": null, "kind": "pro
```

Pour une analyse complète avec les vrais agents, voir l'exemple de [`../README.md`](../README.md) (`AlphaForge.analyze`).

## 10. Tests associés (`tests/orchestrator/`)

Lancer `python3 -m pytest tests/orchestrator -q`. `conftest.py` fournit un univers synthétique de 6 actifs (`RY.TO`, `CNQ.TO`, `CNR.TO`, `AAPL`, `NVDA`, `BTC-USD`) avec des prix simulés, un `QuantAnalyst` et une fabrique de profils minimaux (`profile_dict`).

| Fichier | Tests | Ce qu'il couvre |
|---|---|---|
| `test_orch_profile.py` | 23 | Chargement depuis dict, instance, fichier (les trois profils d'exemple chargent et valident) ; codes `profile_not_found`, `invalid_profile_id` (`../etc/passwd`, `a/b`, `robert.json`, vide, `x y`), `profile_unreadable`, `profile_schema_invalid` (champ inconnu, horizon inversé, poids hors bornes) ; chaque code de `validate_profile`, y compris le cas « même valeur, `kind` différent » qui n'est pas une contradiction. |
| `test_orch_request.py` | 44 | Repérage des actifs (ticker complet, racine, alias de nom, alias ambigu écarté, mots courts en minuscules, ordre, symboles inconnus signalés, sigles financiers) ; intentions ; signaux de périmètre ; `LLMParser` avec client factice (réponse valide, texte et consigne transmis, tickers inventés, actifs du texte non retirables, signaux non désactivables, signal inconnu ignoré, repli sur cinq cas d'échec) ; extraction des poids dans les deux ordres, allocation incomplète qui reste un scénario, rééquilibrage lu dans la demande, poids que le LLM ne peut ni inventer ni retirer. |
| `test_orch_run.py` | 33 | Profil inconnu ou contradictoire (arrêt avant tout), avertissements non bloquants et présents dans le registre, demande sans actif ; structure, dépendances et déterminisme du plan ; corrélation et rejeu de portefeuille ; outil manquant consigné ; transmission de `as_of` et de la devise de base ; fenêtre absente ignorée ; une preuve par étape ; symbole inconnu devenu preuve refusée ; suivi invalide ou arguments invalides consignés en `error` ; statuts `partial` et `complete` ; boucle de retour (exécution puis réévaluation, borne à `MAX_FEEDBACK_ITERATIONS`, pas de doublon de preuve) ; absence de calcul et de verdict ; plan identique pour deux profils ; parseur personnalisé ; poids transmis sans modification, pondération égale déclarée, poids incomplets ou faux refusés par l'analyste. |
| `test_orch_ledger.py` | 6 | `seq` monotone depuis 1 ; `write_evidence` idempotent ; conservation de `evidence_id` et du contenu ; filtrage par `kind` ; export JSONL déterministe et relisible ; `kind` inconnu rejeté. |
| `test_orch_bench.py` | 2 | Banc d'essai de l'interprétation (voir ci-dessous). |

**Banc d'essai (`test_orch_bench.py` et `deploy/bench_llm.py`).** `deploy/bench_llm.py` évalue chaque interpréteur sur 12 demandes. Chaque demande rapporte 3 points : intention juste, actifs justes, signaux de périmètre attendus présents. Un passage vaut donc au plus 36. Les appels Bedrock qui retombent sur les règles sont comptés à part (`fallbacks`). Les résultats sont écrits dans `deploy/bench_results.json`. Les deux tests ne nécessitent ni réseau ni AWS :
1. `test_the_rules_score_is_recomputed_not_remembered` rejoue `RuleBasedParser` sur l'univers complet (`DataStore.from_directory()`), vérifie `max == 36` et `fallbacks == 0`, puis vérifie que le score publié pour `rules` dans `bench_results.json` est le score recalculé.
2. `test_the_readme_quotes_the_results_file` vérifie que, pour chaque interpréteur du fichier de résultats, la chaîne « total/maximum » figure dans le `README.md` racine.

Les passages Bedrock ne sont pas rejoués par les tests : leurs scores publiés ne se contrôlent qu'avec un accès AWS. Au moment de la rédaction, `deploy/bench_results.json` (exécuté le 2026-11-15) donne 30/36 pour les règles, 36/36 sur chacun de 3 passages pour `global.anthropic.claude-haiku-4-5-20251001-v1:0` et 35/36 sur chacun de 3 passages pour `us.amazon.nova-2-lite-v1:0`. Ces valeurs sont à relire dans le fichier, qui fait foi.

## 11. Limites connues

- **Règles d'interprétation limitées.** Le fichier de résultats du banc d'essai liste cinq demandes manquées par `RuleBasedParser`, par exemple « Compare la Banque Royale et TD » (seul `TD.TO` est reconnu) ou « entre l'or et les obligations américaines à long terme » (aucun actif), qui dépendent d'une désignation par description. Le motif de transaction ne couvre pas l'infinitif « acheter ». Le LLM compense une partie de ces cas quand il est disponible.
- **Intention du LLM non revérifiée sur le fond.** Le code contrôle la forme (valeur permise) et l'existence des tickers, pas la pertinence de l'intention ou des tickers ajoutés par le modèle (voir 4.5).
- **Poids exprimés en pourcentage seulement** : l'extraction demande `%`, « pour cent » ou « percent ».
- **Une seule relance d'enrichissement**, limitée aux alternatives chiffrées par `compute_metrics` ; les alternatives chiffrées par un autre outil ne la déclenchent pas.
- **Exceptions non interceptées.** Seules `KeyError` et `ValueError` de l'analyste sont consignées en `step` d'erreur. Une exception de la gouvernance, de l'explicateur ou d'un observateur `on_entry` remonte à l'appelant.
- **Registre en mémoire.** `Ledger` n'écrit sur disque que par `to_jsonl`. L'archivage S3 relève de `alphaforge/app/archive.py`.
- **Plan non adaptatif.** Les étapes `gouvernance` et `explicabilite` du plan sont informatives ; l'ordre d'appel est fixé dans `run`.
- **Non vérifié** : le comportement réel de `BedrockClient` face à un modèle ou une région donnés (aucun appel AWS n'a été fait pour ce document), et les scores Bedrock du banc d'essai autrement que par lecture du fichier de résultats.
