# Analyste quantitatif (`alphaforge/quant/`)

Ce dossier contient l'agent « Analyste quantitatif » d'AlphaForge AI. Il transforme des fichiers de prix en **preuves chiffrées reproductibles** (objets `Evidence`) que l'orchestrateur écrit au registre de preuves. Il est écrit pour Python 3.9 et s'appuie sur `pandas`, `numpy` et `pydantic`.

Pour le contexte global (architecture à quatre agents, registre de preuves, interface, déploiement), voir [`README.md`](../../README.md) à la racine et [`Agents.md`](../../Agents.md) (section « Agent 2 : Analyste quantitatif »). Ce document ne les répète pas : il décrit le code de ce dossier.

Tous les exemples de ce document ont été exécutés depuis la racine du dépôt avec `ALPHAFORGE_LLM=0 python3 -W ignore -c "..."`. Les sorties montrées sont des extraits réels. Les dates et chiffres dépendent du jeu de départ `alphaforge_starter/` tel qu'il est dans le dépôt (données de 2021-11-12 à 2026-11-12, 31 actifs).

## Sommaire

1. [Rôle et interdits](#1-rôle-et-interdits)
2. [Vue d'ensemble des modules](#2-vue-densemble-des-modules)
3. [Le magasin de données (`DataStore`)](#3-le-magasin-de-données-datastore)
4. [Le contrat d'une preuve (`Evidence`)](#4-le-contrat-dune-preuve-evidence)
5. [Les fenêtres](#5-les-fenêtres)
6. [Les outils](#6-les-outils)
7. [Les métriques (`metrics.py`)](#7-les-métriques-metricspy)
8. [Configuration (`QuantConfig`)](#8-configuration-quantconfig)
9. [Garde-fous et choix de conception](#9-garde-fous-et-choix-de-conception)
10. [Exemples d'utilisation vérifiés](#10-exemples-dutilisation-vérifiés)
11. [Tests associés](#11-tests-associés)
12. [Limites connues](#12-limites-connues)

## 1. Rôle et interdits

L'agent calcule des faits sur le passé : rendements, volatilité, perte maximale, corrélations, rejeu d'une allocation fictive. Chaque résultat est une `Evidence` qui porte sa fenêtre, ses paramètres, l'empreinte des données lues, ses avertissements, ses hypothèses et ses limites.

Ce que l'agent ne fait jamais (vérifié dans le code de `alphaforge/quant/`) :

- **Aucune prédiction.** Les outils décrivent des données historiques. `portfolio_metrics` rejoue des poids sur des prix passés et étiquette sa preuve `label="scenario"`. Chaque outil de calcul ajoute la limite `LIMIT_PAST` (`tools/_common.py`) : « Les données historiques décrivent le passé ; elles ne prédisent pas les rendements futurs. »
- **Aucun accès réseau, aucun fichier hors du jeu de données.** Une recherche dans `alphaforge/quant/` ne trouve aucun import de `requests`, `urllib`, `socket`, `boto3`, `openai` ou `anthropic`, et aucun appel à `open(` ni à `os.environ`. Le seul accès disque est la lecture des CSV (`DataStore.from_directory`) et, si demandé, l'écriture de séries Parquet (`series_store.py`).
- **Aucun appel à un modèle de langage et aucun hasard.** Aucun usage de `random`, de `datetime.now` ni de `date.today` : la date d'analyse est le paramètre `as_of`, ou la dernière date des prix par défaut.
- **Aucun verdict de conformité.** L'agent ne compare rien au profil du client. C'est le rôle de la gouvernance.
- **Aucune correction silencieuse des données.** Un problème est signalé (notice, statut `avertissement` ou `insuffisant`), jamais réparé. Un saut de prix étiqueté n'est pas supprimé.

## 2. Vue d'ensemble des modules

| Fichier | Rôle |
|---|---|
| `__init__.py` | Importe `tools` (ce qui enregistre les huit outils) et exporte `DataStore`, `QuantConfig`, `Evidence`, `WindowSpec`, `TOOLS`, `run_tool`, `tool_schemas`. |
| `config.py` | `QuantConfig` (seuils, bases d'annualisation, alias de fenêtres), `WindowAlias`, `ExcludedPeriod`, `DEFAULT_DATA_DIR` (dossier `alphaforge_starter/`). |
| `datastore.py` | `DataStore` : lecture seule des CSV, résolution d'un actif, coupe à `as_of`, empreinte SHA-256, taux directeur. |
| `models.py` | Modèles pydantic : `WindowSpec`, `ResolvedWindow`, `Period`, `Notice`, `InputRef`, `Evidence`, `ToolInputError`. |
| `registry.py` | `TOOLS`, décorateur `@tool`, `tool_schemas()`, `run_tool()`, calcul de l'`evidence_id`, `build_evidence()`, `refused()`, codes d'erreur. |
| `windows.py` | `resolve_as_of()` et `resolve_window()` : alias ou dates explicites vers une fenêtre concrète, plafonnée à `as_of`. |
| `series.py` | `PriceSeries`, chargement des prix (`load_prices`), conversion de devise (`convert_series`), base d'annualisation (`periods_per_year`, `annualization_basis`, `calendar_kind`). |
| `series_store.py` | `SeriesStore` : les séries sont déposées ici et les outils ne renvoient qu'un `series_id`. Mémoire, ou Parquet si un dossier est fourni. |
| `metrics.py` | Calculs purs : rendements, volatilité, drawdown, pire jour, pire mois, rendements par année civile, Sharpe, version segmentée. |
| `tools/_common.py` | `prelude()` (résout `as_of`, actif, fenêtre), `fallback_params()`, `resolved_params()`, limites `LIMIT_PAST` et `LIMIT_SURVIVOR`. |
| `tools/asset_lookup.py` | Outil `asset_lookup`. |
| `tools/get_prices.py` | Outil `get_prices`. |
| `tools/data_quality.py` | Outil `data_quality_check`. |
| `tools/convert_currency.py` | Outil `convert_currency` et la constante `FX_LIMIT`. |
| `tools/compute_metrics.py` | Outil `compute_metrics`. |
| `tools/stress_window.py` | Outil `stress_window`. |
| `tools/correlation_matrix.py` | Outil `correlation_matrix`. |
| `tools/portfolio_metrics.py` | Outil `portfolio_metrics`. |

Versions des outils (champ `tool_version` de la preuve) : `asset_lookup` 1.1.0, `get_prices` 1.1.0, `data_quality_check` 1.1.0, `convert_currency` 1.1.0, `compute_metrics` 1.1.0, `stress_window` 1.0.0, `correlation_matrix` 1.0.0, `portfolio_metrics` 1.0.0.

## 3. Le magasin de données (`DataStore`)

### Chargement

`DataStore.from_directory(path=None, config=None, series_dir=None)` lit, dans `path` (par défaut `DEFAULT_DATA_DIR`, soit `alphaforge_starter/`) :

| Fichier | Usage |
|---|---|
| `assets_universe.csv` | Univers d'actifs (colonnes lues par le code : `asset_id`, `ticker`, `name`, `asset_class`, `market`, `currency`, `sector`). |
| `prices_daily.csv` | Prix quotidiens (`date`, `ticker`, `open`, `high`, `low`, `close`, `adj_close`, `volume`, `currency`). |
| `fx_usdcad_daily.csv` | Taux de change : seule la colonne `usdcad` est utilisée. |
| `corporate_actions.csv` | Dividendes et fractionnements (`ex_date`, `ticker`, `action`, `value`, `unit`). Lu sans condition. |
| `macro_rates.csv` | Facultatif. Sert au taux sans risque du Sharpe. |
| `sources_metadata.csv` | Facultatif. Sert à fabriquer les libellés de source (`source_label`). |

Le constructeur `DataStore(assets, prices, fx, actions, config, sources, macro, series_dir)` accepte aussi des DataFrames directement, ce que font les tests. Il lève `ValueError` dans trois cas : doublons `(date, ticker)` dans les prix, ticker de prix absent de l'univers, dates en double dans les taux de change. Les prix sont triés par `(ticker, date)`.

### Empreinte des données

`store.fingerprint` est un SHA-256 hexadécimal. Il combine un SHA-256 par table (actifs, prix, taux de change, opérations sur titres, et taux macro s'ils existent), calculé par `_hash_frame` sur les noms de colonnes et sur le hachage pandas des lignes. Il couvre **toutes** les données chargées, y compris celles postérieures à `as_of`. Il entre dans l'`evidence_id` (section 4). Deux chargements du même dossier donnent la même empreinte (vérifié : `DataStore.from_directory().fingerprint == store.fingerprint` est vrai).

`store.first_date` et `store.last_date` sont la première et la dernière date de prix de tout le jeu.

### `as_of`

`as_of` est la date d'analyse. Si elle est absente, `resolve_as_of` (`windows.py`) prend `store.last_date`. Toutes les lectures passent par des méthodes qui coupent à `as_of` :

- `price_frame(ticker, as_of)` : lignes de prix dont la date est `<= as_of` ;
- `fx_series(as_of)` : taux de change `<= as_of` ;
- `corporate_actions(ticker, as_of)` : opérations dont `ex_date <= as_of` ;
- `risk_free_rate(currency, as_of)` : taux directeur `<= as_of`, en fraction annuelle (valeur du fichier divisée par 100). CAD lit la série `V39079` (cible du taux à un jour de la Banque du Canada), USD lit `DFF` (taux effectif des fonds fédéraux). Renvoie `None` sans fichier macro, pour une autre devise, ou sans valeur.

`input_ref(file, symbol, as_of)` fabrique un `InputRef` : fichier, symbole et SHA-256 **des seules lignes lues jusqu'à `as_of`**. Les fichiers acceptés sont `prices_daily.csv`, `fx_usdcad_daily.csv`, `corporate_actions.csv`, `macro_rates.csv` et `assets_universe.csv`. Toute autre valeur lève `ValueError`.

### Recherche d'un actif

`store.resolve(query)` renvoie `(type, candidats)`. Les niveaux sont essayés dans cet ordre et le premier qui produit des candidats gagne :

1. `ticker` : symbole ou `asset_id` exact. Si la requête est un symbole nu (sans `.` ni `-`), les cotes qui partagent cette racine sont ajoutées aux candidats ;
2. `ticker_root` : racine du symbole (`SHOP` pour `SHOP.TO`) ;
3. `name` : nom complet normalisé ;
4. `name_first_word` : premier mot du nom (Apple, Shopify, Bitcoin).

La recherche ne complète jamais de mémoire. `store.asset(query)` renvoie l'actif seulement s'il y a exactement un candidat, sinon `None` (hors univers ou ambigu). `store.suggest(query, n=3)` propose les tickers les plus proches avec `difflib.SequenceMatcher`, en gardant les scores `>= 0.6`.

`store.peer_dates(ticker, as_of)` renvoie l'union des dates de cotation des actifs du même groupe de marché. Les groupes (`_market_group`) sont `crypto`, `CA` (actif non crypto coté en CAD) et `US` (le reste). `data_quality_check` s'en sert comme calendrier de référence.

### Séries (`series.py`, `series_store.py`)

Une série de prix est un `PriceSeries` (`ticker`, `currency`, `prices`, `field`, `converted_from`, `fx_filled_days`, `fx_dropped_days`, `fx_rates`).

- `load_prices(store, asset, window, as_of, field="adj_close")` extrait le champ demandé sur la fenêtre, convertit en `float` et retire les valeurs manquantes. Aucun remplissage ni alignement de calendrier.
- `convert_series(store, ps, target, as_of)` convertit les prix (section 6.4). Il refuse les devises hors `supported_currencies` (`unsupported_currency`) et une série déjà convertie (`double_conversion`). Si la série est déjà dans la devise cible, il la renvoie telle quelle.
- `SeriesStore.put(series, recipe, data_sha, parent=None)` renvoie un `series_id` de la forme `sr_` suivi des 12 premiers caractères hexadécimaux du SHA-256 de `{recipe, data}`. Le même appel redonne donc le même identifiant, et un identifiant déjà présent n'est pas réécrit.
- `SeriesStore.get(series_id)` renvoie une `StoredSeries`, ou lève `ToolInputError("unknown_series", ...)`. `series_id in store.series` est un test d'existence.
- Par défaut, tout reste en mémoire pour la durée de la session. Avec `series_dir`, chaque série est écrite en `<series_id>.parquet` (colonnes `date`, `price`, et `fx_rate` si convertie) avec ses métadonnées dans `<series_id>.json`, et relue à la demande après un redémarrage. Ce mode exige `pyarrow`, importé seulement si un dossier est fourni (exemple en section 10.5).

Les séries ne passent jamais par un modèle de langage : les outils renvoient un `series_id` et un résumé, jamais la série elle-même.

## 4. Le contrat d'une preuve (`Evidence`)

Toute sortie d'outil est une `Evidence` (`models.py`), construite par `build_evidence` (`registry.py`). Les modèles dérivent de `StrictModel` (`extra="forbid"`) : un champ inconnu est rejeté.

| Champ | Contenu |
|---|---|
| `evidence_id` | Identifiant déterministe, voir ci-dessous. |
| `tool`, `tool_version` | Nom et version de l'outil. |
| `data_fingerprint` | `store.fingerprint` au moment du calcul. |
| `as_of` | Date d'analyse résolue. |
| `params` | Paramètres **résolus** : ticker canonique, fenêtre avec dates, `as_of` en ISO. `compute_metrics` y consigne aussi la devise de base, le champ de prix (`adj_close`), la base d'annualisation, `annualization_min_days` et `include_sharpe`. |
| `window` | `ResolvedWindow` (alias, début, fin, périodes exclues) ou `None`. |
| `status` | `ok`, `warning` ou `refused`. |
| `refusal_reason` | Motif en minuscules (`asset_not_found`, `insufficient_data`...), conservé tel quel pour les lecteurs existants (la gouvernance). |
| `error_code` | Code typé du contrat (voir ci-dessous). |
| `n_obs` | Nombre d'observations utilisées. |
| `inputs` | Liste d'`InputRef` (fichier, symbole, SHA-256 des lignes lues jusqu'à `as_of`). |
| `value` | Dictionnaire de résultats propre à l'outil. `None` pour la plupart des refus. |
| `sources` | Libellés de sources (`source_label`). |
| `notices` | Liste d'avis `Notice(code, message, severity)`. |
| `warnings` | Champ calculé : messages des avis de gravité `warning`. |
| `assumptions`, `limits` | Hypothèses de calcul et limites à citer avec le chiffre. |
| `depends_on` | `evidence_id` des preuves dont celle-ci dépend (contrôle de qualité, fenêtres rejouées). |
| `label` | `fait` (défaut) ou `scenario`. Seul `portfolio_metrics` utilise `scenario`. |

### Calcul de l'`evidence_id`

`make_evidence_id` (`registry.py`) sérialise en JSON canonique (clés triées, séparateurs compacts) l'objet `{tool, version, params, data}`, où `data` est `store.fingerprint`. L'identifiant est `ev_` suivi des 16 premiers caractères hexadécimaux du SHA-256 de ce texte.

Conséquences vérifiées par les tests (`test_evidence_id_depends_on_params_data_and_tool_version`) :

- le même appel sur les mêmes données redonne le même `evidence_id` ;
- changer un paramètre, une donnée ou la version de l'outil change l'identifiant ;
- `ResolvedWindow.model_dump` omet `excluded` quand la liste est vide, pour que les fenêtres ordinaires gardent la même forme (et le même identifiant) qu'avant l'ajout des fenêtres segmentées ;
- l'empreinte porte sur **tout** le jeu : si des données postérieures à `as_of` changent, l'`evidence_id` change, alors que les chiffres et les `inputs` (coupés à `as_of`) restent identiques (`test_results_ignore_data_after_as_of`, `test_input_hash_ignores_data_after_as_of`).

### Statuts, avis et refus

- `refused` si l'outil a posé un `refusal_reason`.
- `warning` si au moins un avis a la gravité `warning` (gravité par défaut d'un `Notice`).
- `ok` sinon. Les avis de gravité `info` (par exemple `LARGE_MOVES`, `ANNUALIZATION_SKIPPED`, `SEGMENTED_WINDOW`, `DRAWDOWN_NOT_RECOVERED`) ne changent pas le statut.

`refused()` ajoute un avis dont le code est le motif en majuscules et dont le message est celui de l'erreur. `error_code_for` applique deux correspondances : `asset_not_found` devient `NOT_IN_UNIVERSE` et `insufficient_data` devient `INSUFFICIENT_DATA`. Tout autre motif devient simplement sa version en majuscules. Codes rencontrés dans le code des outils :

| `error_code` | Origine |
|---|---|
| `NOT_IN_UNIVERSE` | actif ou ticker absent de l'univers (tous les outils) |
| `AMBIGUOUS_QUERY` | `asset_lookup` : plusieurs candidats |
| `INSUFFICIENT_DATA` | `compute_metrics`, `correlation_matrix`, `portfolio_metrics` |
| `UNKNOWN_WINDOW_ALIAS` | alias absent de `window_aliases` |
| `WINDOW_AFTER_AS_OF` | la fenêtre commence après `as_of` |
| `WINDOW_FULLY_EXCLUDED` | la fenêtre est entièrement exclue |
| `NO_DATA_IN_WINDOW` | `get_prices`, `convert_currency` : aucun prix dans la fenêtre |
| `UNKNOWN_SERIES` | `series_id` inconnu |
| `UNSUPPORTED_CURRENCY` | devise hors `supported_currencies` |
| `DOUBLE_CONVERSION` | série déjà convertie |
| `CURRENCY_MISMATCH` | `source_currency` déclarée différente de la devise réelle |
| `ALREADY_IN_TARGET_CURRENCY` | la série est déjà dans la devise cible |
| `INVALID_THRESHOLD`, `NO_WINDOWS` | `stress_window` |
| `DUPLICATE_POSITION`, `INVALID_WEIGHTS` | `portfolio_metrics` |
| `NOT_ENOUGH_ASSETS` | `correlation_matrix` : moins de deux actifs distincts |

(`convert_series` peut aussi lever `unsupported_pair` pour une paire autre que USD/CAD ou CAD/USD ; avec `supported_currencies` à deux devises, ce cas n'est pas atteignable.)

### Appeler un outil : `run_tool`

```python
from alphaforge.quant import DataStore, run_tool, tool_schemas
store = DataStore.from_directory()
ev = run_tool(store, "compute_metrics", {"ticker": "NVDA", "window": {"alias": "2022"}})
```

`run_tool(store, name, arguments)` valide `arguments` contre le modèle pydantic de l'outil, puis l'exécute. Deux exceptions seulement, dans le cas d'un appel mal formé :

- `KeyError` si le nom d'outil est inconnu (le message liste les outils disponibles) ;
- `ValueError` si les arguments sont invalides (champ manquant, champ inconnu, type incorrect, fenêtre incohérente).

Tout le reste est une preuve `refused`, jamais une exception : actif hors univers, données insuffisantes, alias inconnu, etc. `tool_schemas()` renvoie pour chaque outil `{name, version, description, input_schema}`, avec le schéma JSON généré depuis le modèle pydantic (aucune duplication manuelle). `TOOLS` est le dictionnaire des `ToolSpec`.

## 5. Les fenêtres

`WindowSpec` (`models.py`) décrit la fenêtre demandée : soit un `alias`, soit `start` et `end` explicites, pas les deux. Un mélange, un seul bord, ou `start > end` lève une erreur de validation (donc `ValueError` via `run_tool`). `resolve_window` (`windows.py`) la transforme en `ResolvedWindow` (alias conservé pour la traçabilité, `start`, `end`, `excluded`).

Les alias sont définis dans `QuantConfig.window_aliases` (`config.py`, fonction `_default_aliases`). Un alias a une seule forme à la fois : dates fixes (`end` absent signifie `as_of`), `lookback_days`, ou `full`. Dates résolues ci-dessous pour `as_of` = 2026-11-12, dernière date du jeu fourni :

| Alias | Définition (`config.py`) | Début | Fin | Exclusion |
|---|---|---|---|---|
| `complet` | `full=True` : de la première date du jeu jusqu'à `as_of` | 2021-11-12 | 2026-11-12 | aucune |
| `2022` | du 2022-01-01 au 2022-12-31 | 2022-01-01 | 2022-12-31 | aucune |
| `hors_2022` | `full=True` avec `exclude` 2022-01-01 à 2022-12-31 | 2021-11-12 | 2026-11-12 | 2022 |
| `depuis_2023` | début 2023-01-01, fin = `as_of` | 2023-01-01 | 2026-11-12 | aucune |
| `1_an` | `lookback_days=365` | 2025-11-12 | 2026-11-12 | aucune |
| `3_ans` | `lookback_days=1095` | 2023-11-13 | 2026-11-12 | aucune |

Règles de résolution :

- Un alias inconnu est refusé (`UNKNOWN_WINDOW_ALIAS`), avec la liste des alias disponibles dans le message.
- Si `start > as_of`, refus `WINDOW_AFTER_AS_OF`.
- Si `end > as_of`, la fin est ramenée à `as_of` et l'avis `WINDOW_CLIPPED_TO_AS_OF` (gravité `warning`) l'indique : « aucune donnée future utilisée ».
- Les périodes exclues sont ramenées à l'intérieur de la fenêtre. Celles qui ne la recoupent pas disparaissent. Si une seule période exclue couvre toute la fenêtre, refus `WINDOW_FULLY_EXCLUDED`. Avec les alias par défaut et ce jeu de données, cette situation n'est pas atteignable ; le test `test_fully_excluded_window_is_refused` la provoque avec un alias ajouté à la configuration.

### Fenêtre segmentée « hors 2022 »

Pour `hors_2022`, la fenêtre va de la première date à `as_of` et porte `excluded = [2022-01-01 à 2022-12-31]`. L'exclusion s'applique aux **rendements datés** dans la période, pas aux prix. Aucun faux rendement n'enjambe le trou : le premier rendement après 2022 est un vrai rendement d'un jour de bourse (31 déc. 2021 puis 3 janv. 2023 ne sont jamais reliés). Détails de calcul en section 7. Si `as_of` tombe en 2022, l'exclusion est ramenée à `as_of` et aucune donnée de 2022 n'est lue (`test_exclusion_is_clipped_to_the_window_and_as_of`).

Les périodes exclues viennent uniquement des alias de configuration ; une fenêtre explicite `start`/`end` n'en porte jamais.

## 6. Les outils

Tous les outils prennent un `as_of` facultatif et renvoient une `Evidence`. Les paramètres sont des modèles `StrictModel` : tout champ inconnu est rejeté.

Pour les outils qui prennent un `ticker` (`get_prices`, `data_quality_check`, `compute_metrics`, `stress_window`), la fonction commune `prelude` (`tools/_common.py`) résout `as_of`, l'actif (via `store.asset`) et la fenêtre. Un actif introuvable ou ambigu donne `NOT_IN_UNIVERSE`, avec des suggestions si `store.suggest` en trouve. Le message cite « 31 actifs » : ce nombre est écrit en dur dans `_common.py`.

### 6.1 `asset_lookup`

**Objectif.** Confirmer qu'un actif existe dans l'univers et décrire sa couverture.

**Paramètres.** `query` (symbole ou nom libre, obligatoire), `as_of` (défaut : dernière date du jeu).

**`value`.** `in_universe`, puis `asset_id`, `ticker`, `name`, `asset_class`, `market`, `currency`, `sector`, `match_type` (`ticker`, `ticker_root`, `name` ou `name_first_word`), `n_rows`, `n_obs_available` (même valeur que `n_rows`, nom historique lu par d'autres agents), `first_date`, `last_date`. `n_obs` de la preuve vaut `n_rows`. Les `inputs` sont `assets_universe.csv` et `prices_daily.csv`.

**Refus.**

- `AMBIGUOUS_QUERY` : plusieurs candidats. `value` contient `in_universe: True`, `ambiguous: True` et `candidates` (métadonnées de chaque candidat). L'appelant tranche.
- `NOT_IN_UNIVERSE` : aucun candidat. `value` contient `in_universe: False` et `suggestions`.

**Limite consignée.** Les secteurs sont attribués par les organisateurs et simplifiés ; pour un FNB, ils décrivent l'exposition.

### 6.2 `get_prices`

**Objectif.** Extraire une série de prix quotidiens sur une fenêtre, dans sa devise de cotation, sans remplissage ni conversion, et la déposer dans le magasin de séries.

**Paramètres.** `ticker`, `window` (obligatoires), `field` (`adj_close` par défaut, ou `close`), `as_of`.

**`value`.** `series_id`, `ticker`, `currency`, `field`, `n_obs`, `first_date`, `last_date`, `first_price`, `last_price`, `truncated`. Jamais la série elle-même.

**Avis et refus.**

- `WINDOW_TRUNCATED_TO_COVERAGE` (`warning`) : la fenêtre demandée dépasse la couverture de l'actif ; la série est tronquée et l'avis donne les dates demandées et disponibles. `truncated` vaut alors `True`.
- `CLOSE_NOT_DIVIDEND_ADJUSTED` (`info`) : avec `field="close"`. Le message de l'outil précise que `close` est ajusté des fractionnements seulement et recommande `adj_close` pour les rendements.
- Refus `NO_DATA_IN_WINDOW` si aucun prix n'est présent ; refus du `prelude` (actif, alias, `as_of`).

**Règle.** La recette de la série est `{kind: native, ticker, field, start, end, as_of}` ; le `series_id` en dépend et de l'empreinte des lignes de prix lues.

### 6.3 `data_quality_check`

**Objectif.** Dire dans quel état sont les données d'un actif sur une fenêtre. `compute_metrics`, `correlation_matrix` et `portfolio_metrics` l'appellent d'abord.

**Paramètres.** `ticker`, `window`, `as_of`.

**`value`.** `ticker`, `quality` (`ok`, `avertissement` ou `insuffisant`), `n_obs`, `expected_obs`, `longest_missing_run`, `coverage_ratio`, `first_date`, `last_date`, `checks` (liste de `{name, passed, detail}`), `n_large_moves`, `large_moves` (les 20 plus grands au plus), `corporate_actions` (`dividends` et `splits`).

Le statut de la preuve reste `ok` / `warning` / `refused` ; `value.quality` est un libellé distinct. `insuffisant` est le seul niveau qui bloque les calculs suivants.

**Contrôles** (seuils lus dans `QuantConfig`, voir section 8) :

| Contrôle (`checks[].name`) | Condition de signalement | Code d'avis | Niveau |
|---|---|---|---|
| `observations` | 0 observation | `NO_DATA` | insuffisant |
| | moins de `min_obs_insufficient` (60) | `INSUFFICIENT_OBSERVATIONS` | insuffisant |
| | moins de `min_obs_warning` (120) | `FEW_OBSERVATIONS` | avertissement |
| `couverture` | couverture sous `coverage_insufficient` (0,90) | `LOW_COVERAGE` | insuffisant |
| | couverture sous `coverage_warning` (0,98) | `MISSING_DAYS` | avertissement |
| `debut_fenetre` | première observation à plus de `edge_gap_days` (5) jours du début de fenêtre | `LATE_START` si aucune donnée antérieure n'existe, sinon `WINDOW_START_GAP` | avertissement |
| `fin_fenetre` | dernière observation à plus de 5 jours de la fin | `WINDOW_END_GAP` | avertissement |
| `sequence_manquante` | plus de 3 jours de cotation de référence consécutifs manquants (3 est écrit en dur) | `MISSING_RUN` | avertissement |
| `trous_internes` | écart entre deux observations supérieur à `internal_gap_days_exchange` (5) ou `internal_gap_days_crypto` (1) | `INTERNAL_GAP` | avertissement |
| `prix_non_positifs` | `close` ou `adj_close` nul ou négatif | `NON_POSITIVE_PRICE` | insuffisant |
| `prix_manquants` | `close` ou `adj_close` manquant | `NAN_PRICES` | avertissement |
| `devise_des_lignes` | devise d'une ligne différente de celle de l'univers | `CURRENCY_MISMATCH` | avertissement |
| `adj_close_vs_close` | `adj_close` supérieur à `close` de plus de `adj_close_over_close_tolerance` (1 %) | `ADJ_CLOSE_ABOVE_CLOSE` | avertissement |
| `volume_nul` | au moins un jour à volume nul | `ZERO_VOLUME` | avertissement |
| `fractionnement_<date>` | ratio strictement entre 1 et 1,5 | `SPLIT_RATIO_UNUSUAL` | avertissement |
| | ratio d'au moins 1,5 et `close` qui chute d'au moins 0,8 × (1 − 1/ratio) le jour de l'opération | `SPLIT_NOT_ADJUSTED` | avertissement |

Le code ajoute aussi l'avis `DATA_INSUFFICIENT` quand le niveau final est `insuffisant`.

**Sauts suspects.** Un rendement quotidien sur `adj_close` est étiqueté s'il dépasse `large_move_exchange` (0,25) ou `large_move_crypto` (0,40) en valeur absolue, **ou** `large_move_sigma` (3,0) écarts-types des rendements de la fenêtre. Chaque entrée de `large_moves` porte `date`, `return`, `z` et `trigger` (`fixe`, `sigma` ou `fixe+sigma`). Ils sont triés par amplitude décroissante, limités à 20 (valeur en dur), et le nombre total est dans `n_large_moves`. L'avis `LARGE_MOVES` est de gravité `info` : un saut étiqueté ne change ni le niveau de qualité ni le statut. Les sauts ne sont jamais supprimés des calculs.

**Couverture.** `expected_obs` est le nombre de dates de `store.peer_dates` (union des dates des actifs du même groupe de marché) dans la fenêtre. `coverage_ratio = n_obs / expected_obs`. Un actif seul dans son groupe est donc comparé à ses propres dates (déduit du code ; non exécuté).

**Limite consignée.** Le contrôle détecte des anomalies visibles dans les séries ; il ne certifie pas l'exactitude des cours.

Cas réels du jeu de départ, vérifiés par `test_known_data_issues_are_flagged` : `CNQ.TO` sur `2022` signale `SPLIT_RATIO_UNUSUAL` (ratio 1,021 le 2022-08-22) et `ZST.TO` sur `complet` signale `ZERO_VOLUME`.

### 6.4 `convert_currency`

**Objectif.** Exprimer une série dans une autre devise (CAD ou USD), prix par prix, avec le taux du même jour.

**Paramètres.** Deux formes, exclusives :

- `series_id` (série produite par un autre outil) ;
- ou `ticker` et `window` (la série native `adj_close` est alors créée et déposée).

Plus `source_currency` (facultatif, comparé à la devise réelle de la série), `target_currency` (obligatoire), `as_of`. Fournir les deux formes, ou aucune, est une erreur de validation (`ValueError`). Avec `series_id` et sans `as_of`, l'outil reprend l'`as_of` de la recette de la série (lu dans le code, non exécuté).

**Règles de calcul** (`convert_series`, `series.py`) :

- USD vers CAD : prix × taux. CAD vers USD : prix ÷ taux. Le taux est `usdcad` (CAD par USD).
- Le taux du même jour est utilisé. Sinon, le dernier taux connu est **reporté vers l'avant**, au plus `ffill_limit_days` (5) jours civils après la dernière observation du taux, jamais le taux suivant (pas de `bfill`).
- Un prix sans taux dans cette limite est écarté (`fx_dropped_days`). Un prix converti avec un taux reporté est compté dans `fx_filled_days`.
- Les rendements se calculent ensuite sur les prix convertis, jamais l'inverse.

**`value`.** `series_id` (nouvelle série), `parent_series_id`, `ticker`, `from_currency`, `to_currency`, `n_obs_in`, `n_obs_out`, `fx_forward_filled_days`, `fx_dropped_days`, `fx_rates_used` (`pair`, `first`, `last`, `min`, `max`), `first_date`, `last_date`, prix natif et converti au début et à la fin.

**Avis.** `FX_FORWARD_FILLED` (`warning`) et `FX_DROPPED_DAYS` (`warning`). Limite consignée : `FX_LIMIT` (taux Banque du Canada, un par jour, publié vers 16 h 30 ET d'après `sources_metadata.csv`, écart d'heure avec la clôture et avec les jours UTC de la crypto non corrigé).

**Refus.** `UNKNOWN_SERIES`, `NO_DATA_IN_WINDOW`, `CURRENCY_MISMATCH`, `DOUBLE_CONVERSION`, `ALREADY_IN_TARGET_CURRENCY` (message : un FNB en CAD qui détient des actions américaines porte déjà son exposition au change), `UNSUPPORTED_CURRENCY`, plus les refus du `prelude` pour la forme `ticker` + `window`.

### 6.5 `compute_metrics`

**Objectif.** Rendement total et annualisé, volatilité annualisée, drawdown maximal, pire jour, pire mois, rendements par année civile, en devise native **et** en devise de base, avec l'effet de change.

**Paramètres.** `ticker`, `window` (obligatoires), `base_currency` (défaut : `default_base_currency`, `CAD`), `as_of`, `include_sharpe` (`False` par défaut).

**Déroulement** (`compute_metrics.py`) :

1. Résolution via `prelude` (refus possibles : actif, alias, `as_of`).
2. Appel de `data_quality_check` sur la même fenêtre. Ses avis sont repris (sauf `WINDOW_CLIPPED_TO_AS_OF`), son `evidence_id` va dans `depends_on`. Si le niveau est `insuffisant`, refus `INSUFFICIENT_DATA`, sans `value`. Les avis de qualité figurent dans la preuve refusée (exemple en section 10.6).
3. Chargement de `adj_close` et conversion vers la devise de base. Une devise non prise en charge est refusée.
4. Refus `INSUFFICIENT_DATA` s'il reste moins de `min_obs_insufficient` (60) prix après conversion.
5. Fenêtre segmentée : refus `INSUFFICIENT_DATA` s'il reste moins de 60 rendements hors des périodes exclues. Calcul par `metric_block_segmented`. Sinon `metric_block`. Les deux blocs (natif et base) sont calculés.
6. Dépôt des séries : `series_id_native` et `series_id_base` (identiques si aucune conversion n'a eu lieu).

**`value`.** `ticker`, `asset_class`, `native_currency`, `base_currency`, `currency_converted`, `calendar` (`crypto` ou `exchange`), `periods_per_year`, `annualization_basis`, `n_prices`, `n_returns`, `segmented`, `first_date`, `last_date`, `sharpe`, `series_id_native`, `series_id_base`, `native` et `base` (blocs de métriques, section 7), `fx_effect`, `data_quality` (`quality` et `evidence_id`).

`fx_effect.total_return` est le rendement total en devise de base moins le rendement total natif. `fx_effect.annualized_return` est la différence des rendements annualisés, ou `None` si l'un des deux est `None`. Pour un actif déjà en devise de base, la différence est nulle (exemple vérifié : `RY.TO` donne 0,0).

**Annualisation.** Base de périodes par an selon le calendrier de l'actif (`series.periods_per_year`) : `periods_per_year_crypto` (365) pour une crypto, `periods_per_year_exchange` (252) sinon. Chaque actif est évalué sur **son propre calendrier**, sans remplissage. Le message `annualization_basis` le dit en clair (« 252 jours de bourse par an »). Un avertissement dans les hypothèses rappelle de ne pas comparer sans le dire la volatilité d'une crypto (365) à celle d'un actif coté (252).

**Avis.**

| Code | Gravité | Condition |
|---|---|---|
| `FX_FORWARD_FILLED` | warning | des taux ont été reportés |
| `FX_DROPPED_DAYS` | warning | des jours sont exclus faute de taux |
| `SEGMENTED_WINDOW` | info | fenêtre segmentée |
| `ANNUALIZATION_SKIPPED` | info | période observée de moins de `annualization_min_days` (350) jours civils |
| `DRAWDOWN_NOT_RECOVERED` | info | le drawdown maximal n'est pas récupéré en fin de fenêtre |
| `SHARPE_UNAVAILABLE` | warning | Sharpe demandé, aucun taux directeur pour la devise de base |
| `SHARPE_SKIPPED` | warning | Sharpe demandé, période de moins de 350 jours |
| avis de `data_quality_check` | selon l'avis | repris tels quels |

**Sharpe (facultatif).** Si `include_sharpe=True`, que le taux directeur de la devise de base existe et que la période est annualisable, `value.sharpe` contient `{optional: True, ratio, n_returns, n_dropped_no_rate, mean_risk_free_annual, risk_free_series}` (calcul en section 7) ; `macro_rates.csv` s'ajoute aux `inputs` et deux limites propres au Sharpe sont ajoutées. Sinon `value.sharpe` vaut `None`. `include_sharpe` entre dans `params`, donc dans l'`evidence_id`.

**Hypothèses consignées** : rendements simples sur `adj_close` (dividendes réinvestis), formule d'annualisation, première période partielle, calendrier propre à l'actif, effet de change en points de rendement, drawdown mesuré dans la fenêtre seulement. **Limites** : `LIMIT_PAST`, `LIMIT_SURVIVOR` (biais du survivant : l'univers a été choisi en 2026 parmi des titres aujourd'hui importants), et `FX_LIMIT` si une conversion a eu lieu.

### 6.6 `stress_window`

**Objectif.** Rejouer `compute_metrics` sur plusieurs fenêtres et dire si une mesure est **stable** ou **sensible** à la période.

**Paramètres.**

| Paramètre | Défaut | Rôle |
|---|---|---|
| `ticker` | obligatoire | actif |
| `tool` | `compute_metrics` | seul outil rejouable pour l'instant |
| `windows` | `None` | liste de `WindowSpec` ; `None` prend `stress_default_windows` (`complet`, `2022`, `hors_2022`, `1_an`, `3_ans`) |
| `metric` | `annualized_volatility` | ou `max_drawdown` |
| `threshold` | `None` | `None` prend `stress_threshold` (0,25) |
| `min_abs_spread` | `None` | `None` prend `stress_min_abs_spread` (0,02) ; `0` désactive le plancher |
| `base_currency` | `None` | `None` prend `CAD` |
| `as_of` | `None` | dernière date du jeu |

**Règle de décision.** Chaque fenêtre donne une mesure : la volatilité annualisée en devise de base, ou la perte maximale en valeur positive (`-max_drawdown.depth`). Avec les mesures chiffrées `lo` (la plus petite) et `hi` (la plus grande) :

- écart relatif `(hi − lo) ÷ lo` et écart absolu `hi − lo` ;
- `floor_applied` est vrai si l'écart relatif dépasse le seuil **et** l'écart absolu est sous le plancher ;
- verdict `sensible` si l'écart relatif dépasse le seuil et que le plancher ne s'applique pas ; sinon `stable`.

Le plancher absolu évite qu'un écart relatif élevé sur une mesure presque nulle (0,4 % contre 0,8 %) soit présenté comme une vraie sensibilité. Verdict `indetermine` (avis `STRESS_UNDETERMINED`) s'il y a moins de deux fenêtres chiffrées, ou si la mesure minimale est nulle ou négative.

**`value`.** `ticker`, `replayed_tool`, `metric`, `threshold`, `verdict`, `relative_spread`, `absolute_spread`, `min_abs_spread`, `floor_applied`, `min_window`, `max_window`, `n_windows`, `n_computed`, `rows`, et `summary` (phrase prête à citer, quand l'écart est défini). Chaque ligne de `rows` contient le libellé de fenêtre, l'`evidence_id` de la preuve `compute_metrics` rejouée, son statut, les dates et périodes exclues, puis la mesure (`value`), volatilité, drawdown, rendement total et annualisé, nombre de rendements et base d'annualisation. Une fenêtre refusée est consignée avec `error_code` et `message`, et ajoute l'avis `WINDOW_NOT_COMPUTED`.

Tous les `evidence_id` rejoués sont dans `depends_on`. `n_obs` de la preuve est le nombre de fenêtres chiffrées. Le statut de la preuve `stress_window` dépend de ses propres avis seulement, pas de ceux des fenêtres rejouées (exemple en 10.3 : statut `ok` alors que chaque ligne est `warning`).

**Refus.** `INVALID_THRESHOLD` (seuil `<= 0`, ou plancher `< 0`), `NO_WINDOWS` (liste vide), `NOT_IN_UNIVERSE`.

**Cas réel vérifié.** `ZST.TO` : écart relatif de 110 % mais écart absolu de 0,004. Le plancher s'applique, le verdict est `stable`. Avec `min_abs_spread=0`, il devient `sensible`.

### 6.7 `correlation_matrix`

**Objectif.** Corrélations de Pearson des **rendements** entre 2 à 15 actifs, en devise de base, avec un alignement de calendrier explicite.

**Paramètres.** `tickers` (2 à 15 éléments), `window`, `frequency` (`auto` par défaut, `daily` ou `weekly`), `base_currency` (défaut `CAD`), `as_of`.

**Déroulement.**

1. Les doublons sont retirés (`dict.fromkeys`), puis chaque ticker est résolu. Un actif inconnu donne `NOT_IN_UNIVERSE`. Moins de deux actifs distincts donne `NOT_ENOUGH_ASSETS`.
2. `data_quality_check` sur chaque actif. Un actif `insuffisant` est **retiré et dit** (avis `ASSET_EXCLUDED`, entrée dans `value.excluded_assets`). S'il reste moins de deux actifs : refus `INSUFFICIENT_DATA`.
3. Conversion de chaque série en devise de base **avant** les rendements.
4. Deux matrices sont calculées :
   - `daily_common` : on garde les dates où tous les actifs cotent (intersection), puis on calcule les rendements entre dates communes. **Aucun report.** Le rendement d'une crypto du vendredi au lundi est comparé à celui d'une action du vendredi au lundi. Un report fabriquerait des rendements nuls les jours fermés et sous-estimerait la corrélation ;
   - `weekly` : dernier prix de chaque semaine close le vendredi (`corr_weekly_anchor`, `W-FRI`), semaines communes.
5. La matrice de référence (`value.reference`) est `weekly` si les calendriers sont mélangés (crypto et actifs cotés ensemble) en mode `auto`, `daily_common` sinon. En mode `auto` sans mélange, seule `daily_common` est fournie. Avec `frequency` forcé, les deux sont fournies.
6. Une fenêtre segmentée retire les rendements datés dans la période exclue.

**`value`.** `tickers` (actifs retenus), `excluded_assets`, `base_currency`, `mixed_calendars`, `reference`, `matrices`, `n_obs`, `max_pair`, `min_pair`, `mean_pairwise` (les trois derniers pour la matrice de référence). Chaque matrice contient `method` (texte), `periods_per_year`, `n_obs`, `first_date`, `last_date`, `computed`, `matrix` (dictionnaire imbriqué), `pairs` (triées de la plus forte à la plus faible), `mean_pairwise`, `max_pair`, `min_pair`. Une corrélation indéfinie (série constante) vaut `None`.

**Seuils.** Matrice non calculée si elle a moins de `corr_min_obs_refuse` (10) observations ; si c'est la référence, refus `INSUFFICIENT_DATA`. Avis `FEW_COMMON_OBSERVATIONS` (`warning`) sous `corr_min_obs_daily` (60) pour la quotidienne et sous `corr_min_obs_weekly` (26) pour l'hebdomadaire. Avis `UNDEFINED_CORRELATION` (`warning`) si une corrélation est indéfinie.

**Autres avis.** `MIXED_CALENDARS` (`info`), `FX_FORWARD_FILLED` (`info` dans cet outil), `ASSET_EXCLUDED` (`warning`).

**Limites consignées.** `LIMIT_PAST`, `LIMIT_SURVIVOR`, et le fait que les corrélations changent selon les régimes de marché (2022 : actions et obligations ont baissé ensemble) ; `FX_LIMIT` après conversion.

### 6.8 `portfolio_metrics`

**Objectif.** Rejouer sur le passé une allocation **fictive** (poids donnés) et calculer les mêmes indicateurs que `compute_metrics` au niveau du portefeuille, plus concentration, répartition et risque. La preuve est étiquetée `label="scenario"` ; la `value` porte aussi `label` = « Scénario rétrospectif fictif (rejeu historique), aucune prévision ». Aucun frais, aucune taxe, aucune vente à découvert.

**Paramètres.**

| Paramètre | Défaut | Rôle |
|---|---|---|
| `positions` | obligatoire | liste de `{ticker, weight}` ; de 1 à 15 éléments selon le schéma (voir limites) |
| `window` | obligatoire | fenêtre |
| `rebalancing` | **obligatoire** | `rebalanced` (poids constants, rééquilibrés à chaque période) ou `buy_and_hold` (poids initiaux laissés dériver) |
| `mode` | `rejeu_historique` | seule valeur acceptée |
| `weights_basis` | `user` | `user` ou `assumed_equal` (pondération égale posée par le plan) ; consigné pour que l'explication ne présente pas une hypothèse comme un choix de l'utilisateur |
| `base_currency` | `CAD` | devise de base |
| `as_of` | dernière date | date d'analyse |

Omettre `rebalancing` est une erreur de validation (`ValueError`).

**Validations (refus).** `NOT_IN_UNIVERSE` (actifs listés), `DUPLICATE_POSITION`, `INVALID_WEIGHTS` (poids non strictement positif, ou somme qui s'écarte de 1 de plus de `portfolio_weight_tolerance`, 1e-6). Si le contrôle de qualité d'un actif est `insuffisant`, refus `INSUFFICIENT_DATA` : **aucun actif n'est retiré en silence**, car les poids ne seraient plus ceux demandés.

**Calendriers** (décidés dans le code) :

| Contenu | Pas | Périodes par an |
|---|---|---|
| actions et FNB seulement | jour, sur l'union des jours de bourse | 252 |
| crypto seule | jour (7 jours sur 7) | 365 |
| crypto et actifs cotés mélangés | semaine close le vendredi (`corr_weekly_anchor`) | 52 (`periods_per_year_mixed`) |

En mode quotidien, chaque série est reportée vers l'avant sur l'union des dates (`_bounded_ffill`), au plus `ffill_limit_days` (5) jours civils, et chaque report est compté (avis `PANEL_FORWARD_FILLED`, `warning`). Les dates où un actif n'a toujours pas de prix sont retirées (`PANEL_ROWS_DROPPED`, `warning`). Les prix sont convertis en devise de base **avant** les rendements.

**Seuil minimal.** Moins de `min_obs_insufficient` (60) rendements communs en mode quotidien, ou moins de `corr_min_obs_weekly` (26) en mode hebdomadaire, donne `INSUFFICIENT_DATA`. Même contrôle sur les rendements restants d'une fenêtre segmentée. Si le portefeuille ne peut être mesuré qu'à partir d'une date postérieure de plus de `edge_gap_days` (5) jours au début de fenêtre, l'avis `COMMON_START_LATE` (`warning`) donne la date effective. Les poids demandés ne sont jamais renormalisés.

**Calcul de la valeur du portefeuille** (base 100) :

- `rebalanced` : `100 × cumprod(1 + r · w)`, où `r` est le vecteur des rendements des actifs et `w` les poids ;
- `buy_and_hold` : `100 × ((prix ÷ prix initial) · w)`. `concentration.final_weights` donne les poids en fin de période.

Les métriques (`value.metrics`) sont celles de la section 7 appliquées à cette série de valeur (`metric_block`, ou `metric_block_segmented` si la fenêtre est segmentée).

**Risque.** Covariance annualisée (`cov × périodes par an`) des rendements conservés, avec les **poids initiaux** :

- volatilité du portefeuille `σp = √(wᵀ Σ w)` ;
- `risk_contribution` de l'actif i = `wi × (Σw)i ÷ σp`, et `risk_share` = `wi × (Σw)i ÷ σp²` ;
- `diversification_ratio` = `Σ wi σi ÷ σp` (volatilité moyenne pondérée des actifs divisée par celle du portefeuille).

Si `σp` est négligeable (sous 1e-6 fois la plus grande volatilité individuelle), `contributions` et `diversification_ratio` valent `None` et l'avis `ZERO_PORTFOLIO_VOLATILITY` est ajouté.

**Concentration** (`value.concentration`) : `max_weight`, `max_weight_ticker`, `hhi` = `Σ wi²`, `effective_positions` = `1 ÷ hhi`, `n_positions`, `final_weights`. **Répartitions** (`value.breakdown`) par `asset_class`, `sector` et `currency` (devise de cotation), triées par poids décroissant.

**Autres champs de `value`.** `mode`, `rebalancing`, `weights_basis`, `rebalancing_description`, `base_currency`, `calendar_basis` (`daily` ou `weekly`), `period_label` (`jour` ou `semaine`), `periods_per_year`, `annualization_basis`, `n_returns`, `segmented`, `first_date`, `last_date`, `effective_start`, `positions` (avec nom, classe, secteur, devise), `risk`, `series_id_portfolio`, `data_quality` (un `evidence_id` par actif, repris dans `depends_on`).

**Limites consignées.** `LIMIT_PAST`, `LIMIT_SURVIVOR`, rejeu rétrospectif sans projection, et le traitement d'un FNB comme une position unique (la transparence réelle exigerait des données de composition hors du jeu de départ) ; `FX_LIMIT` après conversion.

## 7. Les métriques (`metrics.py`)

Toutes les fonctions sont déterministes. Les rendements sont des **rendements simples** (`prices.pct_change()`, `simple_returns`).

**Bloc de métriques** (`MetricBlock`) : `total_return`, `annualized_return` (peut être `None`), `annualized_volatility`, `max_drawdown`, `worst_day`, `worst_month`, `calendar_year_returns`.

| Mesure | Définition exacte (`metric_block`) |
|---|---|
| Rendement total | `dernier prix ÷ premier prix − 1`, du premier au dernier prix de la fenêtre |
| Jours observés | `span_days` = jours civils entre la première et la dernière observation |
| Rendement annualisé | `(1 + total)^(365,25 ÷ span_days) − 1`. `None` si `span_days < annualization_min_days` (350). `-1,0` si `total <= -1`. Équivaut à `(1 + total)^(1 ÷ années) − 1` avec `années = span_days ÷ 365,25` |
| Volatilité annualisée | `écart-type(rendements, ddof=1) × √N`, `N` = périodes par an (252, 365 ou 52 selon l'outil). `NaN` s'il y a au plus un rendement |
| Pire jour | rendement minimal : `WorstDay(date, return_)` |
| Rendement d'une période civile | `dernier prix de la période ÷ dernier prix de la précédente − 1` (`_period_returns`). La première période part du premier prix de la fenêtre et porte `partial=True` |
| Pire mois | période mensuelle de rendement minimal : `WorstMonth(month "AAAA-MM", return_, partial)` |
| Rendements par année civile | liste de `CalendarYear(year, return_, partial, first_date, last_date)` |

**Drawdown maximal** (`max_drawdown`). `drawdown = prix ÷ maximum cumulé − 1`. `depth` est la valeur minimale (négative, `-0,5` signifie une perte de 50 % depuis le sommet). `peak_date` est la première date, avant le creux, où le prix atteint le sommet (tolérance 1e-12). `trough_date` est la date du creux. `recovery_date` est la première date après le creux où le prix retrouve le sommet, et `recovered` indique si elle existe. Si `depth >= 0`, on renvoie `depth=0.0`, dates `None` et `recovered=True`. Le drawdown est mesuré à l'intérieur de la fenêtre.

**Version segmentée** (`metric_block_segmented`, fenêtre « hors 2022 »). Elle lève `ValueError` s'il ne reste aucun rendement.

1. Les rendements quotidiens sont calculés sur la **série complète**, puis ceux dont la **date** tombe dans une période exclue (bornes incluses, `excluded_mask`) sont retirés. On filtre les rendements, pas les prix.
2. On reconstruit un indice base 1 en enchaînant les rendements conservés ; `total_return` et `max_drawdown` portent sur cet indice.
3. Années d'annualisation : `span_days` = jours civils de la première à la dernière observation, moins les jours exclus (chevauchement avec la fenêtre, bornes incluses). Même seuil de 350 jours.
4. Rendements mensuels et annuels : produit des `(1 + r)` conservés de la période, moins 1. Une période est `partial` si c'est la première, ou si au moins une exclusion la touche.

**Sharpe** (`sharpe_ratio`, facultatif). Le taux annuel est reporté vers l'avant seulement (jamais de valeur future, pas de `bfill`). Les jours antérieurs à la première valeur connue sont exclus et comptés dans `n_dropped_no_rate`. Excédent par période = rendement − taux annuel ÷ N. `ratio = moyenne(excédent) ÷ écart-type(excédent, ddof=1) × √N`, ou `None` si l'écart-type est nul ou s'il y a au plus un point. Le résultat porte aussi `n_returns`, `mean_risk_free_annual` et `risk_free_series` (`V39079` ou `DFF`).

## 8. Configuration (`QuantConfig`)

Valeurs par défaut de `alphaforge/quant/config.py`. On les change en passant un `QuantConfig` à `DataStore(..., config=...)` ou `DataStore.from_directory(config=...)`. Un seuil est enregistré dans `params` quand il conditionne un résultat (par exemple `annualization_min_days`), donc il entre dans l'`evidence_id`.

| Paramètre | Valeur | Effet |
|---|---|---|
| `default_base_currency` | `"CAD"` | devise de base quand `base_currency` est absent |
| `supported_currencies` | `("CAD", "USD")` | devises acceptées par `convert_series` |
| `periods_per_year_exchange` | 252 | base d'annualisation des actifs cotés |
| `periods_per_year_crypto` | 365 | base d'annualisation des cryptos |
| `periods_per_year_mixed` | 52 | base hebdomadaire d'un mélange crypto et actifs cotés |
| `ffill_limit_days` | 5 | report maximal du taux de change (et des prix d'un portefeuille quotidien), en jours civils. Jamais de `bfill` |
| `min_obs_insufficient` | 60 | sous ce nombre d'observations : `insuffisant`, et refus de `compute_metrics` ; minimum de rendements d'un portefeuille quotidien |
| `min_obs_warning` | 120 | sous ce nombre : avertissement `FEW_OBSERVATIONS` |
| `coverage_insufficient` | 0,90 | couverture sous ce seuil : `insuffisant` |
| `coverage_warning` | 0,98 | couverture sous ce seuil : avertissement `MISSING_DAYS` |
| `edge_gap_days` | 5 | écart toléré aux bords de la fenêtre ; aussi seuil de `COMMON_START_LATE` |
| `internal_gap_days_exchange` | 5 | écart interne maximal d'un actif coté |
| `internal_gap_days_crypto` | 1 | écart interne maximal d'une crypto |
| `large_move_exchange` | 0,25 | saut quotidien étiqueté, actif coté |
| `large_move_crypto` | 0,40 | saut quotidien étiqueté, crypto |
| `large_move_sigma` | 3,0 | saut étiqueté aussi au-delà de ce nombre d'écarts-types |
| `adj_close_over_close_tolerance` | 0,01 | `adj_close` au-dessus de `close` de plus de 1 % : incohérent |
| `annualization_min_days` | 350 | rendement annualisé seulement au-delà de ce nombre de jours civils observés |
| `window_aliases` | 6 alias (section 5) | fenêtres nommées |
| `stress_default_windows` | `["complet", "2022", "hors_2022", "1_an", "3_ans"]` | fenêtres rejouées par défaut |
| `stress_threshold` | 0,25 | seuil d'écart relatif de `stress_window` |
| `stress_min_abs_spread` | 0,02 | plancher d'écart absolu de `stress_window` (2 points) |
| `corr_min_obs_daily` | 60 | avertissement de corrélation quotidienne sous ce nombre d'observations communes |
| `corr_min_obs_weekly` | 26 | idem hebdomadaire ; minimum d'un portefeuille hebdomadaire |
| `corr_min_obs_refuse` | 10 | sous ce nombre, matrice non calculée |
| `corr_weekly_anchor` | `"W-FRI"` | semaine close le vendredi, aussi pour la crypto |
| `portfolio_weight_tolerance` | 1e-6 | écart toléré de la somme des poids à 1 |

Valeurs écrites **en dur** dans le code, donc non configurables : 3 jours de séquence manquante (`data_quality.py`), 20 sauts conservés dans `large_moves`, bornes de ratio de fractionnement (1 et 1,5) et facteur 0,8 de `SPLIT_NOT_ADJUSTED`, bornes de 2 à 15 actifs d'une matrice et de 1 à 15 positions d'un portefeuille (schémas pydantic), tolérance 1e-12 du drawdown, seuil relatif 1e-6 de volatilité nulle d'un portefeuille, et le « 31 actifs » du message de `prelude`.

## 9. Garde-fous et choix de conception

- **Reproductibilité.** Aucun hasard, aucune horloge, aucun réseau. Le même appel sur les mêmes données redonne le même `evidence_id` et les mêmes chiffres. Les `series_id` sont déterministes aussi.
- **Pas de donnée postérieure à `as_of`.** Toutes les lectures de `DataStore` coupent à `as_of`. Une fin de fenêtre postérieure est ramenée à `as_of` avec un avis. Le taux sans risque est lu jusqu'à `as_of`. Les `inputs` hachent les lignes lues jusqu'à `as_of`.
- **Tout ce qui conditionne le résultat est dans `params`.** Devise de base, champ de prix, base d'annualisation, seuil d'annualisation, fenêtre résolue et `as_of`.
- **Signaler sans corriger.** `data_quality_check` étiquette les sauts, fractionnements douteux, volumes nuls et trous, sans les modifier. Un krach réel n'est pas une erreur.
- **Pas de remplissage caché.** Un actif seul est évalué sur son propre calendrier. Le seul report est celui du taux de change et, dans un portefeuille quotidien, des prix, borné à 5 jours civils et compté. Les corrélations quotidiennes n'utilisent aucun report.
- **Pas de reconversion.** Une série déjà convertie est refusée (`DOUBLE_CONVERSION`) ; une série déjà dans la devise cible aussi, car un FNB en CAD qui détient des actions américaines porte déjà son exposition au change.
- **Aucun actif retiré en silence d'un portefeuille.** `portfolio_metrics` refuse plutôt que de changer les poids. `correlation_matrix`, elle, retire un actif insuffisant mais le consigne (`ASSET_EXCLUDED`, `excluded_assets`).
- **Refus typés plutôt qu'exceptions.** Une entrée impossible à satisfaire donne une preuve `refused` avec un `error_code`, une explication et, si utile, des suggestions.
- **Séries par référence.** Les valeurs de prix ne transitent pas par le modèle de langage ni par la preuve : seulement des `series_id`.
- **Les limites voyagent avec le chiffre.** Chaque outil de calcul fournit `assumptions` et `limits`, que l'explicabilité peut citer.

## 10. Exemples d'utilisation vérifiés

Chaque bloc a été exécuté depuis `/workshop/datathon2026` avec `ALPHAFORGE_LLM=0 python3 -W ignore -c "..."`. La sortie montrée est la sortie réelle.

### 10.1 Charger les données, calculer des métriques, vérifier la reproductibilité

```python
from alphaforge.quant import DataStore, run_tool

store = DataStore.from_directory()
print(store.first_date, store.last_date, len(store.assets), store.fingerprint[:16])

e = run_tool(store, "compute_metrics", {"ticker": "NVDA", "window": {"alias": "2022"}, "base_currency": "CAD"})
b = e.value["base"]
print(e.evidence_id, e.status, e.n_obs, [n.code for n in e.notices])
print(round(b["total_return"], 4), round(b["annualized_volatility"], 4), b["max_drawdown"]["peak_date"], b["max_drawdown"]["trough_date"], b["max_drawdown"]["recovered"])
print(e.value["annualization_basis"], "| dépend de :", e.depends_on)

again = run_tool(store, "compute_metrics", {"ticker": "NVDA", "window": {"alias": "2022"}, "base_currency": "CAD"})
cut = run_tool(store, "compute_metrics", {"ticker": "NVDA", "window": {"alias": "2022"}, "base_currency": "CAD", "as_of": "2022-06-30"})
print(again.evidence_id == e.evidence_id, cut.evidence_id == e.evidence_id, cut.window.end, [n.code for n in cut.notices])
```

Sortie :

```
2021-11-12 2026-11-12 31 8d7b3d5f1e666576
ev_99262d44b5b14abd warning 251 ['LARGE_MOVES', 'FX_FORWARD_FILLED', 'DRAWDOWN_NOT_RECOVERED']
-0.4812 0.6131 2022-01-03 2022-10-14 False
252 jours de bourse par an | dépend de : ['ev_566ff73111bb8afa']
True False 2022-06-30 ['WINDOW_CLIPPED_TO_AS_OF', 'FX_FORWARD_FILLED', 'ANNUALIZATION_SKIPPED', 'DRAWDOWN_NOT_RECOVERED']
```

Le statut est `warning` à cause de `FX_FORWARD_FILLED` (gravité `warning`) ; `LARGE_MOVES` et `DRAWDOWN_NOT_RECOVERED` sont des `info`. Avec `as_of` au 2022-06-30, la fenêtre est ramenée à cette date, la période observée est inférieure à 350 jours et le rendement annualisé est omis.

### 10.2 Chaîner `get_prices` et `convert_currency`

```python
from alphaforge.quant import DataStore, run_tool

store = DataStore.from_directory()
px = run_tool(store, "get_prices", {"ticker": "AAPL", "window": {"alias": "1_an"}})
print(px.value["series_id"], px.value["currency"], px.value["n_obs"], px.value["first_date"], px.value["last_date"])
cad = run_tool(store, "convert_currency", {"series_id": px.value["series_id"], "target_currency": "CAD"})
print(cad.status, cad.value["from_currency"], "->", cad.value["to_currency"], cad.value["fx_forward_filled_days"], cad.value["fx_rates_used"]["pair"])
again = run_tool(store, "convert_currency", {"series_id": cad.value["series_id"], "target_currency": "USD"})
print(again.status, again.error_code, again.refusal_reason)
```

Sortie :

```
sr_7931605ce127 USD 252 2025-11-12 2026-11-12
warning USD -> CAD 7 USD/CAD (CAD par USD)
refused DOUBLE_CONVERSION double_conversion
```

### 10.3 Tester la sensibilité à la période

```python
from alphaforge.quant import DataStore, run_tool

store = DataStore.from_directory()
s = run_tool(store, "stress_window", {"ticker": "AAPL"})
print(s.value["verdict"], "|", s.value["summary"])
for r in s.value["rows"]:
    print(r["window"], round(r["value"], 4), r["start"], r["end"], r["excluded"])

z = run_tool(store, "stress_window", {"ticker": "ZST.TO"})
print(z.value["verdict"], z.value["floor_applied"], round(z.value["relative_spread"], 2), round(z.value["absolute_spread"], 4))
z0 = run_tool(store, "stress_window", {"ticker": "ZST.TO", "min_abs_spread": 0})
print(z0.value["verdict"], z0.value["floor_applied"])
```

Sortie :

```
sensible | volatilité annualisée : de 25.3% (« 1_an ») à 34.4% (« 2022 »), écart relatif 36% pour un seuil de 25% : sensible.
complet 0.2785 2021-11-12 2026-11-12 []
2022 0.3442 2022-01-01 2022-12-31 []
hors_2022 0.2591 2021-11-12 2026-11-12 [{'start': '2022-01-01', 'end': '2022-12-31'}]
1_an 0.2525 2025-11-12 2026-11-12 []
3_ans 0.2722 2023-11-13 2026-11-12 []
stable True 1.1 0.004
sensible False
```

### 10.4 Corrélations (calendriers mélangés) et portefeuille

```python
from alphaforge.quant import DataStore, run_tool

store = DataStore.from_directory()
e = run_tool(store, "correlation_matrix", {"tickers": ["AAPL", "BTC-USD"], "window": {"alias": "complet"}})
v = e.value
print(e.status, v["mixed_calendars"], v["reference"], {k: m["n_obs"] for k, m in v["matrices"].items()}, [n.code for n in e.notices])
print(round(v["max_pair"]["corr"], 3), v["matrices"]["weekly"]["method"])

e = run_tool(store, "portfolio_metrics", {"positions": [{"ticker": "RY.TO", "weight": 0.4}, {"ticker": "AAPL", "weight": 0.3}, {"ticker": "XBB.TO", "weight": 0.3}], "window": {"alias": "3_ans"}, "rebalancing": "rebalanced"})
v = e.value
print(e.status, e.label, e.n_obs, v["calendar_basis"], v["periods_per_year"], round(v["metrics"]["annualized_volatility"], 4))
print(v["concentration"]["hhi"], round(v["concentration"]["effective_positions"], 2), round(v["risk"]["diversification_ratio"], 3), [n.code for n in e.notices])
bad = run_tool(store, "portfolio_metrics", {"positions": [{"ticker": "RY.TO", "weight": 0.6}, {"ticker": "AAPL", "weight": 0.6}], "window": {"alias": "3_ans"}, "rebalancing": "rebalanced"})
print(bad.status, bad.error_code, bad.notices[-1].message)
```

Sortie :

```
ok True weekly {'daily_common': 1254, 'weekly': 261} ['FX_FORWARD_FILLED', 'MIXED_CALENDARS']
0.085 hebdomadaire, dernier prix de chaque semaine close le vendredi (W-FRI), semaines communes
warning scenario 768 daily 252 0.1181
0.33999999999999997 2.94 1.329 ['FX_FORWARD_FILLED', 'PANEL_FORWARD_FILLED']
refused INVALID_WEIGHTS Les poids doivent sommer à 1 : somme = 1.200000.
```

### 10.5 Refus, erreurs d'appel et séries persistantes

```python
import tempfile
from alphaforge.quant import DataStore, run_tool

store = DataStore.from_directory()
q = run_tool(store, "asset_lookup", {"query": "Netflix"})
print(q.status, q.error_code, q.refusal_reason, q.value)
short = run_tool(store, "compute_metrics", {"ticker": "RY.TO", "window": {"start": "2026-10-01", "end": "2026-11-12"}})
print(short.status, short.error_code, short.value, short.n_obs, [n.code for n in short.notices])
for name, args in [("compute_metrics", {"ticker": "RY.TO"}), ("inconnu", {})]:
    try:
        run_tool(store, name, args)
    except (ValueError, KeyError) as x:
        print(type(x).__name__, str(x).splitlines()[0][:70])

d = tempfile.mkdtemp()
p = run_tool(DataStore.from_directory(series_dir=d), "get_prices", {"ticker": "AAPL", "window": {"alias": "1_an"}})
fresh = DataStore.from_directory(series_dir=d)
print(p.value["series_id"], p.value["series_id"] in fresh.series, len(fresh.series.get(p.value["series_id"]).series.prices))
```

Sortie :

```
refused NOT_IN_UNIVERSE asset_not_found {'in_universe': False, 'suggestions': []}
refused INSUFFICIENT_DATA None 30 ['INSUFFICIENT_OBSERVATIONS', 'DATA_INSUFFICIENT', 'INSUFFICIENT_DATA']
ValueError Arguments invalides pour compute_metrics: 1 validation error for Compu
KeyError "Outil inconnu: inconnu. Disponibles: ['asset_lookup', 'compute_metric
sr_7931605ce127 True 252
```

La dernière ligne montre qu'un second `DataStore`, qui part d'une mémoire vide, retrouve la série écrite par le premier (le `series_id` ne dépend que de la recette et des données). Le refus `INSUFFICIENT_DATA` ne porte pas de `value` mais garde l'avis de qualité et un `depends_on` vers la preuve de qualité (`len(short.depends_on)` vaut 1).

## 11. Tests associés

Les tests sont dans `tests/quant/` (`python3 -m pytest tests/quant -q`). La plupart utilisent des séries synthétiques à résultat connu, fabriquées par `conftest.py` (fixture `make_store`, fonctions `series` et `bdays`).

| Fichier | Tests | Ce qui est couvert |
|---|---|---|
| `conftest.py` | n/a | Fabrique de `DataStore` synthétiques : `make_store({ticker: (classe, devise, série)}, fx=...)`, taux de 1,25 par défaut. |
| `test_contract.py` | 15 | Contrat de l'analyste : recherche libre et erreurs typées (`NOT_IN_UNIVERSE`, `AMBIGUOUS_QUERY`, ancien champ `refusal_reason` conservé), `get_prices` (série par référence, troncature, `series_id` déterministe), chaîne `convert_currency` (parent, refus de double conversion, devise cible, `CURRENCY_MISMATCH`, `UNKNOWN_SERIES`), champs fixes de la preuve et `params` explicites, empreinte des `inputs` insensible aux données après `as_of`, absence d'annualisation sous un an, minimum de 60 observations, pire jour, pire mois, années civiles, constats de `data_quality_check` (début tardif, plus longue séquence manquante, `adj_close` vs `close`, devise), sauts étiquetés par sigma et jamais retirés, libellés français de qualité, aller-retour Parquet, Sharpe facultatif sans taux futur. |
| `test_metrics_synthetic.py` | 14 | Calculs à valeur connue : série constante, doublement en un an, drawdown récupéré ou non, base 365 (crypto) contre 252, effet de change pur, identité USD vers CAD vers USD, refus de double conversion, report de taux borné et jamais vers l'arrière, résultats insensibles aux données postérieures à `as_of`, dépendance de l'`evidence_id` aux paramètres, aux données et à la version, blocage par données insuffisantes, actif et alias inconnus, schémas JSON et validation des arguments. |
| `test_stress_window.py` | 12 | Verdicts de `stress_window` et fenêtres segmentées : choc en 2022 sensible, absence de choc stable, seuil et mesure dans `params`, filtrage des rendements et non des prix (aucun faux rendement à travers le trou), annualisation sur jours civils hors période exclue, toutes les fenêtres consignées y compris refusées, lignes liées à des preuves `compute_metrics` reproductibles, actif inconnu et seuil invalide, exclusion ramenée à la fenêtre et à `as_of`, fenêtre entièrement exclue, plancher absolu qui garde stable une mesure presque nulle et qui ne cache pas un vrai choc. |
| `test_correlation_matrix.py` | 10 | Corrélations de valeur connue (1 et -1), rendements et non prix, week-ends crypto comparés sur dates communes sans report, référence hebdomadaire en cas de mélange et fréquence forçable, jours fériés non partagés, conversion avant les rendements, période exclue et `as_of`, avertissement sur peu d'observations communes, actif insuffisant exclu et actif inconnu refusé, contrat de preuve. |
| `test_portfolio_metrics.py` | 14 | Valeurs connues (deux actifs identiques, anticorrélation parfaite), rééquilibré contre achat-et-conservation, concentration et répartitions, validation des poids, rééquilibrage obligatoire et consigné, base de calendrier selon le contenu, reports bornés et comptés, début tardif signalé sans renormalisation, actif insuffisant qui refuse, conversion avant les rendements, fenêtre exclue et `as_of`, étiquette `scenario` et série stockée, origine des poids dans l'`evidence_id`. |
| `test_smoke_real_data.py` | 5 cas (2 fonctions, dont une paramétrée sur 4 actifs) | Marqueur `smoke`, sur le jeu de départ : `RY.TO`, `AAPL`, `BTC-USD`, `XBB.TO` donnent des métriques plausibles (plages de volatilité, drawdown entre -1 et 0, base 365 pour la crypto, 252 sinon, dépendance au contrôle de qualité), et anomalies connues signalées (`SPLIT_RATIO_UNUSUAL` pour `CNQ.TO`, `ZERO_VOLUME` pour `ZST.TO`). Ignoré si le jeu de départ est absent. |

## 12. Limites connues

- **Univers fermé.** 31 actifs (vérifié : `len(store.assets)`). Un actif hors univers est refusé, jamais complété de mémoire.
- **Données historiques.** Rien ne projette l'avenir. Le biais du survivant est signalé dans les limites (`LIMIT_SURVIVOR`) : l'univers a été choisi en 2026.
- **Taux de change.** Un taux par jour (Banque du Canada), publié vers 16 h 30 ET. L'écart avec l'heure de clôture des marchés et avec les jours UTC de la crypto n'est pas corrigé (`FX_LIMIT`). Seule la paire USD/CAD existe.
- **Rendements sur `adj_close`.** Les dividendes sont supposés réinvestis. Aucun frais, aucune taxe, aucun glissement de prix dans `portfolio_metrics`.
- **Annualisation.** Pas de rendement annualisé sous 350 jours civils observés. Les volatilités d'une crypto (365) et d'un actif coté (252) ne sont pas directement comparables.
- **Contrôle de qualité.** Il repère des anomalies visibles, il ne certifie pas les cours. La détection de fractionnement ne regarde que le ratio et la chute de `close` le jour de l'opération. Les seuils de la section 8 « en dur » ne sont pas réglables.
- **Couverture.** Le calendrier de référence est l'union des dates du groupe de marché (`CA`, `US` ou `crypto`). La couverture d'un actif isolé dans son groupe est comparée à ses propres dates (déduit du code).
- **`stress_window`.** Rejoue seulement `compute_metrics`, et ne compare que la volatilité annualisée ou la perte maximale. Une mesure stable sur les fenêtres choisies peut changer sur une autre période.
- **Fenêtres segmentées.** Les exclusions viennent seulement des alias de configuration. Un alias personnalisé est possible en modifiant `store.config.window_aliases` (c'est ce que font les tests).
- **`portfolio_metrics`.** La description de l'outil annonce « 2 à 15 positions », mais le schéma (`Field(min_length=1, max_length=15)`) accepte une seule position : un appel à 1 position (`RY.TO`, poids 1,0) a été exécuté et renvoie `ok`. Un FNB est traité comme une seule position (pas de composition). Les répartitions utilisent la devise de **cotation**, pas l'exposition réelle. Le risque utilise les poids initiaux, y compris en `buy_and_hold`.
- **Corrélations.** Elles changent selon les régimes de marché. Une corrélation passée faible ne garantit pas une diversification future.
- **Séries en mémoire par défaut.** Sans `series_dir`, un `series_id` n'existe que pendant la session. Le mode Parquet exige `pyarrow` (non vérifié ici : la version installée n'a pas été relevée).
- **Empreinte globale.** L'`evidence_id` dépend de tout le jeu de données : modifier des lignes postérieures à `as_of` change l'identifiant sans changer les chiffres.
- **Non vérifié.** Le comportement sur un jeu de données dont `macro_rates.csv` ou `sources_metadata.csv` est absent n'a été exécuté que par les tests synthétiques (sans macro) ; le chemin `convert_currency` avec `series_id` sans `as_of` a été lu dans le code mais pas exécuté.
