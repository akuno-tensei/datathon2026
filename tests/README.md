# Suite de tests d'AlphaForge AI

Ce document décrit comment lancer les tests, ce qu'ils vérifient et comment en ajouter. Le résumé et la liste des familles de tests figurent déjà dans la section « Tests » du [README du projet](../README.md#tests) ; ce document n'en répète pas le contenu.

Les tests sont écrits pour Python 3.9 et pytest 8.4.2 (version épinglée dans la CI). Tout se lance depuis la racine du dépôt, `/workshop/datathon2026`.

## 1. Lancer les tests

### Commandes

```bash
python3 -W ignore -m pytest -q                                   # suite complète (dossier tests/, voir pytest.ini)
python3 -W ignore -m pytest -q tests/quant                       # un dossier
python3 -W ignore -m pytest -q tests/quant/test_stress_window.py # un fichier
python3 -W ignore -m pytest -q "tests/quant/test_metrics_synthetic.py::test_drawdown_with_recovery"  # un test
python3 -W ignore -m pytest -q tests/governance -k alternative   # les tests dont le nom contient « alternative »
python3 -W ignore -m pytest -q -m smoke                          # tests de fumée sur les données réelles
python3 -W ignore -m pytest -q -m "not smoke"                    # tout sauf les tests de fumée
```

L'option `-W ignore` masque les avertissements des bibliothèques. Elle n'est pas obligatoire.

Ces commandes ont été exécutées le 2026-11-15 (Python 3.9.25, pytest 8.4.2). La suite complète dure environ 2 minutes (la durée est dominée par `tests/app`). Les autres commandes ci-dessus ont aussi réussi.

### Configuration

`pytest.ini` fixe trois choses :

- `pythonpath = .` : le paquet `alphaforge` s'importe sans installation.
- `testpaths = tests` : sans argument, pytest ne cherche que dans `tests/`.
- un seul marqueur, `smoke` : tests de fumée sur les données réelles du jeu de départ. Ils sont dans `tests/quant/test_smoke_real_data.py`. Le fichier se saute lui-même si `alphaforge_starter/prices_daily.csv` est absent.

### Compter les tests

Les nombres de tests changent à chaque ajout. Aucun test automatique ne vérifie les comptes de ce document : recomptez-les avec cette commande.

```bash
python3 -W ignore -m pytest -q --collect-only tests/quant | tail -1    # remplacer quant par un autre dossier, ou retirer l'argument pour tout compter
```

Pour un tableau par dossier :

```bash
for d in tests/*/; do printf "%-24s " "$d"; python3 -W ignore -m pytest -q --collect-only "$d" | tail -1; done
```

### Limite : certains dossiers ne se lancent pas ensemble

Il n'y a pas de `__init__.py` dans `tests/`. Chaque dossier a donc un fichier `conftest.py`, et pour pytest tous ces fichiers portent le même nom de module, `conftest`. Deux fichiers de test font `from conftest import ...` pour récupérer des aides du `conftest.py` de leur propre dossier :

- `tests/quant/test_contract.py` et `tests/quant/test_metrics_synthetic.py` importent `series` et `bdays` ;
- `tests/integration/test_enrichment_integration.py` importe `AS_OF`, `Fake`, `esg`, `news`, `failing`, `codes`, `reasons` et `verdict`.

Quand on passe plusieurs dossiers en argument, pytest charge leurs `conftest.py` au démarrage, l'un après l'autre. Le dernier chargé remplace les autres sous le nom `conftest` (voir `PytestPluginManager._importconftest` dans pytest). Le `from conftest import ...` d'un fichier de `quant` ou d'`integration` lit alors le `conftest.py` d'un autre dossier et échoue avec `ImportError: cannot import name ...`. Sans argument, la suite complète ne rencontre pas ce problème : pytest charge chaque `conftest.py` au moment de parcourir son dossier.

Les autres dossiers n'ont pas ce problème, car leurs fichiers importent des modules d'aide au nom unique (`helpers_gov`, `helpers_expl`, `helpers_enr`).

Résultats observés le 2026-11-15 :

| Commande | Résultat |
|---|---|
| `pytest -q tests/enrichment tests/integration` | 75 passed |
| `pytest -q tests/integration tests/enrichment` | erreur de collecte : `cannot import name 'AS_OF' from 'conftest' (.../tests/enrichment/conftest.py)` |
| `pytest -q tests/quant tests/integration` | erreurs de collecte (deux dossiers qui importent `conftest`) |
| `pytest -q tests/integration tests/quant` | erreur de collecte |
| `pytest -q tests/quant tests/governance` | erreurs de collecte dans `quant` |
| `pytest -q tests/governance tests/quant` | 182 passed |
| `pytest -q tests/quant/test_contract.py tests/governance/test_gov_run.py` | erreur de collecte |

Le résultat dépend donc de l'ordre des arguments : le dernier dossier cité garde son `conftest`. La combinaison `tests/enrichment tests/integration`, dans cet ordre, passe. Écrite dans l'ordre inverse, elle échoue. Règles à retenir :

1. Lancez `quant` et `integration` seuls, ou placez-les en dernier argument, et jamais ensemble.
2. En cas de doute, lancez un dossier à la fois, ou toute la suite sans argument.

Ce comportement est signalé ici et non corrigé. Une correction possible serait de donner des noms uniques aux aides de `quant` et d'`integration`, comme c'est déjà le cas ailleurs.

## 2. Environnement de test

Le `conftest.py` de la racine s'exécute avant tous les tests. Il fixe trois variables d'environnement avec `os.environ.setdefault` : une valeur déjà définie dans votre shell est donc conservée.

| Variable | Valeur | Effet |
|---|---|---|
| `ALPHAFORGE_HOT_RELOAD` | `0` | Désactive le rechargement à chaud de l'interface (`alphaforge/app/_reload.py`). Une purge de modules en cours de suite rendrait incohérentes les références que les tests gardent. Le test `test_hot_reload.py` le réactive dans un processus séparé, et `test_the_test_suite_runs_with_hot_reload_disabled` vérifie la valeur `0`. |
| `ALPHAFORGE_LLM` | `0` | Pas d'appel à Bedrock : l'interprétation de la demande se fait par règles, de façon déterministe. |
| `ALPHAFORGE_S3_BUCKET` | chaîne vide | Pas d'archivage S3. |

La CI définit les mêmes trois variables dans le workflow.

### Aucun accès à AWS, à la SEC ni au réseau

Aucun test ne fournit d'identifiants AWS. Les appels externes sont remplacés par des doublures écrites dans les fichiers de test :

| Service | Doublure | Où |
|---|---|---|
| Bedrock (analyse de la demande) | `FakeClient` (méthode `complete(system, user)`) | `tests/orchestrator/test_orch_request.py` |
| Bedrock (pipeline) | `FakeLLM` | `tests/app/test_app_aws.py` |
| S3 (archive des analyses) | `FakeS3` (`put_object`, vérifie le chiffrement `AES256`, peut simuler une panne) | `tests/app/test_app_aws.py` |
| SEC EDGAR | fonction `http(url, headers)` passée à `EdgarProvider`, avec des réponses JSON au format réel de l'API `companyfacts` | `tests/enrichment/test_enr_fundamentals.py`, `tests/enrichment/test_enr_tools.py`, `tests/integration/test_enrichment_integration.py` |
| Fournisseurs ESG et actualités | `FakeProvider` (`helpers_enr.py`) et `Fake` (`tests/integration/conftest.py`), qui comptent leurs appels | `tests/enrichment`, `tests/integration` |

Dans `tests/app`, l'interface complète s'exécute sans réseau pour deux raisons. Les fondamentaux SEC viennent du dossier `alphaforge_starter/enrichment_snapshot` (test `test_offline_snapshot_serves_sec_data_without_network_or_identification`) ou d'un cache créé dans un dossier temporaire. Et `test_without_aws_credentials_the_rules_are_used_directly` vérifie que l'absence d'identifiants AWS donne l'interprétation par règles.

Le dépôt ne contient aucune protection active contre le réseau (pas de blocage de `socket`). L'absence d'appel réseau repose sur la configuration ci-dessus et sur les doublures. Un test qui construirait un vrai client AWS ou HTTP irait sur le réseau.

### Données synthétiques et données réelles

La plupart des tests construisent leur propre univers de prix, avec un résultat connu d'avance (voir section 3). Seuls trois endroits lisent le jeu de départ réel, `alphaforge_starter/` :

- `tests/quant/test_smoke_real_data.py` : tests de fumée sur RY.TO, AAPL, BTC-USD et XBB.TO (volatilité dans une plage plausible, pas de valeur exacte), et présence des alertes de qualité connues sur CNQ.TO (`SPLIT_RATIO_UNUSUAL`) et ZST.TO (`ZERO_VOLUME`).
- `tests/orchestrator/test_orch_bench.py` : rejoue l'interprétation par règles sur l'univers complet.
- `tests/app/` : `test_app_aws.py` crée `AlphaForge.from_directory(...)`, et `test_app_streamlit.py` lance `alphaforge/app/streamlit_app.py`, qui charge ces mêmes données.

## 3. Contenu par dossier

Les dossiers `quant`, `orchestrator`, `governance`, `explainability`, `enrichment` et `integration` correspondent aux cinq agents du projet, plus leur assemblage. `app` couvre l'interface et le pipeline.

### `tests/quant`

L'analyste quantitatif : outils de calcul, sur des séries à résultat connu.

Fixtures et aides (`conftest.py`) : `make_store({ticker: (classe, devise, Series)}, fx=None)` construit un `DataStore` synthétique (taux USD/CAD constant à 1,25 par défaut) ; `series(valeurs, start)` et `bdays(n, start)` créent une série de jours ouvrés à partir du 2022-01-03.

| Fichier | Ce qu'il vérifie |
|---|---|
| `test_metrics_synthetic.py` | Série constante (volatilité et rendement nuls), doublement en un an (+100 %), drawdown avec et sans récupération, base 365 jours pour la crypto et 252 pour les bourses, conversion USD→CAD→USD qui redonne l'identité, refus de la double conversion, report des taux de change borné, résultats inchangés quand on ajoute des données après `as_of`, `evidence_id` qui dépend des paramètres, des données et de la version de l'outil. |
| `test_contract.py` | Contrat de l'analyste : recherche d'actif et erreurs typées, séries renvoyées par identifiant, enveloppe de preuve à champs fixes, pas d'annualisation sous un an, minimum de 60 observations, pire jour et pire mois, contrôle de qualité des données (début tardif, plus long trou, incohérences), mouvements extrêmes signalés mais jamais retirés, libellés de qualité en français, stockage Parquet des séries, ratio de Sharpe optionnel. |
| `test_correlation_matrix.py` | Corrélation 1 pour deux actifs identiques et −1 pour deux actifs opposés, calcul sur les rendements, dates communes sans report de prix (week-ends de la crypto, jours fériés), conversion en devise de base avant les rendements, `as_of` et période exclue respectés. |
| `test_portfolio_metrics.py` | Deux actifs identiques à 50/50 (volatilité de l'actif), deux actifs anticorrélés qui s'annulent, rééquilibré contre achat-conservation, validation des poids, hypothèse de rééquilibrage obligatoire et enregistrée, actif à historique insuffisant refusé et non retiré, preuve étiquetée « scénario ». |
| `test_stress_window.py` | Un choc en 2022 donne « sensible », l'absence de choc donne « stable », seuil et mesure enregistrés dans les paramètres, rendements calculés hors période exclue (« hors 2022 ») sans faux rendement à cheval sur le trou, plancher absolu qui garde stable une mesure proche de zéro sans masquer un vrai choc. |
| `test_smoke_real_data.py` | Voir section 2. Marqué `smoke`. |

### `tests/orchestrator`

Le chef d'orchestre : analyse de la demande, profils, plan, registre.

Fixtures (`conftest.py`) : `assets`, `store` (six actifs, prix aléatoires à graine fixe, 2021-11-12 à 2023-12-29), `analyst`, `profile_dict` (fabrique de profils valides).

| Fichier | Ce qu'il vérifie |
|---|---|
| `test_orch_request.py` | `RuleBasedParser` : symboles, noms d'entreprise, intentions, signaux de périmètre (prédiction, transaction, conseil), poids lus dans le texte. `LLMParser` avec `FakeClient` : une réponse valide est utilisée, mais le modèle ne peut ni inventer un symbole, ni retirer un actif ou un signal de périmètre, ni inventer des poids. Une réponse invalide ou une erreur ramène aux règles. |
| `test_orch_run.py` | Exécution complète : arrêt sur profil inconnu ou contradictoire, demande sans actif, plan déterministe et dépendances, outil manquant enregistré sans simulation, `as_of` et devise de base transmis aux outils, boucle de retour de la gouvernance bornée (`MAX_FEEDBACK_ITERATIONS`) et sans doublon de preuves, résultat partiel sans les autres agents, poids de l'utilisateur transmis tels quels et poids invalides refusés par l'analyste et non corrigés. |
| `test_orch_profile.py` | Chargement des profils (dict, instance, `amira`, `robert`, `mateo`), refus des identifiants dangereux (`../etc/passwd`), schéma invalide, contradictions internes d'un profil (risque élevé et horizon court, exclusion et préférence sur la même valeur, bornes de poids). |
| `test_orch_ledger.py` | Registre : numéros de séquence croissants depuis 1, écriture de preuve idempotente, filtre par type, export JSONL identique octet pour octet à deux constructions, type inconnu refusé. |
| `test_orch_bench.py` | Le score des règles (36 points maximum, aucun repli) est recalculé et comparé à `deploy/bench_results.json`. Les totaux du fichier de résultats apparaissent dans le `README.md` racine (voir section 4). |

### `tests/governance`

La gouvernance : limites, exclusions, périmètre, alternatives.

Aides (`helpers_gov.py`) : `last_verdict(result)`, `window(...)` et `facts(...)` qui fabriquent des faits écrits à la main. Fixtures (`conftest.py`) : univers synthétique de 10 actifs à volatilités connues (rendements alternés +r/−r, volatilité annualisée d'environ 15,9 × r, un actif « CRASH » qui perd 50 % sans jamais récupérer), `assets`, `store`, `analyst`, `gov`, `profile_dict`, `run(texte, as_of=None, governance=None, **profil)`.

| Fichier | Ce qu'il vérifie |
|---|---|
| `test_gov_checks.py` | Contrôles purs, sans données : limites selon le niveau de risque et resserrées pour un horizon court, exclusions dures (secteur, classe, ticker, thème) qui bloquent, thème qui avertit sans bloquer, préférences en information seulement, volatilité et drawdown au-dessus des limites (jamais bloquants), temps de récupération face à l'horizon, hors-univers, drapeaux de périmètre convertis en refus avec offre d'une description, priorité des statuts. |
| `test_gov_run.py` | Gouvernance branchée sur l'orchestrateur : verdict conforme ou avec avertissement, conformité au schéma, blocage par exclusion ou classe non permise, refus de prédiction, de transaction et de conseil personnel, alternatives demandées, évaluées sur les mêmes fenêtres et le même `as_of`, pas d'alternative exclue, `max_alternatives` respecté, chaque `evidence_id` cité existe dans le registre, même entrée donne même verdict, l'identité du profil ne change pas le jugement. |
| `test_gov_coherence.py` | Alternatives chiffrées dans la devise du profil, requête ambiguë jamais « conforme », paire très corrélée signalée en information, scénario équipondéré au-dessus du plafond par actif, plafond de classe appliqué au portefeuille. |
| `test_gov_exclusion_vocab.py` | Une exclusion dure dont la valeur ne désigne rien dans l'univers (secteur mal orthographié, classe en français) est signalée avec une suggestion, jamais ignorée, une seule fois par exclusion. |
| `test_gov_facts.py` | `build_facts` relit le registre sans rien calculer : métadonnées de l'actif, fenêtres de mesure, refus conservés, codes d'avis dédupliqués. |

### `tests/explainability`

L'explication : gabarits, vérification des chiffres et du ton.

Aides (`helpers_expl.py`) : `explain(result, **over)` relance l'explicabilité sur le registre d'un run avec un profil modifié (langue, expérience), `body_lines(text)`. Fixtures (`conftest.py`) : même univers synthétique que `governance`, et `run(texte, as_of=None, **profil)` avec gouvernance et `Explainer` branchés.

| Fichier | Ce qu'il vérifie |
|---|---|
| `test_expl_verifier.py` | Chaque chiffre doit correspondre au registre : un chiffre exact est gardé, une valeur falsifiée, un arrondi faux, un nombre non déclaré, une référence inconnue ou une date absente du registre sont retirés. Un fait sans preuve est rétrogradé. Un brouillon rejeté est renvoyé en réparation, avec un nombre de tentatives borné. |
| `test_expl_tone.py` | Aucune tournure de conseil, de prédiction ou de promesse, quel que soit l'étiquetage de l'énoncé. Une citation entre guillemets est conservée. Le refus de périmètre pour une demande de conseil subsiste. |
| `test_expl_provenance.py` | Contrôle structurel : tout mot vient d'un gabarit ou du registre. Une recommandation déguisée est retirée même sans mot interdit. Les phrases des gabarits passent toutes (4 demandes × 2 langues × 3 niveaux d'expérience). Le texte de la demande n'entre jamais dans le vocabulaire autorisé. |
| `test_expl_run.py` | De bout en bout : tous les cas passent la vérification sans retrait, chaque ligne est étiquetée, sortie déterministe, chiffres identiques à ceux du verdict, changement de langue et de niveau (un débutant a des définitions et moins d'hypothèses), limites toujours présentes (passé, simulation), scénario étiqueté fictif. |
| `test_expl_sensitivity.py` | La sensibilité à la période cite la preuve `stress_window`, corrélation avec son signe, scénario de portefeuille fictif avec poids égaux déclarés, poids de l'utilisateur jamais décrits comme une hypothèse. |
| `test_expl_limits.py` | Hypothèses, limites et « non couvert » tirés du registre seul. |

### `tests/enrichment`

L'agent d'enrichissement (ESG, actualités, fondamentaux SEC), testé seul.

Aides (`helpers_enr.py`) : `AS_OF` (2025-06-30), `raw`, `esg_item`, `news_item`, `FakeProvider`, `failing`. Fixtures (`conftest.py`) : `store` et `assets` (cinq actifs, prix aléatoires à graine fixe jusqu'au 2026-11-12), `mateo` (profil ESG), `neutral_profile`, `parse`, `registry` (registre avec un fournisseur ESG fictif de classe `third_party`).

| Fichier | Ce qu'il vérifie |
|---|---|
| `test_enr_tools.py` | Détection des besoins à partir du profil et du texte de la demande, sans logique par persona. Filtre `as_of`, refus d'un enregistrement publié après `as_of`. Faits sans trace refusés, extraction fidèle des phrases. Niveaux de confiance A/B/C (`test_confidence_levels`). Fiches de source. Erreurs de fournisseur converties en codes. Cache avec repli quand la source tombe. `EdgarProvider` qui filtre les formulaires et les dates. |
| `test_enr_agent.py` | Chaîne complète qui écrit des enregistrements datés et sourcés, publications futures jamais écrites, panne d'un fournisseur transformée en lacune sans exception, source non enregistrée ou sans méthodologie plafonnée à C, relance idempotente, un seul appel par outil et par ticker, validation à l'écriture dans le registre. |
| `test_enr_fundamentals.py` | Les chiffres viennent du dernier rapport annuel déposé avant `as_of`, un indicateur absent est omis et jamais estimé, un `as_of` plus tardif voit le rapport suivant, pas de rapport avant `as_of` donne une lacune explicite, aucun appel SEC pour une demande non prise en charge, liste des déposants téléchargée une fois. |

### `tests/integration`

L'agent d'enrichissement branché sur les quatre autres, règle par règle (`Agents.md` 2.2 et 2.4). Un seul fichier, `test_enrichment_integration.py`.

Aides et fixtures (`conftest.py`) : `AS_OF` (2026-11-12), univers de quatre actions (NVDA très volatile, ses pairs AMD et INTC non), `registry`, fabriques `raw`, `esg`, `news`, doublure `Fake`, `failing`, accesseurs `reasons`, `codes`, `verdict`, fixtures `profile` (profil prudent qui exclut les controverses) et `run(texte, profil, providers=None)`, qui renvoie `(résultat, agent)`.

| Groupe de tests | Ce qu'il vérifie |
|---|---|
| Règles de confiance | Une controverse documentée par une source de niveau B bloque une exclusion dure. Un signal de niveau C n'est jamais bloquant. Une source tierce non enregistrée ou sans méthodologie est plafonnée à C. |
| Temps et pannes | Une publication postérieure à `as_of` n'existe pas pour la simulation. Une source en panne devient une lacune explicite. Un plantage de l'enrichissement laisse le cœur fonctionner et le dit. |
| Déclenchement | L'enrichissement ne tourne que s'il y a un besoin. Sans l'agent, les chiffres du cœur à quatre agents sont identiques. Une seule relance pour les alternatives, idempotente. |
| Explication | Niveau, date et source de chaque énoncé enrichi, lacunes en « non couvert », phrase d'actualité citée traitée comme mention et non comme conseil, énoncés en anglais. |
| Registre | Ordre : besoins, enrichissement, analyste, verdict. |
| SEC | Les fondamentaux arrivent à l'explication vérifiée au niveau A, avec une perte nette dite par le libellé. |

### `tests/app`

Le pipeline `AlphaForge`, les vues et l'interface Streamlit.

Fixtures (`conftest.py`) : `forge` (instance d'`AlphaForge` sur un univers synthétique de 4 actifs, profils créés dans un dossier temporaire) et `prof` (fabrique de profils). `test_app_aws.py` et `test_app_streamlit.py` définissent leurs propres fixtures `forge` et `app`.

| Fichier | Ce qu'il vérifie |
|---|---|
| `test_app_views.py` | Pipeline à quatre agents et explication structurée, devise de base transmise à chaque appel, barres de verdict avec la limite et son origine, chaque preuve se reproduit avec le même identifiant, comparaison de profils sur les mêmes données, création, copie et suppression de profils (dossier utilisateur seulement, identifiants dangereux refusés), poids du formulaire transmis tels quels, trace en direct, export de rapport sans nouveau chiffre. |
| `test_app_streamlit.py` | Interface avec `AppTest` : bandeau de simulation permanent, envoi d'une demande, tiroir de preuve avec la commande de reproduction, bascule anglais, onglet de comparaison, constructeur de scénario (somme des poids), raccourcis, formulaire de profil (ajout, suppression, confirmation), bascule enrichissement/cœur seul, sources connectées affichées sans rien feindre, identification SEC gardée en session et jamais écrite sur disque, tableau des fondamentaux. |
| `test_app_aws.py` | Bedrock et S3 simulés : le modèle voit l'univers, une panne Bedrock ramène aux règles et l'analyse aboutit, archive chiffrée avec registre et résumé, panne S3 signalée sans casser l'analyse, snapshot SEC hors ligne sans identification. |
| `test_app_labels.py` | Libellés lisibles en deux langues, un mot inconnu n'est jamais modifié, les nombres ne changent jamais. |
| `test_hot_reload.py` | Le rechargement à chaud ne purge que si le code a changé. Un test lance un processus séparé avec `ALPHAFORGE_HOT_RELOAD=1` et reproduit l'erreur d'origine (classe périmée) avant de vérifier la correction. Ce test a duré environ 1,4 seconde lors du relevé. |

## 4. Les familles de tests qui comptent pour le jury

| Famille | Où la trouver |
|---|---|
| Séries synthétiques à résultat connu | `quant/test_metrics_synthetic.py`, `test_portfolio_metrics.py`, `test_correlation_matrix.py`, `test_stress_window.py`. Les univers de `governance`, `explainability` et `integration` fixent la volatilité par construction (rendements alternés +r/−r). |
| Non-régression temporelle (`as_of`) | `test_results_ignore_data_after_as_of` et `test_input_hash_ignores_data_after_as_of` (quant), `test_alternative_uses_same_windows_and_as_of_as_primary` (governance), `test_as_of_is_forwarded_to_tools` (orchestrator), `test_future_publications_never_reach_the_ledger` (enrichment), `test_a_publication_after_as_of_does_not_exist_for_the_simulation` (integration). |
| Reproductibilité des preuves | `test_evidence_id_depends_on_params_data_and_tool_version` (quant), `test_jsonl_roundtrip_and_determinism` (orchestrator), `test_same_input_same_verdict` (governance), `test_deterministic` (explainability), `test_every_evidence_reproduces_to_the_same_id` et `test_scenario_evidence_reproduces_to_the_same_id` (app). |
| Garde-fous : périmètre | Demandes de prédiction, de transaction et de conseil : `orchestrator/test_orch_request.py` (le modèle ne peut pas les désactiver), `governance/test_gov_run.py` et `test_gov_checks.py` (refus avec offre d'une description), `explainability/test_expl_run.py`. |
| Garde-fous : ton | `explainability/test_expl_tone.py` : aucune formule de conseil, de prédiction ou de promesse. |
| Garde-fous : provenance | `explainability/test_expl_verifier.py` (chaque chiffre vient du registre) et `test_expl_provenance.py` (chaque mot vient d'un gabarit ou du registre), `enrichment/test_enr_tools.py` (fait sans trace refusé), `app/test_app_views.py` (le rapport exporté ne contient aucun nouveau chiffre). |
| Règles de confiance A/B/C | `enrichment/test_enr_tools.py::test_confidence_levels` (la table de décision), `enrichment/test_enr_agent.py` (plafond à C) et `integration/test_enrichment_integration.py` (B bloque une exclusion dure, C ne bloque jamais). |
| Interface (`AppTest` de Streamlit) | `app/test_app_streamlit.py`, avec `pytest.importorskip("streamlit")` : le fichier se saute si Streamlit n'est pas installé. |
| Chiffres du README reliés aux fichiers de résultats | `orchestrator/test_orch_bench.py` : `test_the_readme_quotes_the_results_file` vérifie que chaque total `x/y` de `deploy/bench_results.json` apparaît dans le `README.md` racine, et `test_the_rules_score_is_recomputed_not_remembered` vérifie que le score des règles du fichier est recalculé à l'identique. Le score de Bedrock, lui, n'est pas rejouable sans AWS : le test ne le recalcule pas. |

## 5. Écrire un nouveau test

Conventions observées dans le code existant :

1. **Placement.** Mettez le test dans le dossier de l'agent concerné. Un nouveau fichier s'appelle `test_<préfixe>_<sujet>.py` (`test_gov_`, `test_expl_`, `test_enr_`, `test_orch_`, `test_app_`), avec une docstring de module en français qui dit ce que le fichier couvre.
2. **Nom du test.** Une phrase descriptive en anglais, en minuscules avec des tirets bas, qui énonce le comportement attendu : `test_future_publications_never_reach_the_ledger`. Les docstrings et commentaires sont en français.
3. **Un comportement par test.** Plusieurs `assert` sont acceptés s'ils décrivent le même comportement. Pour plusieurs cas du même comportement, utilisez `@pytest.mark.parametrize`.
4. **Données.** Construisez une série à résultat connu avec les fixtures du dossier (`make_store` dans `quant`, `store` et `run` ailleurs). Évitez le jeu de départ réel, sauf pour un test de fumée marqué `@pytest.mark.smoke` ou `pytestmark = pytest.mark.smoke`.
5. **Aucun réseau.** Utilisez une doublure : `FakeClient` ou `FakeLLM` pour le modèle, `FakeS3` pour S3, `FakeProvider` ou `Fake` pour les fournisseurs, une fonction `http(url, headers)` pour la SEC. Utilisez `tmp_path` et `monkeypatch` pour tout fichier ou variable d'environnement.
6. **Aides partagées.** N'ajoutez pas de `from conftest import ...` : il casse le lancement combiné des dossiers (section 1). Mettez les aides dans un module au nom unique, comme `helpers_gov.py`, et importez-le avec `from helpers_gov import ...`.
7. **Pas de logique par persona.** Les tests d'orchestration, de gouvernance et d'enrichissement vérifient que seule la valeur des champs du profil compte, pas son nom (`test_profile_identity_does_not_change_judgement`, `test_the_decision_does_not_depend_on_a_persona_only_on_profile_fields`). Un nouveau test doit suivre la même règle.
8. **Vérification.** Lancez le fichier seul, puis le dossier, puis la suite complète avant de proposer le changement.

## 6. Intégration continue

Le workflow `.github/workflows/tests.yml` s'appelle `tests`. Il se déclenche à chaque `push`, à chaque `pull_request` et à la demande (`workflow_dispatch`). Il n'a que le droit de lecture sur le dépôt (`contents: read`), et une exécution en cours est annulée quand un envoi plus récent arrive sur la même référence.

Le job `pytest` (délai maximal de 20 minutes) s'exécute sur `ubuntu-latest`, dans le conteneur `public.ecr.aws/docker/library/python:3.9-slim`. C'est l'image de base de l'application déployée (voir le `Dockerfile`) : les tests tournent donc avec la même version de Python que la production. Le workflow définit `ALPHAFORGE_LLM=0`, `ALPHAFORGE_S3_BUCKET=""` et `ALPHAFORGE_HOT_RELOAD=0`, et aucun secret.

Les étapes sont :

1. `actions/checkout@v5`.
2. Installation de `deploy/requirements.lock.txt` (versions exactes de l'image déployée) et de `pytest==8.4.2`.
3. `python -m pytest -q` : la suite complète, sans argument, donc sans le problème de collecte décrit en section 1.
4. Installation de `cfn-lint`, puis `cfn-lint deploy/build-stack.yaml deploy/app-stack.yaml` : validation des deux modèles CloudFormation. Cette étape n'est pas une étape de test pytest.

La CI ne lance pas de commande séparée pour les tests de fumée : ils font partie de la suite complète et se sautent si le jeu de départ est absent du dépôt cloné.
