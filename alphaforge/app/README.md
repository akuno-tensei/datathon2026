# Interface et assemblage (`alphaforge/app/`)

Ce dossier relie les quatre agents du noyau (orchestrateur, analyste, gouvernance, explicabilité) et l'agent d'enrichissement à une interface Streamlit. Ce document s'adresse à une personne qui rejoint l'équipe ou à un jury technique. La vue d'ensemble du projet, les agents et les décisions de calcul sont dans le [README racine](../../README.md). Le déploiement sur AWS est décrit dans [`deploy/README.md`](../../deploy/README.md).

AlphaForge AI est une simulation d'aide à la décision. Rien ici ne constitue un conseil financier.

## 1. Rôle : aucune logique métier

Le dossier `app/` ne calcule aucune mesure de marché, ne décide d'aucun verdict et ne rédige aucune explication. Il fait quatre choses :

1. Il assemble les agents sur les données réelles (`pipeline.py`).
2. Il transforme le registre de preuves en structures d'affichage (`views.py`).
3. Il affiche ces structures (`streamlit_app.py`) en français ou en anglais (`i18n.py`, `labels.py`).
4. Il archive chaque analyse dans Amazon S3 si on le demande (`archive.py`).

Les valeurs lues par l'interface viennent toutes du registre (`analysis.result.ledger`) ou du verdict de la gouvernance. Les fonctions de `views.py` ne dépendent pas de Streamlit, ce qui permet de les tester sans interface (`tests/app/test_app_views.py`). La règle est écrite dans l'en-tête de `views.py` et dans `.kiro/steering/app-interface.md`.

## 2. Modules

| Fichier | Lignes | Contenu |
|---|---|---|
| `pipeline.py` | 306 | `AlphaForge`, `Analysis`, `Comparison`, `ProfileInfo`, `build_parser`, `build_enrichment_agent`, `enrichment_coverage`, `aws_credentials_available`, classes internes `_ScenarioParser`, `_CapturingExplainer`, `_FirstAvailable`. |
| `views.py` | 612 | Vues pures : une fonction par bloc d'affichage, plus `report_markdown`. |
| `streamlit_app.py` | 743 | Le script Streamlit : bandeau, panneaux, onglets, rendu des vues. |
| `labels.py` | 244 | Libellés lisibles, noms de périodes, dates, lexique. |
| `i18n.py` | 273 | Textes de l'interface, 319 clés en français et 319 en anglais, fonction `t`. |
| `forms.py` | 138 | Fonctions pures pour le formulaire de profil et la saisie des poids. |
| `archive.py` | 86 | Archive S3 du registre et d'un résumé. |
| `_reload.py` | 40 | Rechargement à chaud du code pendant le développement. |
| `profiles/user/` | | Dossier des profils créés depuis l'interface (vide dans le dépôt, exclu de l'image Docker par `.dockerignore`). |

Les nombres de lignes viennent de `wc -l` au moment de la rédaction.

## 3. `AlphaForge` (`pipeline.py`)

### Construction

`AlphaForge(store, user_profiles_dir=None, parser=None, archive=None)` garde le `DataStore`, la liste des actifs (`store.assets.to_dict("records")`), l'interpréteur de demandes et l'archive. Sans argument, `parser` vient de `build_parser()` et `archive` de `build_archive()` (section 9). Le dossier des profils créés est créé s'il manque (`USER_PROFILES_DIR` par défaut, soit `app/profiles/user/`). `AlphaForge.from_directory(data_dir=None, **kw)` charge les données depuis `alphaforge_starter/` (`DEFAULT_DATA_DIR` dans `quant/config.py`).

### `analyze`

```
analyze(request_text, profile, as_of=None, base_currency=None, language=None, weights=None,
        rebalancing=None, on_entry=None, enrichment=True, sec_user_agent=None) -> Analysis
```

Déroulement, dans l'ordre du code :

1. `get_profile` charge le profil (un identifiant, un dictionnaire ou un `InvestorProfile`). Pour un identifiant, le dossier des profils créés est cherché avant celui des exemples.
2. `base_currency` (mise en majuscules) et `language` s'appliquent à une copie du profil (`model_copy`) pour cette analyse seulement. Le profil enregistré ne change pas.
3. `as_of` vaut `store.last_date` s'il est absent.
4. Si `weights` ou `rebalancing` est fourni, l'interpréteur est enveloppé dans `_ScenarioParser`. Les poids du formulaire s'ajoutent à ceux du texte et gagnent en cas de conflit. Les tickers inconnus de l'univers sont écartés. L'intention devient `build_scenario` quand il y a au moins deux actifs. Les poids restent des fractions, jamais normalisés ici : l'analyste valide lui-même la somme.
5. `on_entry`, s'il est fourni, est transmis à l'orchestrateur et reçoit chaque entrée du registre au moment de son écriture (trace en direct, section 6).
6. Si `enrichment` est vrai, `build_enrichment_agent` fournit l'agent d'enrichissement, avec l'identification SEC passée en argument ou, à défaut, la variable `ALPHAFORGE_SEC_USER_AGENT`. Sinon le noyau à quatre agents travaille seul.
7. L'`Orchestrator` reçoit `QuantAnalyst`, `Governance`, l'explicabilité (enveloppée dans `_CapturingExplainer`, qui garde l'`Explanation` structurée en plus du texte) et exécute la demande.
8. Le verdict est le payload de la dernière entrée `verdict` du registre, ou `None` s'il n'y en a pas.
9. `archive_notes` tente l'archive S3 et range ses messages dans `Analysis.archive_notes`.

### `Analysis`

`Analysis` est un `dataclass` avec : `request_text`, `profile` (la copie utilisée), `as_of`, `base_currency`, `result` (`RunResult` de l'orchestrateur, donc `status`, `message`, `answer`, `ledger`, `request`), `verdict`, `explanation`, `enrichment_enabled` et `archive_notes`.

### `compare`

`compare(request_text, left, right, as_of=None, base_currency=None, language=None, enrichment=True, sec_user_agent=None)` appelle `analyze` deux fois avec la même demande et renvoie une `Comparison(request_text, left, right)`. Les données sont identiques, seules les limites dérivées des profils changent. `enrichment` et `sec_user_agent` sont transmis aux deux analyses : l'onglet « Comparer deux profils » suit l'interrupteur du bandeau et l'identification saisie (test `test_comparison_follows_the_enrichment_switch`).

### Profils : listage, création, suppression

| Méthode | Comportement |
|---|---|
| `list_profiles()` | Lit les `*.json` des exemples (`PROFILES_DIR`, dans `orchestrator/profiles/`, trois fichiers : `amira`, `mateo`, `robert`) puis ceux des profils créés. Un fichier invalide est ignoré sans erreur. En cas d'identifiant commun, le profil créé remplace l'exemple dans la liste, ce que `save_profile` empêche. Résultat trié par identifiant, avec l'origine `exemple` ou `créé`. |
| `profile_origin(id)` | `exemple`, `créé` ou `None`. |
| `save_profile(data, overwrite=False)` | Valide avec `load_profile`, puis exige un identifiant qui respecte `^[a-z0-9_-]{1,40}$` (`invalid_profile_id`). Refuse d'écraser un exemple (`profile_exists_example`) et refuse d'écraser un profil créé sans `overwrite=True` (`profile_exists`). Écrit `<id>.json` en UTF-8. |
| `delete_profile(id)` | Refuse un identifiant hors du motif ci-dessus, par exemple `../x` (`invalid_profile_id`), un profil absent (`profile_not_found`) et un exemple (`profile_not_deletable`). Vérifie en plus que le chemin résolu est directement dans le dossier des profils créés avant `unlink`. |

Les erreurs sont des `ProfileError` : l'attribut `reason` porte le code, `message` le texte.

### `build_parser` et la détection des identifiants AWS

`build_parser()` choisit l'interpréteur de demandes :

1. Si `ALPHAFORGE_LLM` vaut `0`, il renvoie `RuleBasedParser` (règles déterministes).
2. Sinon `aws_credentials_available()` demande à `boto3.session.Session().get_credentials()` si des identifiants existent (variables d'environnement, fichier, rôle de la machine ou de la tâche). Cette fonction ne contacte aucun service. Sans identifiants, ou si `boto3` est absent, le résultat est `RuleBasedParser`, ce qui évite une tentative d'appel à chaque demande sur un poste sans compte AWS.
3. Sinon il construit un `BedrockClient` avec `ALPHAFORGE_LLM_MODEL` ou, à défaut, `DEFAULT_LLM_MODEL` (`global.anthropic.claude-haiku-4-5-20251001-v1:0`), dans la région `AWS_REGION`, puis `AWS_DEFAULT_REGION`, puis `us-east-1`. Une exception à la construction renvoie aussi `RuleBasedParser`.
4. Le client est enveloppé dans `LLMParser`. Dans `orchestrator/request.py`, `BedrockClient` fixe `connect_timeout=3`, un délai de lecture de 8,0 s (`timeout_s`) et au plus 2 tentatives (`retries={"max_attempts": 2, "mode": "standard"}`). Un échec d'appel retombe sur les règles, et la demande est alors marquée `rules(fallback)` (`LLMParser`, ligne 250 du même fichier).

Le choix du modèle vient du banc d'essai décrit dans [`deploy/README.md`](../../deploy/README.md). Le code ne recopie aucun score.

### `build_enrichment_agent` et `enrichment_coverage`

`build_enrichment_agent(sec_user_agent=None, cache_dir=None, today=None, snapshot_dir=None)` branche seulement les sources réellement disponibles. Une source absente produit une lacune explicite dans le registre, jamais une valeur inventée.

| Outil de collecte | Source |
|---|---|
| `fetch_filings`, `fetch_fundamentals` | Avec une identification SEC non vide : SEC EDGAR en direct (`CachingProvider(EdgarProvider(...), cache)`), qui écrit le résultat dans le cache et retombe sur le cache si l'appel échoue. Puis, si cet appel échoue aussi, l'instantané versionné `alphaforge_starter/enrichment_snapshot/` (`_FirstAvailable`). Sans identification : le cache local si le dossier du type existe, puis l'instantané. |
| `fetch_esg`, `fetch_news`, `fetch_etf_holdings` | Seulement le cache local (`app/cache/enrichment/<type>/<symbole>.json`) si le dossier du type existe. Aucune source en direct n'est branchée. |

`enrichment_coverage(...)` dit, pour chaque outil, `live`, `cache`, `snapshot` ou `absent`. L'interface l'affiche tel quel. Elle sert à voir ce qui n'est pas branché. Elle ne teste pas le réseau : `live` signifie seulement qu'une identification SEC est fournie.

## 4. Vues (`views.py`)

Chaque vue reçoit une `Analysis` (parfois une `Comparison`) et la langue. Les textes passent par `i18n.t`.

| Fonction | Rend | Ce qu'elle lit |
|---|---|---|
| `status_badge` | `Badge(icon, label, status)` pour `conforme`, `avertissement`, `bloqué`. | Texte `i18n`. |
| `entry_to_step`, `agent_trace` | Étape de trace par entrée du registre, puis chronologie complète. | Entrées `profile`, `request`, `plan`, `step` (outil appelé, arguments, `evidence_id`, étapes `fb…` de la boucle de rétroaction), `verdict`, `enrichment`, `note` (`needs_detected`, `needs_relaunched`, `enrichment_done`, `enrichment_failed`, notes de l'explicabilité). Une entrée `source_card` ne donne pas d'étape. L'étape d'explicabilité de `agent_trace` vient de `analysis.explanation.report`. |
| `feedback_iterations` | Nombre d'itérations de la boucle gouvernance, analyste. | Entrées `step` dont l'`step_id` commence par `fb`. |
| `verdict_measures` | Pour chaque actif et alternative, deux barres : volatilité et perte maximale, mesure contre limite. | Preuves `compute_metrics` non refusées (la pire fenêtre par indicateur) et `verdict["constraints"]` (limites, niveau de risque appliqué, ajustements). |
| `asset_reasons` | Raisons de la gouvernance avec icône et nature (contrainte dure, préférence souple, limite dérivée, donnée, périmètre, profil). | `reasons` de chaque actif du verdict. |
| `sensitivity_table` | Toutes les périodes testées avec l'indicateur stable, sensible ou indéterminé et le seuil. | Dernière preuve `stress_window` de l'actif ; à défaut, les preuves `compute_metrics` (sans indicateur). L'interface ne calcule pas l'indicateur. |
| `correlation_view` | Matrice de référence, observations, matrice complémentaire, actifs exclus, texte alternatif. | Dernière preuve `correlation_matrix`. |
| `portfolio_view` | Scénario de portefeuille : mesures, répartition, concentration, contributions au risque, identifiant de la série. | Dernière preuve `portfolio_metrics`. |
| `enrichment_view` | Mode (`off`, `idle`, `complete`, `partial`, `degraded`), besoins, lignes, fiches source. | Entrées `enrichment`, `source_card` et notes d'enrichissement. `off` si l'enrichissement est désactivé ; `degraded` s'il y a un échec ou seulement des lacunes ; `partial` s'il y a des lacunes et des données. |
| `explanation_sections`, `limit_sections`, `verification_banner` | Énoncés vérifiés par section, limites, bandeau de vérification. | `analysis.explanation`. Le niveau « résumé » garde les sections `summary`, `fit` et `scope` ; les limites, incertitudes, hypothèses et éléments non couverts sont toujours entiers. |
| `evidence_card`, `resolve_evidence`, `reproduce_arguments`, `related_call` | Fiche de preuve avec la commande `run_tool(store, ...)` qui la reproduit. | Preuves du registre. Une preuve interne, citée par une autre sans être au registre (ligne d'un `stress_window`, contrôle de qualité d'un `compute_metrics`), est recalculée par `run_tool` et gardée seulement si son `evidence_id` est identique. |
| `clarify_options`, `out_of_universe` | Candidats cliquables d'une requête ambiguë, requêtes hors univers. | Preuves `asset_lookup` (`ambiguous`, code d'erreur `NOT_IN_UNIVERSE`). |
| `comparison_diffs` | Différences de limites dérivées et de statuts par actif entre deux profils. | Verdicts des deux analyses. |
| `report_markdown` | Rapport exportable en Markdown. | Uniquement le registre : aucun chiffre nouveau (section 6). |

**Conclusions.** `conclusion_lines(analysis, lang)` renvoie la conclusion en langage simple de l'explicabilité (affichée par `render_conclusion` à la fin de l'analyse, après les limites, quel que soit le niveau de lecture) ; `explanation_sections` l'exclut pour ne pas la répéter. `comparison_conclusion(cmp, lang)` construit celle de l'onglet « Comparer deux profils » à partir des deux verdicts seulement, sans chiffre (voir `alphaforge/explainability/README.md`, section 5.10).

## 5. Libellés lisibles (`labels.py`)

Règle : **affichage seulement**. Les valeurs enregistrées, les codes transmis aux agents et les messages des agents ne changent jamais. Un test vérifie que les nombres restent identiques après reformulation (`test_numbers_are_never_changed_by_the_rewording`). L'incident d'origine, documenté dans `.kiro/steering/app-interface.md` : un remplacement de « . » par « , » sur un message entier avait transformé « SHOP.TO » en « SHOP,TO ».

| Fonction | Rôle |
|---|---|
| `human(value, lang)` | Code vers libellé (`faible_modere` donne « Faible à modéré »). Un code inconnu perd ses « _ » et prend une majuscule. Une valeur qui contient déjà une majuscule (ticker, secteur) reste telle quelle. |
| `humanize_text(text, lang)` | Remplace dans une phrase les seuls codes connus et les noms de fenêtres, et met les dates ISO en forme lisible. Un mot inconnu n'est jamais modifié. |
| `window_name(code, lang)` | Nom d'une période : « année 2022 », « toute la période », ou « du 1 janv. 2023 au 12 nov. 2026 » pour un intervalle `début/fin`. |
| `human_date(iso, lang)` | `2021-11-12` donne « 12 nov. 2021 » (fr) ou « Nov 12, 2021 » (en). Autre valeur : inchangée. |
| `GLOSSARY` | Lexique de 16 termes par langue, affiché dans le panneau « Lexique ». |

## 6. L'application Streamlit (`streamlit_app.py`)

### Démarrage du script

Le script ajoute la racine du projet à `sys.path`, appelle `refresh` (section 10), puis crée l'instance unique `AlphaForge` par `forge(CODE_STAMP)`, une fonction décorée par `st.cache_resource(max_entries=1)` dont la clé est l'empreinte du code.

### Bandeau supérieur

- Titre, choix de la langue (`fr` ou `en`), sélection du profil, boutons « Ajouter » et « Supprimer », date d'analyse (bornée par `store.first_date` et `store.last_date`) et devise de base (CAD ou USD).
- Un avertissement de simulation, `st.warning`, toujours visible.
- Un interrupteur du mode : « Enrichi » (cinq agents) ou « Noyau » (quatre agents). En mode enrichi, la ligne de couverture montre ce que `enrichment_coverage` renvoie.
- Le panneau « Lexique ».
- Les messages d'enregistrement ou de suppression de profil (`pf_flash`, `pf_flash_error`).

### Panneau de gestion des profils

Il s'ouvre sous le bandeau, hors des onglets, donc il est accessible depuis chaque vue.

- **Ajouter** : formulaire vierge ou copie du profil actif (identifiant et nom vidés). Il permet de choisir langue, expérience, devise, tolérance au risque, horizon, objectifs, préférences pondérées et exclusions. Un aperçu en direct montre la fiche, les incohérences et les limites dérivées. L'enregistrement est désactivé si le profil est invalide, si l'identifiant est vide ou différent de son `slugify`, si l'identifiant est celui d'un exemple, ou si un profil créé existe déjà sans confirmation de remplacement. Après l'enregistrement, le panneau se ferme et le nouveau profil devient actif.
- **Supprimer** : liste seulement les profils créés, exige une case de confirmation, puis appelle `delete_profile`. Si le profil actif disparaît, la sélection retombe sur `robert` ou sur le premier profil.

### Onglets

1. **Analyse**, en trois colonnes.
   - Gauche : fiche du profil actif (`render_profile_panel`) avec ses limites dérivées (`derive_constraints`).
   - Centre : choix de deux actifs, quatre raccourcis (évaluer, comparer, scénario, expliquer un risque) qui préremplissent la demande, champ de demande, bouton « Envoyer », constructeur de scénario. Après une analyse : bouton d'export Markdown, parseur utilisé, notes d'archive S3, puis `render_verdict`, `render_enrichment`, `render_explanation`, `render_sensitivity`, `render_correlation`, `render_portfolio` et `render_limits`.
   - Droite : le tiroir de preuve quand une preuve est ouverte, puis la trace des agents.
2. **Comparer deux profils** : même demande, deux profils (`amira` et `robert` par défaut s'ils existent), différences de limites et de statuts, verdict et explication de chaque côté. La comparaison appelle `F.compare`.
3. **Données et sources** : sources de l'analyse, tableau `sources_metadata.csv` de `alphaforge_starter/`, lacunes et substituts signalés par la gouvernance, empreinte des données, champ d'identification SEC (mémorisé dans la session seulement, jamais écrit sur disque, vérifié par `test_sec_identification_is_session_only_never_written_to_disk`), couverture des sources et fiches source.

### Constructeur de scénario

L'utilisateur choisit les actifs ; les poids partent de poids égaux (`forms.equal_weights`). Les boutons « poids égaux » et « normaliser » sont des actions explicites. Le bouton de lancement n'est actif qu'avec au moins deux actifs et une somme de 100 % (`forms.weights_ok`). La demande est ensuite envoyée avec `weights=forms.to_fractions(...)` et `rebalancing` (`rebalanced` ou `buy_and_hold`).

### Trace en direct

`run_analysis` ouvre un `st.status` et passe à `F.analyze` une fonction `on_entry`. Pour chaque entrée du registre, `views.entry_to_step` fournit l'étape, qui s'affiche avec le temps écoulé depuis la précédente. Ces durées sont mesurées par l'interface avec `time.perf_counter`, jamais stockées. Après l'analyse, `render_trace` rend la même chronologie à partir du registre complet (même fonction de rendu des étapes), avec le détail de chaque étape et un lien vers sa preuve.

### Tiroir de preuve

Un bouton « Preuve » (identifiant complet dans l'infobulle) place l'identifiant dans `S.evidence_open`. `render_drawer` affiche alors l'outil et sa version, le nombre d'observations, la période, les paramètres, les données d'entrée avec leur `sha256` tronqué, les sources, les avertissements, l'empreinte des données, un graphique (perte depuis un sommet, avec texte alternatif) et le code Python pour reproduire l'appel.

### Export Markdown

`st.download_button` livre `views.report_markdown(a, lang, F.store)` sous le nom `alphaforge_<profil>_<as_of>.md`. Le rapport contient la bannière de simulation, la demande, le profil, le verdict, l'explication, les tableaux de sensibilité, le scénario, l'enrichissement, la liste des preuves avec la commande de reproduction et les sources. Un test vérifie qu'il ne contient aucun chiffre absent du registre (`test_report_export_contains_banner_evidence_and_no_new_number`).

### Lexique

`GLOSSARY` explique 16 termes (volatilité, perte maximale, période de mesure, année 2022, corrélation, limites dérivées, niveau de confiance A, B, C, etc.), en français et en anglais, dans un panneau repliable visible depuis tous les onglets.

## 7. Bilinguisme (`i18n.py`)

`STRINGS` contient deux tables, `fr` et `en`, et `t(key, lang, **kw)` renvoie le texte formaté. Si la clé manque dans la langue demandée, `t` retombe sur le français, puis sur la clé elle-même. Aucun texte d'interface n'est écrit en dur dans les vues. `test_labels_are_bilingual_and_never_prescriptive` vérifie que les deux tables ont les mêmes clés et que les textes ne contiennent aucune formule interdite (« achetez », « vendez », « garanti », « meilleur choix »...). Les libellés de `labels.py` et ceux des gabarits de l'explicabilité (`explainability.config`) sont bilingues de leur côté.

## 8. Formulaires de profil et de scénario (`forms.py`)

Toutes les fonctions sont pures.

| Fonction | Rôle |
|---|---|
| `parse_criteria(text, with_weight)` | Lit des lignes `type:valeur[:poids]` ; lève `ValueError` avec le numéro de ligne. |
| `equal_weights`, `weights_sum`, `weights_ok`, `normalize`, `to_fractions` | Poids d'un scénario en pourcentages. `weights_ok` exige au moins un actif, des poids strictement positifs et une somme de 100 avec une tolérance de `WEIGHT_TOLERANCE_PCT = 1e-4`. `normalize` ne s'exécute que sur action de l'utilisateur. |
| `slugify` | Identifiant sûr (minuscules, sans accents, « _ » entre les mots, 40 caractères au plus). |
| `add_criterion`, `remove_criterion` | Refusent un type inconnu, une valeur vide, un doublon (insensible à la casse) et un poids hors de [0, 1]. |
| `build_profile_dict` | Dictionnaire prêt pour `load_profile`, sans correction de valeur. |
| `form_state_from_profile` | Valeurs du formulaire à partir d'un profil existant. |
| `criterion_options` | Valeurs que les données savent vérifier, par type de critère. Un thème libre reste possible : la gouvernance le signale non vérifiable. |

## 9. Archive S3 (`archive.py`)

L'archive n'existe que si `ALPHAFORGE_S3_BUCKET` est défini et non vide (`build_archive`). Si `boto3` manque, il n'y a pas d'archive et l'analyse continue.

- `S3Archive.put(summary, ledger)` écrit deux objets sous `runs/AAAA-MM-JJ/<HHMMSS>-<empreinte>/` : `ledger.jsonl` (une entrée par ligne, clés triées) et `summary.json`. L'empreinte est les 12 premiers caractères du SHA-256 du registre : un même registre donne la même empreinte. Les deux objets sont écrits avec `ServerSideEncryption="AES256"`.
- Le client S3 est créé avec `connect_timeout=3`, `read_timeout=8` et au plus 2 tentatives (mode `standard`).
- `summarize` garde de quoi retrouver et rejouer l'analyse : demande, profil, date, devise, langue, interpréteur, intention, actifs, statuts, mode d'enrichissement, nombre d'entrées et de preuves, réponse.
- `archive_notes` ne lève jamais d'exception : une panne devient une note affichée dans la zone d'analyse (« Archive S3 indisponible… »), et l'analyse s'affiche quand même.

Les droits S3 de la tâche déployée se limitent à `s3:PutObject` sous `runs/*` (voir `deploy/README.md`).

## 10. Rechargement à chaud (`_reload.py`)

Streamlit relit le script à chaque action, mais garde en mémoire les modules déjà importés. Après une modification de `pipeline.py`, un serveur resté ouvert exécutait donc le nouveau script avec l'ancienne classe (erreur `'AlphaForge' object has no attribute ...`). `refresh(root)` calcule l'empreinte du code (date de modification la plus récente des `.py` sous `alphaforge/`, hors `__pycache__`) et la compare à celle du dernier appel. Si elle a changé, il retire de `sys.modules` les modules du paquet, sauf `alphaforge`, `alphaforge.app` et `alphaforge.app._reload`. L'empreinte sert aussi de clé au cache de `forge`.

`ALPHAFORGE_HOT_RELOAD=0` mesure l'empreinte sans purger. Le fichier `conftest.py` le force à `0` pendant les tests, et le `Dockerfile` le fixe à `0` dans l'image.

## 11. Variables d'environnement

| Variable | Effet | Défaut | Lue dans |
|---|---|---|---|
| `ALPHAFORGE_LLM` | `0` désactive Bedrock (règles seules). | `1` | `pipeline.build_parser` |
| `ALPHAFORGE_LLM_MODEL` | Identifiant du modèle Bedrock. | `DEFAULT_LLM_MODEL` | `pipeline.build_parser` |
| `AWS_REGION`, `AWS_DEFAULT_REGION` | Région de Bedrock et de S3 (la première non vide). | `us-east-1` | `pipeline.build_parser`, `archive.build_archive` |
| `ALPHAFORGE_S3_BUCKET` | Active l'archive S3. | vide : pas d'archive | `archive.build_archive` |
| `ALPHAFORGE_SEC_USER_AGENT` | Identification SEC (nom et courriel) pour SEC EDGAR en direct. | vide | `pipeline.analyze`, `streamlit_app.py` |
| `ALPHAFORGE_HOT_RELOAD` | `0` désactive la purge des modules. | `1` | `streamlit_app.py` |

Les identifiants AWS ne sont jamais lus par le code du projet : `boto3` applique sa chaîne habituelle. Dans l'interface, le champ SEC de l'onglet « Données et sources » prime sur la variable d'environnement pour la session. Utiliser une identification de contact de l'équipe, par exemple `Equipe exemple contact@exemple.org`, et ne jamais l'écrire dans un fichier du dépôt.

## 12. Lancement local

Depuis la racine du dépôt, avec Python 3.9 et les paquets de `deploy/requirements.lock.txt` :

```bash
streamlit run alphaforge/app/streamlit_app.py
```

Pour travailler sans AWS : `ALPHAFORGE_LLM=0 streamlit run alphaforge/app/streamlit_app.py`. Sans identifiants AWS, Bedrock n'est de toute façon pas tenté.

**Derrière le proxy de l'éditeur.** Ajouter `--server.enableXsrfProtection false` :

```bash
streamlit run alphaforge/app/streamlit_app.py --server.enableXsrfProtection false
```

La raison, notée dans `.kiro/steering/app-interface.md` : le cookie anti-falsification de Streamlit fait dépasser à nginx sa limite d'en-têtes, ce qui donne une erreur 502 sur `_stcore/health`. Cette cause n'a pas été reproduite pour cette documentation. La protection est active par défaut (`server.enableXsrfProtection` vaut `True` avec Streamlit 1.50.0 installé ici). La désactiver convient à un poste de développement derrière ce proxy. L'image déployée ne la désactive pas (`CMD` du `Dockerfile`).

## 13. Exemples vérifiés

Chaque exemple s'exécute depuis la racine du dépôt, par exemple `cd /workshop/datathon2026 && ALPHAFORGE_LLM=0 python3 -W ignore -c "<code>"`. Ils n'utilisent ni réseau ni AWS. Les profils créés vont dans un dossier temporaire.

**Analyser une demande et lire les mesures contre limites**

```python
import tempfile
from pathlib import Path
from alphaforge.app.pipeline import AlphaForge
from alphaforge.app.views import verdict_measures, status_badge
from alphaforge.orchestrator import RuleBasedParser

forge = AlphaForge.from_directory(user_profiles_dir=Path(tempfile.mkdtemp()), parser=RuleBasedParser(), archive=None)
a = forge.analyze("Évalue NVDA", "robert", enrichment=False)
print(a.result.status, a.verdict["status"], status_badge(a.verdict["status"], "fr").label)
for m in verdict_measures(a, "fr")["NVDA"]:
    print(f"{m.label}: {m.value:.1%} pour une limite de {m.limit:.1%}, respectée: {m.ok}")
```

Sortie observée :

```
complete avertissement AVERTISSEMENT
Volatilité maximale: 61.3% pour une limite de 15.0%, respectée: False
Perte maximale tolérée: 63.5% pour une limite de 20.0%, respectée: False
```

**Créer et supprimer un profil, et voir les garde-fous**

```python
import tempfile
from pathlib import Path
from alphaforge.app.pipeline import AlphaForge
from alphaforge.orchestrator import ProfileError, RuleBasedParser

forge = AlphaForge.from_directory(user_profiles_dir=Path(tempfile.mkdtemp()), parser=RuleBasedParser(), archive=None)
data = {"profile_id": "exemple_prudent", "display_name": "Exemple prudent", "horizon_years_min": 5,
        "horizon_years_max": 10, "risk_tolerance": "faible", "objectives": ["preservation_capital"]}
forge.save_profile(data)
print(forge.profile_origin("exemple_prudent"), forge.profile_origin("robert"))
def code(fn, *args, **kw):
    try:
        fn(*args, **kw)
    except ProfileError as e:
        return e.reason
print(code(forge.save_profile, data))
print(code(forge.save_profile, dict(data, profile_id="robert")))
print(code(forge.delete_profile, "robert"))
print(code(forge.delete_profile, "../x"))
print(code(forge.delete_profile, "absent"))
forge.delete_profile("exemple_prudent")
print(forge.profile_origin("exemple_prudent"))
```

Sortie observée :

```
créé exemple
profile_exists
profile_exists_example
profile_not_deletable
invalid_profile_id
profile_not_found
None
```

**Libellés lisibles : affichage seulement**

```python
from alphaforge.app.labels import human, humanize_text, window_name, human_date
print(human("faible_modere"), "|", human("faible_modere", "en"), "|", human("NVDA"), "|", human("inconnu_x"))
print(window_name("2022"), "|", window_name("2023-01-01/2026-11-12"), "|", window_name("3_ans", "en"))
print(human_date("2021-11-12"), "|", human_date("2021-11-12", "en"))
print(humanize_text("Volatilité de 12.5 % sur la fenêtre « 2022 » pour un profil faible_modere, publié le 2021-11-12."))
```

Sortie observée (le nombre `12.5` reste intact) :

```
Faible à modéré | Low to moderate | NVDA | Inconnu x
année 2022 | du 1 janv. 2023 au 12 nov. 2026 | last 3 years
12 nov. 2021 | Nov 12, 2021
Volatilité de 12.5 % en 2022 pour un profil faible à modéré, publié le 12 nov. 2021.
```

**Choix de l'interpréteur et couverture des sources**

```python
import os
from alphaforge.app import pipeline
print(type(pipeline.build_parser()).__name__, os.environ["ALPHAFORGE_LLM"])
print(pipeline.enrichment_coverage(None))
print(pipeline.enrichment_coverage("Equipe exemple contact@exemple.org"))
```

Avec `ALPHAFORGE_LLM=0`, sortie observée (le dossier `app/cache` n'existe pas dans cet environnement, donc le cache n'apparaît pas) :

```
RuleBasedParser 0
{'fetch_filings': 'snapshot', 'fetch_esg': 'absent', 'fetch_fundamentals': 'snapshot', 'fetch_news': 'absent', 'fetch_etf_holdings': 'absent'}
{'fetch_filings': 'live', 'fetch_esg': 'absent', 'fetch_fundamentals': 'live', 'fetch_news': 'absent', 'fetch_etf_holdings': 'absent'}
```

**Comparer deux profils sur la même demande**

```python
import tempfile
from pathlib import Path
from alphaforge.app.pipeline import AlphaForge
from alphaforge.app.views import comparison_diffs
from alphaforge.orchestrator import RuleBasedParser

forge = AlphaForge.from_directory(user_profiles_dir=Path(tempfile.mkdtemp()), parser=RuleBasedParser(), archive=None)
cmp = forge.compare("Évalue NVDA", "amira", "robert")
for d in comparison_diffs(cmp, "fr"):
    print(d.text)
```

Sortie observée :

```
limite de Volatilité maximale : 65% pour amira, 15% pour robert
limite de Perte maximale tolérée : 80% pour amira, 20% pour robert
NVDA : CONFORME pour amira, AVERTISSEMENT pour robert
```

**Fonctions du formulaire**

```python
from alphaforge.app import forms
print(forms.parse_criteria("sector:Energy\nticker:TSLA:0,8", with_weight=True))
w = forms.equal_weights(["SPY", "AGG", "GLD"])
print(w, forms.weights_sum(w), forms.weights_ok(w))
print(forms.weights_ok({"SPY": 60.0, "AGG": 39.0}), forms.normalize({"SPY": 60.0, "AGG": 39.0}))
print(forms.to_fractions({"SPY": 60.0, "AGG": 40.0}))
print(forms.slugify("Équipe Épargne 2026!"))
try:
    forms.add_criterion([{"kind": "ticker", "value": "TSLA"}], "ticker", "tsla")
except ValueError as e:
    print(e)
```

Sortie observée :

```
[{'kind': 'sector', 'value': 'Energy'}, {'kind': 'ticker', 'value': 'TSLA', 'weight': 0.8}]
{'SPY': 33.4, 'AGG': 33.3, 'GLD': 33.3} 100.0 True
False {'SPY': 60.6, 'AGG': 39.4}
{'SPY': 0.6, 'AGG': 0.4}
equipe_epargne_2026
« ticker : tsla » figure déjà dans la liste.
```

**Archive S3 avec un client factice (aucun réseau)**

```python
import tempfile
from pathlib import Path
from alphaforge.app.archive import S3Archive
from alphaforge.app.pipeline import AlphaForge
from alphaforge.orchestrator import RuleBasedParser

class FakeS3:
    def __init__(self):
        self.objects = {}
    def put_object(self, Bucket, Key, Body, ContentType, ServerSideEncryption):
        self.objects[Key] = (ContentType, ServerSideEncryption, len(Body))

fake = FakeS3()
forge = AlphaForge.from_directory(user_profiles_dir=Path(tempfile.mkdtemp()), parser=RuleBasedParser(),
                                  archive=S3Archive("bucket-exemple", client=fake))
a = forge.analyze("Évalue NVDA", "robert", enrichment=False)
print(a.archive_notes[0].split("/runs/")[0])
for key, (ctype, sse, n) in fake.objects.items():
    print(key.split("/")[-1], ctype, sse)
```

Sortie observée :

```
Registre archivé dans Amazon S3 : s3://bucket-exemple
ledger.jsonl application/x-ndjson AES256
summary.json application/json AES256
```

**Trace en direct : une étape par entrée du registre**

```python
import tempfile
from pathlib import Path
from alphaforge.app.pipeline import AlphaForge
from alphaforge.app.views import entry_to_step
from alphaforge.orchestrator import RuleBasedParser

forge = AlphaForge.from_directory(user_profiles_dir=Path(tempfile.mkdtemp()), parser=RuleBasedParser(), archive=None)
seen = []
a = forge.analyze("Évalue NVDA", "robert", enrichment=False, on_entry=lambda e: seen.append(entry_to_step(e, "fr")))
print(len(seen) == len(a.result.ledger), [s.agent_label for s in seen[:3]])
print(next(s for s in seen if s is not None and s.agent == "gouvernance").text)
```

Sortie observée :

```
True ['Orchestrateur', 'Orchestrateur', 'Orchestrateur']
Verdict : AVERTISSEMENT
```

**Empreinte du code (rechargement à chaud)**

```python
import tempfile
from pathlib import Path
from alphaforge.app import _reload
root = Path(tempfile.mkdtemp())
(root / "m.py").write_text("x = 1\n")
s1 = _reload.refresh(root, purge=False)
print(s1 == _reload.refresh(root, purge=False))
```

Sortie observée : `True` (sans modification, l'empreinte ne change pas).

## 14. Tests associés

Dossier `tests/app/` (la fixture `forge` de `conftest.py` construit un petit univers synthétique à volatilités connues). Lancer `python3 -m pytest tests/app -q`.

| Fichier | Couvre |
|---|---|
| `test_app_views.py` | Pipeline à quatre agents, vues (mesures, raisons, sensibilité, corrélation, portefeuille, explication, trace, preuves recalculées à l'identique), profils créés et supprimés et leurs garde-fous, poids du formulaire, trace en direct, export Markdown, parité des clés `i18n`. |
| `test_app_streamlit.py` | L'application avec `streamlit.testing.v1.AppTest` : bandeau, raccourcis, constructeur de scénario, formulaire de profil, panneaux d'ajout et de suppression, mode enrichi ou noyau, identification SEC, lexique, libellés, fondamentaux. |
| `test_app_aws.py` | Bedrock et S3 remplacés par des doublures : repli sur les règles, archive chiffrée avec registre et résumé, panne S3 sans effet sur l'analyse, absence d'identifiants AWS, instantané SEC hors ligne sans identification dans les fichiers. |
| `test_app_labels.py` | `human`, `humanize_text`, périodes, dates, nombres inchangés. |
| `test_hot_reload.py` | Purge des modules, empreinte, désactivation, et le scénario de la classe périmée dans un processus séparé. |

`conftest.py` (racine) fixe par défaut `ALPHAFORGE_HOT_RELOAD=0`, `ALPHAFORGE_LLM=0` et `ALPHAFORGE_S3_BUCKET=""`. Les tests n'appellent donc ni Bedrock ni S3.

## 15. Limites

- **Une comparaison archive deux analyses** si S3 est activé (une par profil).
- **Couverture « live » non testée.** `enrichment_coverage` dit `live` dès qu'une identification est fournie, sans appel réseau. Un échec de SEC EDGAR se voit ensuite dans le registre (repli sur le cache, puis l'instantané, ou lacune).
- **Sources non branchées.** ESG, nouvelles et composition des FNB n'ont aucune source en direct ; seules des données déjà présentes dans le cache seraient servies.
- **Profils créés non persistants sur l'image.** Ils sont écrits dans `app/profiles/user/` du conteneur ; avec une seule tâche Fargate sans volume (voir `deploy/README.md`), ils disparaissent au remplacement de la tâche.
- **Rapport et trace.** Le rapport Markdown rappelle le registre, il ne le remplace pas. Les durées de la trace en direct sont mesurées par l'interface et ne sont pas conservées.
- **Aucune authentification des utilisateurs** dans l'interface. L'accès à l'application déployée repose sur l'adresse de la distribution CloudFront.
- **Lancement réel du serveur non exécuté** pour cette documentation : seuls les tests `AppTest` ont été lancés.
