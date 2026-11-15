# Agent Gouvernance

Documentation du paquet `alphaforge/governance/`. Le rôle de l'agent dans l'architecture, ses outils et la boucle de retour sont décrits dans [`Agents.md`](../../Agents.md) (section « Agent 3 : Gouvernance » et section 2.4 pour l'enrichissement). Les décisions de calcul et les règles de blocage sont dans le [`README.md`](../../README.md) du dépôt. Ce document ne les répète pas : il décrit ce que fait le code, fichier par fichier.

Tous les nombres de ce document sont recopiés de `config.py` ou du code cité. Les exemples de la section 10 ont été exécutés avec `ALPHAFORGE_LLM=0 python3 -W ignore -c "..."` depuis `/workshop/datathon2026`.

## 1. Rôle et limites

La gouvernance compare des **preuves écrites par l'analyste** à des **limites dérivées du profil**, puis rend un verdict visible : `conforme`, `avertissement` ou `bloqué`, avec des raisons chiffrées.

Ce qu'elle fait :

- dériver des limites numériques du profil (`derive_constraints`) ;
- lire le registre (`build_facts`) et appliquer les contrôles (`checks.py`) ;
- intégrer les données de l'agent d'enrichissement (`apply_enrichment`) ;
- signaler les demandes hors périmètre (`check_scope_limits`) ;
- demander à l'orchestrateur de faire chiffrer des alternatives par l'analyste (`follow_up`).

Ce qu'elle ne fait jamais :

- **Aucun calcul de marché.** Ni rendement, ni volatilité, ni corrélation. Elle relit `compute_metrics`, `asset_lookup`, `correlation_matrix` et `portfolio_metrics` (`facts.py`, `checks.py`). Le seul calcul est arithmétique : un rapport valeur/limite (`ratio`) et une durée en années entre deux dates écrites par l'analyste (`check_horizon_fit`).
- **Aucune règle par persona.** Les fonctions ne lisent jamais `profile_id` ni `display_name` pour décider. `profile_id` n'est recopié que dans `Constraints`. Deux tests le vérifient : `test_constraints_do_not_depend_on_identity` et `test_profile_identity_does_not_change_judgement`.
- **Aucune écriture au registre.** `Governance.__call__` renvoie un `GovernanceOutcome`. C'est l'orchestrateur qui écrit le verdict (`ledger_write(ledger, "verdict", "gouvernance", ...)` dans `orchestrator.py`). Le test `test_governance_does_not_write_or_mutate_the_ledger` le vérifie.
- **Aucune recommandation d'achat ou de vente.** Une demande de conseil ou de transaction produit un `ScopeRefusal` (section 5.9).

## 2. Modules

| Fichier | Rôle |
|---|---|
| `__init__.py` | Exporte l'API publique : `Governance`, `GovernanceConfig`, `RiskLimits`, `GovernanceVerdict`, `Constraints`, `Reason`, `AssetAssessment`, `ScopeRefusal`, `derive_constraints`, les fonctions `check_*` et `apply_enrichment`. |
| `config.py` | `GovernanceConfig`, `RiskLimits`, `RISK_LEVELS` et les valeurs par défaut de la politique (limites, plafonds par classe, seuils). |
| `models.py` | Modèles Pydantic stricts (`extra="forbid"`) : `Reason`, `Constraints`, `AssetAssessment`, `ScopeRefusal`, `GovernanceVerdict`, et les types `Status`, `Severity`, `CheckName`. |
| `facts.py` | `build_facts` : regroupe les preuves du registre par actif (`AssetFacts`, `WindowMetrics`, `Refusal`). `ledger_notes` : relit les notes de l'orchestrateur. |
| `checks.py` | Les contrôles (fonctions pures), `derive_constraints`, `apply_enrichment`, `check_scope_limits`, `status_of`, `worst_status`, `ALTERNATIVE_TRIGGER_CODES`. |
| `governance.py` | La classe `Governance` : assemble les contrôles, choisit les alternatives, calcule le statut global, renvoie le `GovernanceOutcome`. |

`Governance(assets, config=None)` est appelable : `Governance(assets)(profile, request, ledger)` renvoie un `GovernanceOutcome(verdict=..., follow_up=[...])`, ce que l'`Orchestrator` attend.

## 3. Dérivation des contraintes

`derive_constraints(profile, cfg)` (`checks.py`) traduit le profil en un objet `Constraints`. C'est la même fonction pour tous les profils.

### 3.1 Algorithme

1. Le rang du niveau de risque vient de `RISK_RANK` (`orchestrator/profile.py`) : `faible` 0, `faible_modere` 1, `modere` 2, `modere_eleve` 3, `eleve` 4. `RISK_LEVELS` (`config.py`) est la liste de ces niveaux, du plus prudent au plus offensif.
2. **Seul ajustement présent dans le code : l'horizon.** Si `profile.horizon_years_max < cfg.short_horizon_years` (3 par défaut) et que le rang est supérieur à 0, le rang baisse d'un niveau. Un message est ajouté à `adjustments`.
3. Les limites sont lues dans `cfg.risk_limits[niveau]` et les plafonds dans `cfg.class_caps.get(niveau, {})`.

L'expérience de l'investisseur (`experience`), les objectifs, la devise et le `horizon_years_min` **n'entrent pas** dans `derive_constraints`. L'exemple 2 vérifie que deux profils qui ne diffèrent que par `experience` donnent les mêmes contraintes. L'expérience sert ailleurs, à l'explication (voir le README du dépôt).

Le test est strict : un horizon maximal de 3 ans ne resserre pas (`test_horizon_at_threshold_is_not_tightened`) ; 2 ans resserre. Le niveau `faible` ne peut pas être resserré (`test_lowest_level_cannot_be_tightened`). Le niveau appliqué est dans `Constraints.risk_level_applied`, et le motif dans `Constraints.adjustments`.

### 3.2 Limites par niveau de risque (`config.py`, `_default_limits`)

| Niveau | Volatilité max (`max_volatility`) | Perte max depuis un sommet (`max_drawdown`) | Poids max par actif (`max_single_asset_weight`) |
|---|---|---|---|
| `faible` | 0,08 | 0,10 | 0,10 |
| `faible_modere` | 0,15 | 0,20 | 0,15 |
| `modere` | 0,22 | 0,30 | 0,20 |
| `modere_eleve` | 0,32 | 0,45 | 0,25 |
| `eleve` | 0,65 | 0,80 | 0,35 |

La volatilité est annualisée. La perte maximale est un nombre positif (0,30 signifie -30 %). Le poids maximal par actif ne sert qu'aux scénarios de portefeuille (section 5.8).

### 3.3 Plafonds par classe d'actifs (`config.py`, `_default_class_caps`)

Seules les classes plafonnées sont listées. Une classe absente n'a pas de plafond (1,0). Un plafond de 0,0 veut dire « classe non permise ».

| Niveau | `crypto` | `etf_commodity` | `equity` | Classes permises |
|---|---|---|---|---|
| `faible` | 0,0 | 0,05 | 0,30 | toutes sauf `crypto` |
| `faible_modere` | 0,0 | 0,10 | 0,50 | toutes sauf `crypto` |
| `modere` | 0,10 | 0,15 | 0,70 | toutes |
| `modere_eleve` | 0,20 | 0,20 | 0,85 | toutes |
| `eleve` | 0,35 | 0,30 | (aucun plafond) | toutes |

Les classes `etf_bond` et `etf_equity` n'ont jamais de plafond. Les plafonds ne portent que sur les noms de classe écrits ici : `equity` ne couvre pas `etf_equity`. Les classes de l'univers de départ (`alphaforge_starter/assets_universe.csv`) sont `equity`, `etf_bond`, `etf_equity`, `etf_commodity` et `crypto`.

Un plafond strictement positif n'est appliqué qu'aux scénarios de portefeuille. Au niveau d'un actif seul, seul le plafond égal à 0,0 agit : il bloque (`CLASS_NOT_PERMITTED`, section 5.1).

## 4. Faits d'actif (`facts.py`)

`build_facts(evidence)` reçoit `ledger.evidence()` et renvoie un dictionnaire `{clé: AssetFacts}`. Il ne calcule rien.

- **Clé de regroupement.** `params["query"]` pour `asset_lookup`, `params["ticker"]` pour les autres outils. Une preuve sans clé (par exemple `correlation_matrix`) est ignorée ici.
- **Avis de l'analyste.** Les codes des `notices` de chaque preuve sont recopiés dans `AssetFacts.note_codes`, sans doublon, dans l'ordre d'apparition. Ils sont transmis tels quels dans `AssetAssessment.data_notes`.
- **Preuve refusée** (`status == "refused"`). Elle devient un `Refusal(tool, reason, message, evidence_id)`. Pour `asset_lookup`, on garde aussi `suggestions` et les tickers de `candidates` (requête ambiguë).
- **`asset_lookup` réussi.** Les métadonnées de l'actif vont dans `AssetFacts.asset` et l'identifiant de preuve dans `lookup_evidence_id`.
- **`compute_metrics` réussi.** Chaque appel devient un `WindowMetrics` : alias de fenêtre, début, fin, `volatility` (`annualized_volatility`), `drawdown_depth` (`max_drawdown.depth`, inférieur ou égal à 0), `peak_date`, `recovery_date`, `recovered`, `annualization_basis`, `evidence_id`, `as_of` et `base_currency` (lus dans `params`). Si `asset_lookup` n'a pas été consulté, on garde au moins la classe d'actifs.

La « pire fenêtre » n'est pas choisie dans `facts.py`. Chaque contrôle choisit la sienne : `check_risk_limits` prend la valeur la plus élevée de chaque mesure, `check_horizon_fit` prend la fenêtre la plus longue (section 5).

`AssetFacts.evidence_ids` renvoie l'identifiant de la consultation, puis ceux des fenêtres. C'est la liste citée par les raisons.

`ledger_notes(entries, code)` renvoie les charges utiles des notes de l'orchestrateur d'un code donné. `Governance` l'utilise pour `tool_unavailable`.

## 5. Les contrôles (`checks.py`)

Chaque contrôle est une fonction pure : il reçoit des données et renvoie des `Reason`. Une `Reason` contient `check`, `code`, `severity`, `message`, `measures` (les nombres cités) et `evidence_ids`. Le docstring du module pose la règle générale : seuls les contrôles de contraintes dures bloquent ; un dépassement de limite dérivée ou une donnée manquante est un avertissement.

Ordre d'appel par actif (`Governance.assess`) : `check_data_gaps`, `check_hard_constraints`, `check_soft_preferences`, `check_risk_limits`, `check_horizon_fit`, puis `apply_enrichment` si le registre contient des enregistrements d'enrichissement.

### 5.1 `check_hard_constraints`

**Ce qu'il vérifie.** Les `hard_exclusions` du profil et la classe d'actifs. Rien n'est jugé si l'actif est inconnu (`f.asset is None`).

La comparaison (`_matches`) ignore la casse et les espaces de bord. Pour `ticker`, elle accepte le ticker entier ou sa racine avant `.` ou `-` (`ry` correspond à `RY.TO`).

| Condition | Code | Sévérité |
|---|---|---|
| Exclusion `asset_class` qui correspond | `EXCLUDED_ASSET_CLASS` | `block` |
| Exclusion `sector` qui correspond | `EXCLUDED_SECTOR` | `block` |
| Exclusion `ticker` qui correspond | `EXCLUDED_TICKER` | `block` |
| Exclusion `theme` (toujours, pour un actif connu) | `THEME_EXCLUSION_UNVERIFIED` | `warn` |
| Plafond de la classe égal à 0,0 au niveau appliqué | `CLASS_NOT_PERMITTED` | `block` |

Le motif (`reason`) de l'exclusion est ajouté au message. `CLASS_NOT_PERMITTED` a pour mesures `asset_class`, `class_cap` (0,0) et `risk_level_applied`. Il dépend du niveau **appliqué** : un profil `modere` avec un horizon maximal d'un an est resserré à `faible_modere` et ne peut plus acheter de crypto (`test_short_horizon_can_make_a_class_not_permitted`).

Les thèmes ne bloquent pas ici : le jeu de départ n'a pas de donnée thématique. Sans enrichissement, l'avertissement reste. Avec enrichissement, `apply_enrichment` peut le lever ou le transformer en blocage (section 8).

### 5.2 `check_exclusion_vocabulary`

**Ce qu'il vérifie.** Une exclusion dure (`asset_class`, `sector`, `ticker`) dont la valeur ne désigne aucun actif de l'univers ne bloquerait rien, en silence. Ce contrôle le dit. Il est appelé une fois par demande (pas par actif), donc il apparaît dans `global_reasons`, même si l'actif demandé n'a aucun rapport (`test_flag_is_raised_even_for_an_unrelated_asset`).

| Condition | Code | Sévérité |
|---|---|---|
| La valeur ne correspond à aucun actif de l'univers (règle identique à `_matches`) | `EXCLUSION_VALUE_UNKNOWN` | `warn` |

Les exclusions de type `theme` sont ignorées ici : elles ont leur propre avertissement. Le contrôle ne corrige rien et n'applique rien à la place de l'utilisateur. Il propose des valeurs proches (`measures["suggestions"]`, 5 au plus, `_MAX_SUGGESTIONS`) :

1. inclusion de la saisie dans une valeur de l'univers, ou l'inverse (saisie d'au moins 3 caractères) ;
2. pour un `ticker`, inclusion dans le nom de l'actif ;
3. ressemblance orthographique de la valeur entière (`difflib.get_close_matches`, seuil `_CLOSE_STRING` = 0,75) ;
4. ressemblance mot à mot (mots d'au moins 4 lettres, seuil `_CLOSE_WORD` = 0,8), par exemple « Technologie » et « Information Technology ».

Sans suggestion, le message liste toutes les valeurs possibles si elles sont au plus 20 (`_MAX_LISTED`), sinon il renvoie à `assets_universe.csv`. `measures["known_values"]` contient la liste complète dans le premier cas, une liste vide dans le second.

### 5.3 `check_soft_preferences`

**Ce qu'il vérifie.** Les `soft_preferences` du profil. Rien n'est jugé si l'actif est inconnu. Une préférence qui ne correspond pas ne produit rien.

| Condition | Code | Sévérité |
|---|---|---|
| Préférence `theme` et ticker listé dans `cfg.theme_proxies[thème]` | `THEME_PROXY` | `info` |
| Préférence `theme` sans substitut pour cet actif | `THEME_DATA_MISSING` | `warn` |
| Préférence `asset_class`, `sector` ou `ticker` qui correspond | `PREFERENCE_MATCH` | `info` |

Avec la configuration par défaut, le seul substitut déclaré est `ESGV` pour le thème `esg`. `THEME_PROXY` précise que le fonds est filtré par son émetteur et que « ce n'est pas une cote ». Un autre thème (par exemple `eviter_controverses`) n'a pas de substitut : `ESGV` reçoit `THEME_DATA_MISSING` pour ce thème (exemple 5). `THEME_DATA_MISSING` a pour mesure `substitutes` (liste, possiblement vide). Une préférence ne produit jamais de blocage.

### 5.4 `check_risk_limits`

**Ce qu'il vérifie.** La volatilité et la perte maximale mesurées par l'analyste, comparées aux limites de `Constraints`. Rien n'est produit si l'actif n'a aucune fenêtre.

Pour chaque mesure, le contrôle prend la **pire fenêtre** (la valeur la plus élevée de la mesure parmi les fenêtres de l'actif). La comparaison est stricte : une valeur égale à la limite ne dépasse pas (`test_within_limits_is_silent_and_boundary_is_not_exceeded`). Pour la perte, la profondeur est prise en valeur absolue (`-drawdown_depth`).

| Condition | Code | Sévérité |
|---|---|---|
| Volatilité de la pire fenêtre supérieure à `max_volatility` | `VOLATILITY_ABOVE_LIMIT` | `warn` |
| Perte maximale de la pire fenêtre supérieure à `max_drawdown` | `DRAWDOWN_ABOVE_LIMIT` | `warn` |

Mesures : `metric`, `value`, `limit`, `ratio` (valeur divisée par limite), `window` (alias de la pire fenêtre), `per_window` (valeur par fenêtre) et `annualization_basis`. Les `evidence_ids` sont ceux de **toutes** les fenêtres. Les deux codes sont des avertissements : voir la section 12.

### 5.5 `check_horizon_fit`

**Ce qu'il vérifie.** Si le pire repli mesuré a duré plus longtemps que l'horizon **minimal** du profil (`horizon_years_min`).

- La fenêtre utilisée est la plus longue (`end - start` en jours).
- Si cette fenêtre n'a pas de `peak_date` (aucun repli), le contrôle ne produit rien.
- La fin du repli est `recovery_date` si elle existe, sinon la fin de la fenêtre. La durée est `(fin - peak_date).days / 365,25`.
- La comparaison est stricte (durée supérieure à l'horizon minimal).

| Condition | Code | Sévérité |
|---|---|---|
| Durée du repli supérieure à `horizon_years_min` | `RECOVERY_LONGER_THAN_HORIZON` | `warn` |

Si le repli n'est pas récupéré, le message le dit (« durée minimale ») : la durée réelle est au moins celle affichée. Mesures : `recovery_years`, `horizon_years_min`, `peak_date`, `end_date`, `recovered`, `max_drawdown`, `window`.

### 5.6 `check_data_gaps`

**Ce qu'il vérifie.** Ce qui manque pour un actif. Toutes les raisons sont des avertissements. Les trois premiers cas sont exclusifs : la fonction s'arrête au premier qui s'applique.

| Condition | Code | Sévérité |
|---|---|---|
| Actif inconnu et refus `asset_not_found` (suggestions reprises dans le message) | `OUT_OF_UNIVERSE` | `warn` |
| Actif inconnu et refus `ambiguous_query` (candidats dans `measures["candidates"]`) | `AMBIGUOUS_ASSET` | `warn` |
| Actif inconnu et tout autre refus de `asset_lookup` | `ASSET_NOT_IDENTIFIED` | `warn` |
| Un refus de `compute_metrics` (une raison par refus) | `METRICS_UNAVAILABLE` | `warn` |
| Actif connu, aucune fenêtre et aucun refus de `compute_metrics` | `NO_METRICS` | `warn` |

Un actif hors univers n'est donc jamais `conforme`. Les demandes (`request.tickers` et `request.unknown_tickers`) sont toutes évaluées, y compris celles que l'analyste n'a pas pu identifier.

Deux autres raisons de lacune ne viennent pas de ce contrôle : `TOOL_UNAVAILABLE` et `NO_ALTERNATIVE` (section 6, assemblées dans `governance.py`).

### 5.7 `check_diversification`

**Ce qu'il vérifie.** Les paires d'actifs **demandés** (les clés principales, pas les alternatives) dont les rendements ont été très corrélés. Il lit la preuve `correlation_matrix` de l'analyste, dans la matrice de référence (`value["reference"]`), et ne calcule aucune corrélation.

| Condition | Code | Sévérité |
|---|---|---|
| Corrélation de la paire supérieure ou égale à `cfg.high_correlation` (0,8) | `HIGH_CORRELATION` | `info` |

C'est une information, pas un avertissement : elle ne change pas le statut. Mesures : `a`, `b`, `corr`, `threshold`, `reference`, `n_obs`. La raison est globale (`global_reasons`).

### 5.8 `check_portfolio_limits`

**Ce qu'il vérifie.** Un scénario de portefeuille (preuve `portfolio_metrics`), comparé aux limites dérivées. Les preuves refusées sont ignorées. Le scénario est fictif : tout est un avertissement. La tolérance est de 1e-9.

| Condition | Code | Sévérité |
|---|---|---|
| Poids maximal d'un actif supérieur à `max_single_asset_weight` | `PORTFOLIO_WEIGHT_ABOVE_CAP` | `warn` |
| Poids d'une classe supérieur à son plafond, si le plafond est strictement positif | `PORTFOLIO_CLASS_CAP` | `warn` |
| Volatilité annualisée du scénario supérieure à `max_volatility` | `PORTFOLIO_VOLATILITY_ABOVE_LIMIT` | `warn` |
| Perte maximale du scénario supérieure à `max_drawdown` | `PORTFOLIO_DRAWDOWN_ABOVE_LIMIT` | `warn` |

Une classe à plafond nul n'est pas répétée ici : elle est déjà bloquée actif par actif (5.1). Une classe sans plafond n'est jamais signalée. Ces raisons ont `check="portfolio"` et sont globales. Même un profil à risque `eleve` a des plafonds (35 % de crypto) : `test_high_risk_does_not_mean_anything_goes_class_cap_applies_to_the_portfolio`.

### 5.9 `check_scope_limits`

**Ce qu'il vérifie.** Les signaux de périmètre (`request.scope_flags`, valeurs `asks_prediction`, `asks_transaction`, `asks_personal_advice`, définies dans `orchestrator/request.py`). Il ne produit pas de `Reason` mais des `ScopeRefusal` (champ `scope_refusals` du verdict). Un signal inconnu est ignoré.

| Signal | `kind` | Statut | Ce qui est proposé (`offered_instead`) |
|---|---|---|---|
| `asks_prediction` | `prediction` | `avertissement` | Description historique chiffrée et scénarios simulés étiquetés comme tels. |
| `asks_transaction` | `transaction` | `bloqué` | Analyse de l'actif et simulation, sans ordre ni exécution. |
| `asks_personal_advice` | `personal_advice` | `avertissement` | Ce que montrent les données, où l'actif cadre ou non avec le profil, et ce qui reste incertain. |

Ces statuts entrent dans le calcul du statut global (section 7.2). Une demande d'achat est donc `bloqué` même si l'actif est `conforme` (exemple 4).

### 5.10 `apply_enrichment`

Voir les sections 6 (codes) et 8 (règles).

### 5.11 Profil incohérent

`Governance.__call__` ajoute aussi aux raisons globales les problèmes de `validate_profile` (`orchestrator/tools.py`), avec `check="profile"`. Un problème de gravité `warning` devient `warn`, tout autre (`error`) devient `block`. Les codes produits par `validate_profile` sont :

| Code | Déclencheur (`orchestrator/tools.py`) |
|---|---|
| `NO_OBJECTIVES` | Aucun objectif déclaré. |
| `RISK_HORIZON_CONFLICT` | Rang de risque supérieur ou égal à `modere_eleve` (`HIGH_RISK_MIN_RANK`) et horizon maximal d'au plus 2 ans (`SHORT_HORIZON_MAX_YEARS`). |
| `RISK_OBJECTIVE_CONFLICT` | Risque `faible` ou `faible_modere` (`LOW_RISK_MAX_RANK`) avec l'objectif `croissance_elevee` ; ou risque supérieur ou égal à `modere_eleve` avec pour seul objectif `preservation_capital`. |
| `EXCLUSION_PREFERENCE_CONTRADICTION` | Même couple (type, valeur) à la fois exclu et préféré. Gravité `error` donc `block`. |

## 6. Codes de raison : tableau de référence

Tous les codes que le paquet peut produire. « Cible » indique où la raison apparaît : `actif` (`AssetAssessment.reasons`) ou `global` (`GovernanceVerdict.global_reasons`). Le statut d'un actif ou du verdict dépend uniquement de la sévérité (section 7.2).

| Code | Contrôle (`check`) | Fonction | Sévérité | Cible | Sens |
|---|---|---|---|---|---|
| `EXCLUDED_ASSET_CLASS` | `hard_constraints` | `check_hard_constraints` | `block` | actif | La classe d'actifs est exclue par le profil. |
| `EXCLUDED_SECTOR` | `hard_constraints` | `check_hard_constraints` | `block` | actif | Le secteur est exclu par le profil. |
| `EXCLUDED_TICKER` | `hard_constraints` | `check_hard_constraints` | `block` | actif | L'actif est exclu par le profil. |
| `CLASS_NOT_PERMITTED` | `hard_constraints` | `check_hard_constraints` | `block` | actif | Plafond de classe nul au niveau de risque appliqué. |
| `THEME_EXCLUSION_UNVERIFIED` | `hard_constraints` | `check_hard_constraints` | `warn` | actif | Exclusion thématique non vérifiable avec les données disponibles. |
| `EXCLUSION_VALUE_UNKNOWN` | `hard_constraints` | `check_exclusion_vocabulary` | `warn` | global | La valeur d'une exclusion ne désigne aucun actif de l'univers : elle ne s'applique à rien. |
| `THEME_PROXY` | `soft_preferences` | `check_soft_preferences` | `info` | actif | L'actif est le substitut déclaré du thème préféré (pas une cote). |
| `THEME_DATA_MISSING` | `soft_preferences` | `check_soft_preferences` | `warn` | actif | Aucune donnée pour vérifier l'alignement avec le thème préféré. |
| `PREFERENCE_MATCH` | `soft_preferences` | `check_soft_preferences` | `info` | actif | L'actif correspond à une préférence du profil. |
| `VOLATILITY_ABOVE_LIMIT` | `risk_limits` | `check_risk_limits` | `warn` | actif | Volatilité mesurée au-dessus de la limite dérivée. |
| `DRAWDOWN_ABOVE_LIMIT` | `risk_limits` | `check_risk_limits` | `warn` | actif | Perte maximale mesurée au-dessus de la limite dérivée. |
| `RECOVERY_LONGER_THAN_HORIZON` | `horizon_fit` | `check_horizon_fit` | `warn` | actif | Le pire repli a duré plus que l'horizon minimal du profil. |
| `OUT_OF_UNIVERSE` | `data_gaps` | `check_data_gaps` | `warn` | actif | Actif absent de l'univers de données. |
| `AMBIGUOUS_ASSET` | `data_gaps` | `check_data_gaps` | `warn` | actif | La désignation correspond à plusieurs actifs. |
| `ASSET_NOT_IDENTIFIED` | `data_gaps` | `check_data_gaps` | `warn` | actif | `asset_lookup` a refusé pour un autre motif. |
| `METRICS_UNAVAILABLE` | `data_gaps` | `check_data_gaps` | `warn` | actif | `compute_metrics` a refusé. |
| `NO_METRICS` | `data_gaps` | `check_data_gaps` | `warn` | actif | Aucune métrique produite : limites non vérifiées. |
| `TOOL_UNAVAILABLE` | `data_gaps` | `Governance.__call__` | `warn` | global | Une note de l'orchestrateur signale un outil non disponible. |
| `NO_ALTERNATIVE` | `data_gaps` | `Governance.__call__` | `info` | global | Un actif déclenche la recherche d'alternatives, mais aucune alternative admissible n'existe. |
| `HIGH_CORRELATION` | `diversification` | `check_diversification` | `info` | global | Paire demandée corrélée à 0,8 ou plus : diversification faible. |
| `PORTFOLIO_WEIGHT_ABOVE_CAP` | `portfolio` | `check_portfolio_limits` | `warn` | global | Poids d'un actif au-dessus du plafond par actif. |
| `PORTFOLIO_CLASS_CAP` | `portfolio` | `check_portfolio_limits` | `warn` | global | Poids d'une classe au-dessus de son plafond. |
| `PORTFOLIO_VOLATILITY_ABOVE_LIMIT` | `portfolio` | `check_portfolio_limits` | `warn` | global | Volatilité du scénario au-dessus de la limite. |
| `PORTFOLIO_DRAWDOWN_ABOVE_LIMIT` | `portfolio` | `check_portfolio_limits` | `warn` | global | Perte maximale du scénario au-dessus de la limite. |
| `EXCLUDED_THEME_CONTROVERSY` | `hard_constraints` | `apply_enrichment` | `block` | actif | Controverses documentées par une source de niveau A ou B, thème exclu. |
| `PREFERENCE_CONTRADICTED` | `soft_preferences` | `apply_enrichment` | `warn` | actif | Controverses documentées (A ou B) pour un thème seulement préféré. |
| `THEME_VERIFIED` | `data_gaps` | `apply_enrichment` | `info` | actif | Source A ou B sans controverse documentée (pas une garantie d'absence). |
| `CONTROVERSY_SIGNAL_LEVEL_C` | `data_gaps` | `apply_enrichment` | `warn` | actif | Signal qualitatif de niveau C à vérifier ; ne bloque jamais seul. |
| `ESG_DATA_NO_THRESHOLD` | `soft_preferences` | `apply_enrichment` | `warn` | actif | Cote ESG A ou B disponible, mais aucun seuil défini : alignement non jugé. |
| `ENRICHED_DATA` | `data_gaps` | `apply_enrichment` | `info` | actif | Rappel de chaque donnée enrichie, avec niveau, date de publication et source. |
| `ENRICHMENT_GAP` | `data_gaps` | `apply_enrichment` | `warn` | actif | Donnée d'enrichissement non comblée, avec le code de raison de la lacune. |
| `NO_OBJECTIVES`, `RISK_HORIZON_CONFLICT`, `RISK_OBJECTIVE_CONFLICT` | `profile` | `Governance.__call__` | `warn` | global | Incohérences internes du profil (section 5.11). |
| `EXCLUSION_PREFERENCE_CONTRADICTION` | `profile` | `Governance.__call__` | `block` | global | Un même critère est exclu et préféré (section 5.11). |

Refus de périmètre (`ScopeRefusal`, pas des `Reason`) : `prediction` (`avertissement`), `personal_advice` (`avertissement`), `transaction` (`bloqué`), voir 5.9.

## 7. Le verdict

### 7.1 Statuts et structure

`Status` (`models.py`) prend trois valeurs : `conforme`, `avertissement`, `bloqué`. `Severity` prend `block`, `warn`, `info`.

`GovernanceVerdict` :

| Champ | Contenu |
|---|---|
| `status` | Statut global. |
| `constraints` | `Constraints` : `profile_id`, `risk_level_applied`, `max_volatility`, `max_drawdown`, `max_single_asset_weight`, `class_caps`, `adjustments`. |
| `assets` | Une `AssetAssessment` par actif demandé : `ticker`, `status`, `reasons`, `data_notes`, `alternative_for` (vide pour un actif demandé). |
| `alternatives` | Des `AssetAssessment` pour les alternatives chiffrées, avec `alternative_for` rempli. |
| `global_reasons` | Raisons qui ne tiennent pas à un seul actif (profil, vocabulaire, outil indisponible, diversification, portefeuille, `NO_ALTERNATIVE`). |
| `scope_refusals` | Liste de `ScopeRefusal` (`kind`, `status`, `message`, `offered_instead`). |

Tous les modèles interdisent les champs supplémentaires (`extra="forbid"`). Chaque nombre cité dans un message figure dans `measures`, et `evidence_ids` renvoie à des preuves du registre (`test_every_cited_evidence_id_exists_in_the_ledger`).

### 7.2 Calcul du statut

- **Statut d'un actif** (`status_of`) : `bloqué` si au moins une raison a la sévérité `block` ; sinon `avertissement` si au moins une a `warn` ; sinon `conforme`. Les raisons `info` ne changent jamais le statut.
- **Statut global** (`worst_status`, `Governance.__call__`) : le pire de ces statuts, avec l'ordre `conforme` < `avertissement` < `bloqué` :
  1. les statuts des actifs **demandés** ;
  2. `status_of(global_reasons)` ;
  3. les statuts des `scope_refusals`.
- Les **alternatives n'entrent pas** dans le statut global (`test_alternatives_do_not_change_the_verdict_status`).
- Sans aucune entrée, `worst_status` renvoie `conforme`.

Dans les exigences d'`Agents.md`, l'outil se nomme `issue_verdict`. Dans le code, il n'existe pas de fonction de ce nom : la section `issue_verdict` de `checks.py` contient `status_of` et `worst_status`, et le verdict est assemblé à la fin de `Governance.__call__`.

### 7.3 Alternatives

**Déclencheurs** (`ALTERNATIVE_TRIGGER_CODES`, `checks.py`) : `EXCLUDED_ASSET_CLASS`, `EXCLUDED_SECTOR`, `EXCLUDED_TICKER`, `CLASS_NOT_PERMITTED`, `EXCLUDED_THEME_CONTROVERSY`, `VOLATILITY_ABOVE_LIMIT`, `DRAWDOWN_ABOVE_LIMIT`. Un actif demandé qui porte au moins un de ces codes est « signalé ». `RECOVERY_LONGER_THAN_HORIZON` et les lacunes de données ne déclenchent pas de recherche.

**Choix des candidats** (`Governance._follow_up`, `_peers`, `_admissible`) :

1. Les pairs d'un actif signalé sont les autres actifs du **même secteur** ; s'il n'y en a aucun, ceux de la **même classe d'actifs**. Ordre alphabétique, donc déterministe. Un actif hors univers n'a pas de pair.
2. On écarte les actifs déjà demandés et ceux déjà candidats.
3. On écarte les candidats déjà bloqués d'emblée : exclusion (classe, secteur, ticker) ou classe non permise (`check_hard_constraints` sur les métadonnées seules).
4. On garde les `cfg.max_alternatives` premiers, soit **4** par défaut (`config.py`), tous actifs signalés confondus.

Il n'y a pas de classement par performance ou par risque : le critère est l'appartenance au même secteur (ou à la même classe) et l'admissibilité.

**Comparabilité.** Les appels de relance reprennent les alias de fenêtre des actifs signalés (sinon `complet`), le même `as_of` et la même `base_currency` que l'analyse initiale. Chaque candidat donne un appel `compute_metrics` par alias.

**Une seule relance par appel de gouvernance.** `_follow_up` renvoie une liste vide dès qu'un actif non demandé a déjà des faits (des alternatives ont été chiffrées). Les alternatives chiffrées sont ensuite évaluées avec les mêmes contrôles que les actifs demandés (`Governance.assess`) ; `alternative_for` liste les actifs demandés non conformes dont l'alternative est un pair. Une alternative sans fenêtre n'est pas retenue.

**`NO_ALTERNATIVE`.** Si un actif est signalé, qu'aucune relance n'est demandée et qu'il n'y a pas d'alternative, la raison globale `NO_ALTERNATIVE` (`info`) est ajoutée, avec `measures["flagged"]`.

### 7.4 Boucle de retour vers l'analyste

`GovernanceOutcome.follow_up` est une liste de `ToolCall` (`compute_metrics`). C'est l'orchestrateur qui les exécute, dans `Orchestrator.run` (`orchestrator.py`) :

1. première évaluation, verdict écrit au registre ;
2. tant que `follow_up` n'est pas vide et que le compteur est inférieur à `MAX_FEEDBACK_ITERATIONS` (**2**), l'analyste exécute les appels (étapes `fb<itération>.<n>`), la gouvernance réévalue et un nouveau verdict est écrit ;
3. si l'enrichissement est branché, une relance unique chiffre les données d'enrichissement des alternatives, puis la gouvernance réévalue encore ;
4. si `follow_up` n'est toujours pas vide, une note `feedback_limit_reached` est écrite.

Le **dernier** verdict du registre fait foi. Dans l'exemple 3, deux verdicts sont écrits : celui sans alternatives, puis celui qui les contient. Les verdicts écrits sont des `GovernanceVerdict.model_dump(mode="json")` : des dictionnaires JSON, lisibles avec `ledger.entries("verdict")[-1].payload`.

## 8. Règles liées à l'enrichissement

`apply_enrichment(reasons, profile, ticker, records, cards)` n'est appelé que si le registre contient au moins un enregistrement d'enrichissement (`Governance.__call__`). Pour un actif sans enregistrement, les raisons restent inchangées. Les alternatives sont traitées de la même façon que les actifs demandés.

Les thèmes viennent de `enrichment/config.py` : `CONTROVERSY_THEMES = {"eviter_controverses", "controverses", "sans_controverse"}` et `ESG_THEMES = {"esg", "isr", "investissement_responsable", "durable"}`.

**Blocage seulement sur niveau A ou B.**

- `EXCLUDED_THEME_CONTROVERSY` (`block`) exige : une exclusion dure sur un thème de controverse, et un enregistrement `ok` du besoin `controversies`, de niveau A ou B, dont `value["controversies"]` n'est pas vide.
- Un enregistrement de **niveau C** ne produit que `CONTROVERSY_SIGNAL_LEVEL_C` (`warn`), s'il porte des `facts`. Le message dit qu'un signal tiré d'un texte « ne bloque jamais à lui seul ».
- Une préférence souple (et non une exclusion) contredite par une source A ou B donne `PREFERENCE_CONTRADICTED` (`warn`), jamais un blocage.
- L'exemple 8 montre le même cas avec les niveaux B puis C : `bloqué`, puis `avertissement`. Les tests d'intégration `test_documented_controversy_in_a_level_b_source_blocks_a_hard_exclusion`, `test_level_c_signal_never_blocks_on_its_own`, `test_an_unregistered_third_party_source_is_capped_at_c_and_cannot_block` et `test_third_party_data_without_methodology_is_capped_at_c` le vérifient. Le plafonnement d'une source tierce non enregistrée ou sans méthodologie au niveau C est fait par l'agent d'enrichissement, pas par la gouvernance.

**Levée des avertissements « non vérifiable ».** La fonction supprime (`drop`) :

- `THEME_EXCLUSION_UNVERIFIED` pour un thème de controverse exclu, si une donnée A ou B documente des controverses ou n'en documente aucune ;
- `THEME_DATA_MISSING` pour un thème de controverse préféré, dans les mêmes conditions ;
- `THEME_DATA_MISSING` pour un thème ESG préféré, si une cote ESG A ou B (`kind == "esg"`, besoin `esg`) existe.

Une donnée A ou B **sans** controverse documentée ajoute `THEME_VERIFIED` (`info`), dont le message précise que ce n'est pas une garantie d'absence.

**Pas de seuil ESG inventé.** Quand une cote ESG A ou B existe pour une préférence ESG, la gouvernance ajoute `ESG_DATA_NO_THRESHOLD` (`warn`) : « aucun seuil ESG n'est défini : l'alignement avec la préférence n'est pas jugé ». Aucune comparaison de cote n'a lieu dans le code. Une exclusion dure sur un thème ESG (et non de controverse) n'est jamais levée par `apply_enrichment` : `THEME_EXCLUSION_UNVERIFIED` reste.

**Lacunes.** Chaque enregistrement `lacune_non_comblee` devient un `ENRICHMENT_GAP` (`warn`), regroupé par couple (`kind`, `need`), avec la liste des codes de raison. Les avertissements « non vérifiable » restent dans ce cas (`test_a_failing_source_becomes_an_explicit_gap_not_a_guess`).

**Rappel de provenance.** Chaque enregistrement `ok` ajoute un `ENRICHED_DATA` (`info`) avec le type de donnée, le niveau, la date de publication et la source (nom de la fiche source, ou « source non documentée »). Les `evidence_ids` de ces raisons sont des `record_id` d'enrichissement.

**Les fondamentaux obtenus n'ont aucun effet sur le statut.** Vérifié de deux façons :

- Dans le code, aucun fichier de `governance/` ne contient le mot « fundamental ». Un enregistrement `fundamentals` de statut `ok` ne passe que par `apply_enrichment`, qui ne réagit qu'aux besoins `controversies` et `esg` et n'ajoute pour lui que `ENRICHED_DATA` (`info`), sans effet sur `status_of`.
- À l'exécution (exemple 9) : la demande « Évalue AAPL » pour `amira` donne `conforme` avec et sans enrichissement. Seule une raison `ENRICHED_DATA` de sévérité `info` s'ajoute avec l'enrichissement.

**Exception : une lacune de fondamentaux est un avertissement.** Quand les fondamentaux sont demandés mais introuvables (par exemple un titre canadien, ou XOM dans l'instantané), l'enregistrement `lacune_non_comblee` devient un `ENRICHMENT_GAP` (`warn`) comme toute autre lacune, et le statut passe à `avertissement` (exemple 10). La donnée absente change donc le statut ; une donnée présente ne le change pas. Aucun montant de fondamentaux n'est comparé à un seuil.

Le README du dépôt dit la même chose (« aucune règle de la gouvernance ne les utilise et aucun seuil n'est inventé »).

## 9. Configuration (`GovernanceConfig`, `config.py`)

Les valeurs sont des choix de politique, pas des faits. Elles dépendent du niveau de risque et jamais d'un persona. Un test passe une configuration personnalisée pour vérifier qu'elle est utilisée (`test_custom_config_is_used`).

| Paramètre | Valeur par défaut | Effet |
|---|---|---|
| `risk_limits` | Table de la section 3.2 (`RiskLimits` : `max_volatility`, `max_drawdown`, `max_single_asset_weight`) | Limites par niveau de risque. |
| `class_caps` | Table de la section 3.3 | Plafonds par classe et par niveau. 0,0 : classe non permise. Classe absente : pas de plafond. |
| `short_horizon_years` | 3 | Si `horizon_years_max` est strictement inférieur, les limites sont resserrées d'un niveau. |
| `theme_proxies` | `{"esg": ["ESGV"]}` | Substituts déclarés par thème préféré (`THEME_PROXY`). |
| `max_alternatives` | 4 | Nombre maximal d'alternatives demandées à l'analyste. |
| `high_correlation` | 0,8 | Corrélation à partir de laquelle `HIGH_CORRELATION` est signalé (comparaison `>=`). |

Constantes internes de `checks.py` (non configurables) : 5 suggestions au plus (`_MAX_SUGGESTIONS`), liste des valeurs possibles jusqu'à 20 (`_MAX_LISTED`), seuils de ressemblance 0,75 (`_CLOSE_STRING`) et 0,8 (`_CLOSE_WORD`), tolérance de 1e-9 pour les contrôles de portefeuille.

## 10. Exemples vérifiés

Chaque commande a été exécutée depuis `/workshop/datathon2026`. Les exemples 3 à 10 lisent les données réelles de `alphaforge_starter/`. Le résultat d'un exemple peut changer si les données ou la configuration changent.

### Exemple 1. Contraintes des trois profils d'exemple

```bash
cd /workshop/datathon2026 && ALPHAFORGE_LLM=0 python3 -W ignore -c "
from alphaforge.governance import GovernanceConfig, derive_constraints
from alphaforge.orchestrator import load_profile
cfg = GovernanceConfig()
for nom in ('robert', 'mateo', 'amira'):
    c = derive_constraints(load_profile(nom), cfg)
    print(nom, c.risk_level_applied, c.max_volatility, c.max_drawdown, c.max_single_asset_weight)
    print('  plafonds :', c.class_caps, '| ajustements :', c.adjustments)
"
```

Résultat :

```text
robert faible_modere 0.15 0.2 0.15
  plafonds : {'crypto': 0.0, 'etf_commodity': 0.1, 'equity': 0.5} | ajustements : []
mateo modere 0.22 0.3 0.2
  plafonds : {'crypto': 0.1, 'etf_commodity': 0.15, 'equity': 0.7} | ajustements : []
amira eleve 0.65 0.8 0.35
  plafonds : {'crypto': 0.35, 'etf_commodity': 0.3} | ajustements : []
```

Robert a un horizon maximal de 5 ans, donc aucun resserrement. Il n'a pas le droit à la crypto (plafond 0,0).

### Exemple 2. Resserrement par l'horizon, et absence d'effet de l'expérience

```bash
cd /workshop/datathon2026 && ALPHAFORGE_LLM=0 python3 -W ignore -c "
from alphaforge.governance import GovernanceConfig, derive_constraints
from alphaforge.orchestrator import load_profile
cfg = GovernanceConfig()
def niveau(risque, hmax, exp='debutant'):
    p = load_profile({'profile_id': 'x', 'horizon_years_min': 0, 'horizon_years_max': hmax, 'risk_tolerance': risque, 'experience': exp})
    return derive_constraints(p, cfg)
print(niveau('eleve', 2).risk_level_applied, niveau('eleve', 2).adjustments)
print(niveau('modere', 3).risk_level_applied)
print(niveau('faible', 1).risk_level_applied)
print(niveau('modere', 5, 'debutant') == niveau('modere', 5, 'avance'))
"
```

Résultat :

```text
modere_eleve ["Horizon d'au plus 2 an(s), inférieur à 3 : limites resserrées d'un niveau (« eleve » -> « modere_eleve »)."]
modere
faible
True
```

### Exemple 3. Évaluer un actif trop volatil, lire les raisons et les alternatives

```bash
cd /workshop/datathon2026 && ALPHAFORGE_LLM=0 python3 -W ignore -c "
from alphaforge.app.pipeline import AlphaForge
af = AlphaForge.from_directory('alphaforge_starter')
a = af.analyze('Évalue NVDA', 'robert', enrichment=False)
verdicts = a.result.ledger.entries('verdict')
v = verdicts[-1].payload
print('verdicts écrits :', len(verdicts), '| statut :', v['status'], '| niveau appliqué :', v['constraints']['risk_level_applied'])
for x in v['assets']:
    print(x['ticker'], x['status'], [(r['code'], r['severity']) for r in x['reasons']])
for x in v['alternatives']:
    print('alternative', x['ticker'], x['status'], x['alternative_for'], [r['code'] for r in x['reasons']])
r = v['assets'][0]['reasons'][0]
print(r['message'])
print({k: r['measures'][k] for k in ('metric', 'limit', 'window')})
"
```

Résultat :

```text
verdicts écrits : 2 | statut : avertissement | niveau appliqué : faible_modere
NVDA avertissement [('VOLATILITY_ABOVE_LIMIT', 'warn'), ('DRAWDOWN_ABOVE_LIMIT', 'warn')]
alternative AAPL avertissement ['NVDA'] ['VOLATILITY_ABOVE_LIMIT', 'DRAWDOWN_ABOVE_LIMIT']
alternative CSU.TO avertissement ['NVDA'] ['VOLATILITY_ABOVE_LIMIT', 'DRAWDOWN_ABOVE_LIMIT']
alternative MSFT avertissement ['NVDA'] ['VOLATILITY_ABOVE_LIMIT', 'DRAWDOWN_ABOVE_LIMIT']
alternative SHOP.TO avertissement ['NVDA'] ['VOLATILITY_ABOVE_LIMIT', 'DRAWDOWN_ABOVE_LIMIT', 'RECOVERY_LONGER_THAN_HORIZON']
```

Les deux dernières lignes de sortie sont le message de la première raison (« Volatilité annualisée de NVDA : 61,3 % (fenêtre « 2022 »), pour une limite de 15,0 % au niveau « faible_modere ». ») et ses mesures (`{'metric': 'annualized_volatility', 'limit': 0.15, 'window': '2022'}`). Le dépassement de volatilité est un avertissement : le statut n'est pas `bloqué`. Quatre alternatives sont proposées (`max_alternatives`), dans l'ordre alphabétique des pairs du secteur de NVDA. Aucune n'est conforme ici : la gouvernance propose des pairs admissibles (ni exclus, ni de classe non permise) et les chiffre, sans garantir qu'ils respectent les limites.

### Exemple 4. Un blocage de classe, et une demande de transaction

```bash
cd /workshop/datathon2026 && ALPHAFORGE_LLM=0 python3 -W ignore -c "
from alphaforge.app.pipeline import AlphaForge
af = AlphaForge.from_directory('alphaforge_starter')
v = af.analyze('Évalue BTC-USD', 'robert', enrichment=False).result.ledger.entries('verdict')[-1].payload
print(v['status'], v['assets'][0]['status'], [(r['code'], r['severity']) for r in v['assets'][0]['reasons']])
print([(r['code'], r['severity'], r['measures']) for r in v['global_reasons']])
v = af.analyze('Achète AAPL', 'amira', enrichment=False).result.ledger.entries('verdict')[-1].payload
print(v['status'], v['assets'][0]['status'], [(s['kind'], s['status']) for s in v['scope_refusals']])
"
```

Résultat :

```text
bloqué bloqué [('CLASS_NOT_PERMITTED', 'block'), ('VOLATILITY_ABOVE_LIMIT', 'warn'), ('DRAWDOWN_ABOVE_LIMIT', 'warn')]
[('NO_ALTERNATIVE', 'info', {'flagged': ['BTC-USD']})]
bloqué conforme [('transaction', 'bloqué')]
```

Pour Robert, la crypto est non permise. Le seul pair de BTC-USD (même secteur) est ETH-USD, de la même classe, donc non admissible : `NO_ALTERNATIVE`. Dans la seconde demande, AAPL est conforme pour Amira, mais la demande d'achat bloque le verdict global.

### Exemple 5. Préférences thématiques sans donnée

```bash
cd /workshop/datathon2026 && ALPHAFORGE_LLM=0 python3 -W ignore -c "
from alphaforge.app.pipeline import AlphaForge
af = AlphaForge.from_directory('alphaforge_starter')
v = af.analyze('Évalue ESGV et AAPL', 'mateo', enrichment=False).result.ledger.entries('verdict')[-1].payload
for x in v['assets']:
    print(x['ticker'], x['status'], [(r['code'], r['severity'], r['measures'].get('theme')) for r in x['reasons']])
"
```

Résultat :

```text
ESGV avertissement [('THEME_PROXY', 'info', 'esg'), ('THEME_DATA_MISSING', 'warn', 'eviter_controverses'), ('VOLATILITY_ABOVE_LIMIT', 'warn', None)]
AAPL avertissement [('THEME_DATA_MISSING', 'warn', 'esg'), ('THEME_DATA_MISSING', 'warn', 'eviter_controverses'), ('VOLATILITY_ABOVE_LIMIT', 'warn', None), ('DRAWDOWN_ABOVE_LIMIT', 'warn', None)]
```

Mateo a deux préférences thématiques. ESGV est le substitut déclaré de `esg`, mais pas de `eviter_controverses`.

### Exemple 6. Scénario de portefeuille

```bash
cd /workshop/datathon2026 && ALPHAFORGE_LLM=0 python3 -W ignore -c "
from alphaforge.app.pipeline import AlphaForge
af = AlphaForge.from_directory('alphaforge_starter')
v = af.analyze('Construis un scénario de portefeuille avec AAPL et MSFT', 'mateo', enrichment=False).result.ledger.entries('verdict')[-1].payload
print(v['status'], [x['status'] for x in v['assets']])
for r in v['global_reasons']:
    print(r['code'], r['severity'], round(r['measures']['value'], 3), r['measures']['limit'])
"
```

Résultat :

```text
avertissement ['avertissement', 'avertissement']
PORTFOLIO_WEIGHT_ABOVE_CAP warn 0.5 0.2
PORTFOLIO_CLASS_CAP warn 1.0 0.7
PORTFOLIO_VOLATILITY_ABOVE_LIMIT warn 0.243 0.22
```

Deux actifs à parts égales pèsent 50 % chacun, pour un plafond de 20 % au niveau `modere`. La classe `equity` pèse 100 % pour un plafond de 70 %.

### Exemple 7. Exclusion dont la valeur n'existe pas dans l'univers

```bash
cd /workshop/datathon2026 && ALPHAFORGE_LLM=0 python3 -W ignore -c "
from alphaforge.app.pipeline import AlphaForge
af = AlphaForge.from_directory('alphaforge_starter')
p = {'profile_id': 't', 'horizon_years_min': 5, 'horizon_years_max': 10, 'risk_tolerance': 'modere', 'objectives': ['croissance_moderee'],
     'hard_exclusions': [{'kind': 'sector', 'value': 'Technologie'}]}
v = af.analyze('Évalue AAPL', p, enrichment=False).result.ledger.entries('verdict')[-1].payload
r = v['global_reasons'][0]
print(v['status'], r['code'], r['severity'], r['measures']['suggestions'])
print([(x['ticker'], x['status'], [y['code'] for y in x['reasons']]) for x in v['assets']])
"
```

Résultat :

```text
avertissement EXCLUSION_VALUE_UNKNOWN warn ['Information Technology']
[('AAPL', 'avertissement', ['VOLATILITY_ABOVE_LIMIT', 'DRAWDOWN_ABOVE_LIMIT'])]
```

« Technologie » n'est pas un secteur de l'univers : AAPL n'est pas bloqué, et l'utilisateur est averti avec une suggestion.

### Exemple 8. Règle A ou B contre niveau C (`apply_enrichment`)

```bash
cd /workshop/datathon2026 && ALPHAFORGE_LLM=0 python3 -W ignore -c "
from alphaforge.governance import Reason, apply_enrichment
from alphaforge.governance.checks import status_of
from alphaforge.orchestrator import load_profile
p = load_profile({'profile_id': 'x', 'horizon_years_min': 5, 'horizon_years_max': 10, 'risk_tolerance': 'modere',
                  'hard_exclusions': [{'kind': 'theme', 'value': 'eviter_controverses'}]})
base = [Reason(check='hard_constraints', code='THEME_EXCLUSION_UNVERIFIED', severity='warn', message='m', measures={'theme': 'eviter_controverses'})]
cartes = {'c1': {'name': 'Fournisseur fictif'}}
def enr(niveau, **kw):
    r = dict(record_id='r1', ticker='T', status='ok', kind='esg', need='controversies', confidence=niveau,
             date_publication='2026-03-01', source_card_id='c1', value={'controversies': ['enquête X']}, facts=[])
    r.update(kw)
    return r
for libelle, r in [('niveau B', enr('B')), ('niveau C', enr('C', facts=[{'x': 1}]))]:
    out = apply_enrichment(base, p, 'T', [r], cartes)
    print(libelle, status_of(out), [(x.code, x.severity) for x in out])
"
```

Résultat :

```text
niveau B bloqué [('EXCLUDED_THEME_CONTROVERSY', 'block'), ('ENRICHED_DATA', 'info')]
niveau C avertissement [('THEME_EXCLUSION_UNVERIFIED', 'warn'), ('CONTROVERSY_SIGNAL_LEVEL_C', 'warn'), ('ENRICHED_DATA', 'info')]
```

Les enregistrements sont écrits à la main avec les champs que lit `apply_enrichment`. Ce n'est pas une donnée réelle.

### Exemple 9. Les fondamentaux ne changent pas le statut

```bash
cd /workshop/datathon2026 && ALPHAFORGE_LLM=0 python3 -W ignore -c "
from alphaforge.app.pipeline import AlphaForge
af = AlphaForge.from_directory('alphaforge_starter')
for avec in (False, True):
    a = af.analyze('Évalue AAPL', 'amira', enrichment=avec)
    v = a.result.ledger.entries('verdict')[-1].payload
    kinds = sorted({(e.payload['kind'], e.payload['status']) for e in a.result.ledger.entries('enrichment')})
    print('enrichissement', avec, v['status'], [(r['code'], r['severity']) for r in v['assets'][0]['reasons']], kinds)
"
```

Résultat :

```text
enrichissement False conforme [('PREFERENCE_MATCH', 'info')] []
enrichissement True conforme [('PREFERENCE_MATCH', 'info'), ('ENRICHED_DATA', 'info')] [('fundamentals', 'ok')]
```

Cet exemple s'appuie sur la chaîne de sources de l'enrichissement (SEC en direct si `ALPHAFORGE_SEC_USER_AGENT` est défini, sinon cache puis instantané versionné, voir le README du dépôt). Le résultat attendu pour le statut ne dépend pas de la source.

### Exemple 10. Une lacune de fondamentaux est un avertissement

```bash
cd /workshop/datathon2026 && ALPHAFORGE_LLM=0 python3 -W ignore -c "
from alphaforge.app.pipeline import AlphaForge
af = AlphaForge.from_directory('alphaforge_starter')
a = af.analyze('Évalue RY.TO et ses résultats', 'amira', enrichment=True)
v = a.result.ledger.entries('verdict')[-1].payload
print(v['status'], [(r['code'], r['severity']) for r in v['assets'][0]['reasons']])
print(sorted({(e.payload['kind'], e.payload['status']) for e in a.result.ledger.entries('enrichment')}))
"
```

Résultat :

```text
avertissement [('ENRICHMENT_GAP', 'warn'), ('ENRICHMENT_GAP', 'warn')]
[('filings', 'lacune_non_comblee'), ('fundamentals', 'lacune_non_comblee')]
```

Le titre est canadien : ni dépôts ni fondamentaux ne sont disponibles, et chaque lacune est nommée.

## 11. Tests associés

Tous sont dans `tests/governance/`, sauf mention contraire. Les tests n'utilisent ni AWS, ni la SEC, ni le réseau.

| Fichier | Fonctions de test | Ce qu'il couvre |
|---|---|---|
| `conftest.py` | (fixtures) | Univers synthétique de 10 actifs aux volatilités connues (rendements alternés +r / -r, un krach de 50 % jamais récupéré pour `CRASH`), `store`, `analyst`, `gov`, `profile_dict` et la fixture `run` qui branche la gouvernance sur l'orchestrateur. |
| `helpers_gov.py` | (outils) | `last_verdict`, `window` et `facts` : fabriques de faits pour les contrôles purs. |
| `test_gov_checks.py` | 42 | Contrôles purs avec des faits écrits à la main : `derive_constraints` (table de configuration, croissance des limites avec le risque, resserrement par l'horizon et son seuil, niveau le plus bas, indépendance de l'identité, configuration personnalisée), `check_hard_constraints`, `check_soft_preferences`, `check_risk_limits` (pire fenêtre, frontière non dépassée, jamais de blocage), `check_horizon_fit`, `check_data_gaps`, `check_scope_limits`, `status_of`, `worst_status`. |
| `test_gov_run.py` | 32 | Gouvernance branchée sur l'orchestrateur : verdicts de base, blocages (secteur, classe non permise, un actif bloqué ne bloque pas l'autre), périmètre, lacunes (actif inconnu, outil manquant, conflit de profil, thème sans donnée, repli non récupéré), alternatives et boucle de retour (une relance, mêmes fenêtres et même `as_of`, exclus non proposés, `NO_ALTERNATIVE`, `max_alternatives`, pairs par classe), propriétés générales (preuves citées présentes au registre, registre non modifié, déterminisme, profil jamais vu). |
| `test_gov_coherence.py` | 8 | Devise des alternatives (celle du profil), requête ambiguë jamais conforme, `HIGH_CORRELATION` (information seulement, seuil configurable), contrôles de portefeuille (poids, classe, volatilité, absence de raisons pour une simple comparaison). |
| `test_gov_exclusion_vocab.py` | 10 | `EXCLUSION_VALUE_UNKNOWN` : valeurs reconnues non signalées, suggestions (secteur, classe, ticker), liste des valeurs possibles, un signalement par exclusion, schéma du verdict, fonction pure. |
| `test_gov_facts.py` | 10 | `build_facts` : métadonnées, fenêtres reprises à l'identique, classe d'actifs sans consultation, ordre indépendant, refus et suggestions, avis dédoublonnés, `evidence_ids`, preuve sans clé ignorée. |
| `tests/integration/test_enrichment_integration.py` | 21 au total, dont les tests de gouvernance ci-dessous | Règles de l'enrichissement vues dans le verdict : blocage sur niveau B (`test_documented_controversy_in_a_level_b_source_blocks_a_hard_exclusion`), niveau C sans blocage, source non enregistrée ou sans méthodologie plafonnée à C, `THEME_VERIFIED`, préférence contredite en avertissement, décision indépendante du persona, publication postérieure à `as_of`, source en panne en `ENRICHMENT_GAP`, relance unique pour les alternatives puis réévaluation, absence de boucle, ordre du registre (besoins, enrichissement, analyste, verdict). Les autres tests du fichier portent sur l'explication et les fondamentaux. |

Lancer `python3 -m pytest tests/governance -q`, puis `python3 -m pytest tests/integration -q` (un dossier à la fois, voir `tests/README.md`).

## 12. Limites connues

- **Un dépassement de limite est un avertissement, jamais un blocage.** `VOLATILITY_ABOVE_LIMIT`, `DRAWDOWN_ABOVE_LIMIT` et les quatre codes `PORTFOLIO_*` ont la sévérité `warn` dans le code (`check_risk_limits`, `check_portfolio_limits`), et `test_risk_limits_never_block` le vérifie avec une volatilité de 5 (500 %). L'exemple 3 le montre : NVDA à 61,3 % de volatilité pour une limite de 15,0 % reste `avertissement`. Seuls bloquent les exclusions du profil, une classe à plafond nul et, avec enrichissement, une controverse de niveau A ou B sur un thème exclu. C'est un choix de politique : la gouvernance signale l'écart chiffré, l'investisseur décide. Si l'équipe veut bloquer, il faut changer la sévérité dans `checks.py` et les tests qui l'attendent.
- **Les limites sont des choix de politique.** Les tables de `config.py` ne sont pas des faits de marché. Elles sont justifiables, pas démontrables.
- **Le niveau de risque est le seul pilier, l'horizon un seul resserrement.** L'expérience, les objectifs et la devise n'influencent pas les limites. Le resserrement ne fait perdre qu'un niveau, jamais deux, et n'agit pas sur `faible`.
- **Le temps de récupération se mesure contre `horizon_years_min`.** `horizon_years_max` ne sert qu'au resserrement des limites. L'analyse du repli porte sur la fenêtre la plus longue, pas sur la pire.
- **Plafonds de classe : noms exacts.** `equity` ne couvre pas `etf_equity`. Les FNB d'actions et d'obligations n'ont aucun plafond. Un plafond strictement positif ne s'applique qu'aux scénarios de portefeuille.
- **Les thèmes sont peu vérifiables.** Le jeu de départ n'a pas de donnée thématique. Sans enrichissement, un thème exclu donne un avertissement permanent. Une exclusion ESG (par opposition aux controverses) n'est jamais levée ni transformée en blocage, même avec des données.
- **Le seul substitut déclaré est `ESGV` pour `esg`.** Il est dans `theme_proxies` et n'est pas déduit des données.
- **Alternatives.** Elles sont des pairs de secteur ou de classe, classés par ordre alphabétique, sans classement par risque ou performance. Il se peut qu'aucune ne respecte les limites (exemple 3), et elles n'influencent pas le statut. Une seule relance est demandée par appel de gouvernance, et l'orchestrateur ne dépasse pas 2 itérations.
- **Corrélation passée.** `HIGH_CORRELATION` est informatif et ne porte que sur les paires demandées. Une corrélation passée ne prédit rien.
- **Les thèmes ne sont pas validés.** Un thème inconnu des listes `ESG_THEMES` et `CONTROVERSY_THEMES` n'est pas refusé : il produit `THEME_DATA_MISSING` ou `THEME_EXCLUSION_UNVERIFIED` et ne peut jamais être vérifié.
- **Une lacune d'enrichissement change le statut.** Un `ENRICHMENT_GAP` est un avertissement, y compris pour les fondamentaux (exemple 10).

## Points non vérifiés

- Le comportement avec les sources SEC en direct (réseau) : l'exemple 9 a été exécuté sans identification SEC.
- Le chemin Bedrock de l'interprétation des demandes : tous les exemples sont exécutés avec `ALPHAFORGE_LLM=0` (interprétation par règles).
