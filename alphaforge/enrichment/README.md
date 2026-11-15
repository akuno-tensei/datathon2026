# Agent d'enrichissement (`alphaforge/enrichment/`)

Documentation détaillée de l'agent « Enrichissement » d'AlphaForge AI. Ce document décrit ce que fait le code. Il ne répète ni le
mandat ni les règles générales : pour cela, voir [`Agents.md`](../../Agents.md), section 2 (rôle, outils, garde-fous, échanges avec
les autres agents), et la section « Agent d'enrichissement » du [`README.md`](../../README.md) du projet (sources branchées,
fondamentaux de contexte, identification SEC). Le code cible Python 3.9 et Pydantic 2 (`model_validator`).

Les exemples de la section 13 sont exécutés tels quels depuis la racine du dépôt, sans réseau :
`cd /workshop/datathon2026 && ALPHAFORGE_LLM=0 python3 -W ignore -c "..."`. Les sorties affichées sont celles obtenues.

## 1. Rôle, activation, limites

**Rôle.** L'agent cherche des données publiques absentes du jeu de départ (cotes ESG, controverses, fondamentaux, dépôts
réglementaires, nouvelles, composition des FNB), les normalise, les date, leur donne un niveau de confiance (A, B ou C) et les
écrit au registre avec une fiche source. Le docstring de `agent.py` le résume ainsi : il « ne conclut rien », il produit « faits
sourcés et signaux qualitatifs ».

**Quand il s'active.** L'orchestrateur l'appelle à l'étape 3b de `Orchestrator.run` (`orchestrator/orchestrator.py`), entre le plan et
l'analyste, seulement si un agent d'enrichissement lui a été passé. Dans l'application, `AlphaForge.analyze` (`app/pipeline.py`) en
construit un par analyse quand `enrichment=True` (valeur par défaut). Il ne tourne que si `detect_needs` trouve au moins une lacune :
sans besoin, rien n'est écrit au registre (voir `_enrich`, section 10). Avec `enrichment=False`, le noyau à quatre agents travaille seul.

**Ce qu'il ne fait jamais.**

- Il ne conclut rien. Il n'émet ni verdict, ni recommandation, ni seuil.
- Il ne devine jamais une valeur. Une source en échec, absente ou sans donnée utilisable avant `as_of` produit un enregistrement
  `lacune_non_comblee`. Un indicateur absent d'un rapport annuel est omis, jamais estimé (`EdgarProvider._fundamentals`).
- Il n'écrit rien de publié après `as_of` : `point_in_time_filter` s'applique avant toute écriture, et `EnrichmentRecord` refuse
  encore un enregistrement dont `date_publication` dépasse `as_of`.
- Il ne conserve ni article ni document complet (`STORAGE_POLICY` dans `source_card.py`). Il garde des métadonnées, des valeurs
  structurées et de courts extraits.
- Il ne calcule rien à partir d'un texte : un fait tiré d'un texte est de niveau C et `usable_for_calculation` y reste faux.
- Il n'interroge pas la SEC sans identification de l'appelant (`auth_required`).

## 2. Modules

| Fichier | Rôle |
|---|---|
| `__init__.py` | Expose l'API publique (`EnrichmentAgent`, `detect_needs`, `extract_facts`, fournisseurs, modèles). |
| `agent.py` | `EnrichmentAgent` et `EnrichmentReport` : exécute la chaîne pour chaque lacune comblable et compte le résultat. |
| `config.py` | Paramètres : bornes de texte, formulaires retenus, concepts XBRL, thèmes, expressions régulières, classes d'actifs, outils par besoin. |
| `needs.py` | `detect_needs` : compare profil et demande aux données du registre et produit les `Gap`. Définit `AUTO_FUNDAMENTALS`. |
| `fetchers.py` | Les cinq outils `fetch_*` et `FETCH_TOOLS`. Ils transforment une panne en code d'erreur dans un `FetchResult`. |
| `pit.py` | `point_in_time_filter` : sépare ce qui est publié à la date d'analyse de ce qui l'est après. |
| `facts.py` | `extract_facts`, `RuleBasedExtractor`, protocole `FactExtractor` : texte vers faits validés mot pour mot. |
| `source_card.py` | `source_card`, `SourceDefinition`, `SourceRegistry`, sources par défaut. |
| `records.py` | `assign_confidence` et les trois constructeurs d'enregistrements (`structured_record`, `text_record`, `gap_record`). |
| `ledger_write.py` | `ledger_write` : écriture idempotente au registre, avec contrôle de la fiche source. |
| `models.py` | Modèles Pydantic : `Gap`, `NeedsReport`, `RawItem`, `FetchResult`, `FilterResult`, `Fact`, `SourceCard`, `EnrichmentRecord`. |
| `providers.py` | Contrat `Provider`, `ProviderError`, `CachedProvider`, `CachingProvider`, `EdgarProvider`. |

Outils appelés pour chaque besoin (`config.py`, `NEED_TOOLS`) :

| Besoin (`NeedKind`) | Outils |
|---|---|
| `esg` | `fetch_esg` |
| `controversies` | `fetch_esg`, `fetch_news` |
| `fundamentals` | `fetch_fundamentals`, `fetch_filings` |
| `news` | `fetch_news` |
| `etf_holdings` | `fetch_etf_holdings` |

Type de donnée produit par chaque outil (`TOOL_KIND`) :

| Outil | Type (`DataKind`) |
|---|---|
| `fetch_esg` | `esg` |
| `fetch_filings` | `filings` |
| `fetch_fundamentals` | `fundamentals` |
| `fetch_news` | `news` |
| `fetch_etf_holdings` | `etf_holdings` |

## 3. Détection des besoins (`needs.py`, `config.py`)

`detect_needs(profile, request, store, as_of=None, ledger=None, auto_context=True)` renvoie un `NeedsReport` (`as_of`,
`profile_id`, liste de `Gap`). Sans `as_of`, la date est la dernière date du jeu de données (`resolve_as_of`, `quant/windows.py`).
Aucune règle ne dépend d'un persona : les déclencheurs viennent des champs du profil, du texte de la demande et de la classe de l'actif.

**Déclencheurs par le profil.** `_profile_triggers` parcourt `hard_exclusions` et `soft_preferences` et ne retient que les critères
de `kind == "theme"`. La valeur, mise en minuscules, déclenche :

- `esg` si elle figure dans `ESG_THEMES` : `esg`, `isr`, `investissement_responsable`, `durable` ;
- `controversies` si elle figure dans `CONTROVERSY_THEMES` : `eviter_controverses`, `controverses`, `sans_controverse`.

L'étiquette de déclenchement a la forme `profile:exclusion:theme:<valeur>` ou `profile:preference:theme:<valeur>`.

**Déclencheurs par la demande.** `_request_triggers` applique les expressions régulières de `REQUEST_TRIGGERS` (insensibles à la
casse) à `request.raw_text`. Étiquette : `request:<besoin>`. Les quatre besoins concernés, avec quelques mots reconnus (liste
complète dans `config.py`) :

| Besoin | Mots reconnus (extrait) |
|---|---|
| `esg` | ESG, ISR, durable, sustainab…, responsable, émissions, carbone, carbon |
| `controversies` | controverse, scandale, poursuite, lawsuit, litige, enquête, investigation |
| `fundamentals` | fondamentaux, bénéfice, earnings, revenus, dette, flux de trésorerie, valorisation, P/E, dividende, résultats |
| `news` | nouvelle, actualité, news, récent, dernières semaines, latest |

Le besoin `etf_holdings` n'a pas de mot déclencheur propre (voir plus bas).

**Classes d'actifs applicables** (`APPLICABLE_CLASSES`, champ `asset_class` de l'univers) :

| Besoin | Classes |
|---|---|
| `esg` | `equity`, `etf_equity` |
| `controversies` | `equity` (pour un FNB, voir `etf_holdings`) |
| `fundamentals` | `equity` |
| `news` | `equity`, `etf_equity`, `etf_bond`, `etf_commodity`, `crypto` |
| `etf_holdings` | `etf_equity`, `etf_bond`, `etf_commodity` |

**Construction des lacunes.** Pour chaque symbole de `request.tickers` (sans doublon) et chaque besoin déclenché, `add` crée un `Gap`,
sauf si le registre contient déjà une entrée `enrichment` de statut `ok` pour le même besoin et le même symbole (`_covered`). Un
besoin déjà comblé ne revient donc pas. Une lacune de statut « lacune non comblée » ne compte pas comme comblée.

| Champ de `Gap` | Sens |
|---|---|
| `need_id` | `need_` + 12 caractères du SHA-256 de `besoin\|symbole\|as_of`. |
| `kind` | Besoin : `esg`, `controversies`, `fundamentals`, `news`, `etf_holdings`. |
| `ticker` | Symbole de l'actif. |
| `triggered_by` | Étiquettes de déclenchement, triées et sans doublon. |
| `fillable` | Vrai si l'actif est dans l'univers et si la classe est applicable. |
| `reason` | Si `fillable` est faux : `hors_univers` (symbole absent du magasin) ou `non_applicable_<classe>`. |
| `tools` | Outils à appeler (`NEED_TOOLS`), vide si la lacune n'est pas comblable. |

`NeedsReport.fillable` et `NeedsReport.unfillable` filtrent les lacunes. Une lacune non comblable n'écrit rien au registre : `run`
la reporte dans `EnrichmentReport.unfillable` (exemple 1).

**ESG par transparence.** Si l'actif est un FNB (`asset_class` commençant par `etf`) et qu'un déclencheur `esg` ou `controversies`
existe, un besoin `etf_holdings` est ajouté, avec les étiquettes de ces deux déclencheurs.

**Besoin de contexte automatique `AUTO_FUNDAMENTALS`.** Constante `"auto:action_americaine"`. Elle est ajoutée quand toutes ces
conditions sont réunies :

- `auto_context` est vrai ;
- la demande ne déclenche pas déjà `fundamentals` ;
- l'actif est dans l'univers, de classe `equity`, et sa devise est `USD` ;
- le besoin n'est pas déjà comblé au registre.

Le `Gap` créé a pour seul outil `fetch_fundamentals` : la liste des dépôts n'est pas demandée. Aucune lacune n'est ajoutée pour un titre
coté en CAD ni pour la crypto (exemple de test : `test_a_us_stock_gets_context_fundamentals_without_any_keyword`).

Deux filtres complètent ce besoin :

- `EnrichmentAgent.detect_needs` retire de la liste tout besoin dont `triggered_by == [AUTO_FUNDAMENTALS]` si aucun de ses outils n'a
  de fournisseur branché. Une source absente ne produit donc ni lacune ni bruit pour un besoin que personne n'a demandé.
- L'orchestrateur appelle `detect_needs(..., auto_context=False)` pour la relance des alternatives (section 10) : seuls les actifs
  demandés reçoivent des fondamentaux de contexte.

## 4. La chaîne par lacune (`agent.py`)

```
fetch_*  ->  point_in_time_filter  ->  extract_facts (texte seulement)  ->  source_card  ->  ledger_write
```

`EnrichmentAgent(providers, sources=None, extractor=None)` reçoit un dictionnaire `outil -> fournisseur`. `run(needs, ledger,
as_of=None)` accepte un `NeedsReport` ou une liste de `Gap`. Sans `as_of` (ni `NeedsReport` pour le fournir), il lève `ValueError`.

Pour chaque lacune :

1. Une lacune non comblable est notée dans `report.unfillable` (« `besoin:symbole : raison` ») et passe son tour.
2. Pour chaque outil de `gap.tools` (`_run_tool`) :
   - Aucun fournisseur pour l'outil : lacune `no_provider`.
   - Un même couple (outil, symbole) n'est collecté qu'une fois par exécution. Les besoins `esg` et `controversies` d'un symbole
     partagent donc un seul appel à `fetch_esg` (exemple 4).
   - Le résultat porte un code d'erreur : lacune avec ce code et le message de la source.
   - `point_in_time_filter` garde les éléments dont `date_publication` et `date_observation` sont inférieures ou égales à `as_of`.
     Le compte des éléments écartés va dans `excluded_after_as_of` (compté à la première collecte seulement).
   - Aucun élément gardé : lacune `no_data_before_as_of` si des éléments ont été écartés, sinon `no_data`.
   - Chaque élément gardé est écrit par `_write_item`. Une donnée structurée donne un enregistrement via `structured_record`. Un
     texte passe par `extract_facts`, puis par `text_record` s'il reste au moins un fait. Un élément peut donner les deux.
3. Pour le besoin `controversies`, une donnée `esg` structurée n'est écrite que si elle contient la clé `controversies`
   (`answers_need`). Sinon elle ne répond pas au besoin (test `test_esg_without_controversies_does_not_answer_controversies`).
4. Si aucun outil de la lacune n'a rien produit (`produced == 0`), chaque outil resté « silencieux » (réponse sans erreur, sans lacune
   déjà écrite, sans rien d'utilisable) reçoit une lacune `no_matching_data`, dont le message précise que ce n'est pas la preuve
   d'une absence de problème. Si un autre outil de la même lacune a produit une donnée, aucune lacune `no_matching_data` n'est écrite.

Le sujet passé à `extract_facts` est le besoin (`esg`, `controversies`, `fundamentals`) ; pour `news` et `etf_holdings` il est vide, et
l'extracteur garde alors les premières phrases (`NEWS_FALLBACK_SENTENCES`).

`ledger_write` est idempotent : un enregistrement déjà présent n'est ni réécrit ni compté dans le rapport.

**`EnrichmentReport`** (comptes, pas jugements) :

| Champ | Contenu |
|---|---|
| `as_of` | Date d'analyse utilisée. |
| `written` | `record_id` des données `ok` écrites. |
| `gaps_written` | `record_id` des lacunes non comblées écrites. |
| `excluded_after_as_of` | Éléments postérieurs à `as_of`, écartés par le filtre. |
| `context_unavailable` | Besoins de contexte automatiques restés sans réponse, au format `type:symbole (code)`. Aucune lacune n'est écrite. |
| `rejected_facts` | Faits candidats rejetés par `extract_facts`. |
| `unfillable` | Besoins hors périmètre, avec leur raison. |
| `degraded` (propriété) | Vrai si `gaps_written` n'est pas vide : l'orchestrateur doit le dire. |

**Besoin de contexte silencieux.** Pendant une lacune dont `triggered_by == [AUTO_FUNDAMENTALS]`, l'agent est en mode silencieux
(`_quiet`). `_gap` n'écrit alors rien au registre : il ajoute `type:symbole (code)` à `context_unavailable`, et `degraded` reste faux.
Personne n'a demandé ces données : leur absence ne change ni le verdict ni l'avertissement (exemple 6, cas 1). Le même échec pour un besoin
explicite (« résultats » dans la demande) écrit une lacune par outil et rend `degraded` vrai (exemple 6, cas 2).

**En cas d'échec.** Une panne de fournisseur ne lève jamais d'exception hors de l'agent : `fetchers._fetch` attrape `ProviderError`
(code du fournisseur) et toute autre exception (`provider_failure`). Un fournisseur qui renvoie des éléments d'un autre type ou
d'un autre symbole que ceux demandés voit son lot entier rejeté (`provider_mismatch`).

## 5. Les modèles (`models.py`)

Tous héritent de `StrictModel` (`quant/models.py`).

| Modèle | Rôle |
|---|---|
| `RawItem` | Ce qu'un fournisseur renvoie, non fiable : `ticker`, `kind`, `source_id`, `url`, `title`, `date_publication`, `date_observation`, `retrieved_on` (date réelle de collecte, fournie par le fournisseur : l'agent n'a pas d'horloge), `structured`, `text`, `methodology`, `from_cache`. |
| `FetchResult` | Résultat d'un outil : éléments, ou `error_code` et `error_message`. |
| `FilterResult` | `kept` et `excluded` du filtre temporel. |
| `Fact` | Fait extrait d'un texte : `statement`, `excerpt`, `url`, `date_publication`, `date_observation`. |
| `RejectedFact`, `ExtractionResult` | Candidats refusés avec leur raison, et faits acceptés. |
| `SourceCard` | Fiche source (section 7). |
| `EnrichmentRecord` | Donnée enrichie ou lacune, telle qu'écrite au registre. |
| `Gap`, `NeedsReport` | Besoins (section 3). |

**Statuts.** `EnrichmentRecord.status` vaut `ok` ou `lacune_non_comblee` (constante `LACUNE`). `label` vaut `fait` (défaut) ou
`signal_qualitatif` (faits tirés d'un texte, posé par `text_record`).

**Règles de validation.**

`RawItem` : contient au moins `structured` (non nul) ou un `text` non vide.

`Fact` : énoncé, extrait et URL non vides ; `date_observation` ne suit pas `date_publication`.

`EnrichmentRecord`, statut `lacune_non_comblee` :

- `reason_code` obligatoire ;
- ni `value`, ni `facts`, ni `confidence`, ni `usable_for_calculation`.

`EnrichmentRecord`, statut `ok` :

- `date_publication`, `date_observation`, `confidence` et `source_card_id` obligatoires ;
- `date_publication` inférieure ou égale à `as_of`, `date_observation` inférieure ou égale à `date_publication` ;
- exactement l'un de `value` (structuré) ou `facts` (texte) ;
- chaque fait publié au plus tard à `as_of` ;
- niveau C jamais utilisable pour un calcul ;
- une donnée `news` est de niveau C et non utilisable pour un calcul ;
- un enregistrement avec des faits est de niveau C.

Un enregistrement invalide lève `ValidationError` à sa construction : il n'atteint pas le registre (exemple 8).

Identifiants : `make_record_id` donne `en_` + 16 caractères du SHA-256 du JSON canonique des éléments qui définissent l'enregistrement
(type, besoin, symbole, source, URL, date de publication, valeur ou énoncés des faits, `as_of`). Pour une lacune : statut, type,
besoin, symbole, code et `as_of`.

## 6. Niveaux de confiance A, B, C (`records.py`)

`assign_confidence(kind, source_class, structured, has_methodology)` déduit le niveau de la source et de la forme de la donnée,
jamais d'un jugement sur le contenu. Elle renvoie le niveau et une liste de `Notice`.

| Condition (évaluée dans cet ordre) | Niveau | `Notice` |
|---|---|---|
| `kind == "news"` ou donnée non structurée (texte) | C | aucune |
| Source `official` | A | aucune |
| Source `third_party` avec méthodologie | B | aucune |
| Source `third_party` sans méthodologie | C | `METHODOLOGY_MISSING` |
| Source `unknown` | C | `SOURCE_UNDOCUMENTED` |

Une donnée structurée d'une source `official` est donc A même si elle porte aussi une méthodologie.

`usable_for_calculation` est vrai si et seulement si le niveau est A ou B, pour une donnée structurée (`structured_record`). Pour un
texte (`text_record`), le niveau est toujours C, `usable_for_calculation` est faux et `label` vaut `signal_qualitatif`. Le modèle
`EnrichmentRecord` refuse les combinaisons contraires. Les limites d'un enregistrement reprennent la méthodologie du fournisseur et
signalent qu'une donnée vient du cache.

Qui lit ces niveaux : la gouvernance sépare A ou B des signaux C (`governance/checks.py`, ligne 426 : `ab = lambda r: r["confidence"] in ("A", "B")`) ;
voir `Agents.md` 2.2 et 2.4 pour les règles de blocage.

## 7. Fiches source (`source_card.py`)

`source_card(source_id, retrieved_on, *, processing, extra_limits, methodology, registry)` construit une `SourceCard`. Son
`card_id` est `sc_` + 16 caractères du SHA-256 du contenu : mêmes entrées, même identifiant.

**Sources enregistrées par défaut** (`DEFAULT_SOURCES`) :

| `source_id` | Classe | Particularités |
|---|---|---|
| `sec_edgar` | `official` | SEC EDGAR. Conditions : `User-Agent` identifiant l'appelant et limite de débit. Limites : seules les métadonnées des dépôts sont lues ; la date de dépôt fait foi pour la publication, la date d'observation est la fin de période déclarée. |
| `sedar_plus` | `official` | Fiche seulement : aucun fournisseur SEDAR+ n'existe dans le code, et la fiche dit qu'aucune API publique documentée n'est connue. |

**Source inconnue.** Pour un `source_id` absent du registre, la fiche porte `source_class="unknown"`, `origin_url="inconnue"`,
`reuse_terms` égal à `UNDOCUMENTED` (« non documentées : à vérifier avant toute réutilisation ») et une limite qui nomme la source.
Ses données restent plafonnées au niveau C avec la `Notice` `SOURCE_UNDOCUMENTED` (exemple 5).

**Ajouter une source** : créer une `SourceDefinition` (`source_id`, `name`, `origin_url`, `source_class`, `terms_url`, `reuse_terms`,
`limits`) et la passer dans un `SourceRegistry` à `EnrichmentAgent(..., sources=registre)`. Attention : `SourceRegistry(definitions)`
remplace la liste par défaut ; pour la conserver, écrire `SourceRegistry(DEFAULT_SOURCES + [ma_source])` (exemple 4).

Chaque fiche porte aussi `storage_policy` (`STORAGE_POLICY`) et la liste `processing` écrite par l'agent (`EnrichmentAgent._card`) :
collecte via la source, filtre temporel appliqué, nombre de faits extraits (pour un texte), mention « Servi depuis le cache local »
quand `from_cache` est vrai.

## 8. Extraction de faits (`facts.py`)

Le texte est la zone la plus risquée. Un extracteur *propose* des candidats ; `extract_facts` *dispose* et ne garde que ceux qui passent
toutes les vérifications. `RuleBasedExtractor` est le repli déterministe, sans modèle de langage. Un autre extracteur (par exemple
un modèle de langage) peut être passé à `EnrichmentAgent(extractor=...)` : il doit respecter le protocole `FactExtractor`, c'est-à-dire une
méthode `propose(text, topic)` qui renvoie des `Candidate(statement, excerpt)`.

**Contrôles sur le texte entier** (un échec refuse tout et renvoie un rejet « (texte entier) ») :

- URL absente, ou ne commençant pas par `http://` ou `https://` ;
- date de publication absente ;
- publication postérieure à `as_of` (si `as_of` est fourni) ;
- observation postérieure à la publication (à défaut de `date_observation`, la date de publication est utilisée) ;
- texte vide ;
- extracteur en échec (toute exception).

**Contrôles par candidat** (`_check`, textes normalisés par réduction des espaces) :

| Règle | Valeur (`config.py`) |
|---|---|
| Énoncé non vide, extrait non vide | |
| Longueur maximale de l'énoncé | `MAX_STATEMENT_CHARS = 300` caractères |
| Longueur maximale de l'extrait | `MAX_EXCERPT_CHARS = 300` caractères |
| L'extrait figure mot pour mot dans le texte source | |
| Chaque nombre de l'énoncé figure dans l'extrait (séparateurs décimaux et de milliers ignorés) | |
| Pas de doublon (même énoncé et même extrait) : ignoré sans rejet | |
| Maximum de faits par texte | `MAX_FACTS_PER_TEXT = 10`, les suivants sont rejetés |

`RuleBasedExtractor` découpe le texte en phrases (celles de moins de 15 caractères sont ignorées), retient celles qui contiennent un
mot-clé du sujet (`TOPIC_KEYWORDS`, pour `esg`, `controversies` et `fundamentals`) et reprend la phrase telle quelle comme énoncé et
extrait, tronqués à 300 caractères. Sans sujet reconnu, il garde les `NEWS_FALLBACK_SENTENCES = 3` premières phrases.

Les rejets sont renvoyés dans `ExtractionResult.rejected` avec leur raison et comptés dans `EnrichmentReport.rejected_facts`. Le texte
source n'est pas conservé : seuls l'énoncé, l'extrait, l'URL et les dates le sont (exemple 5).

## 9. Fournisseurs (`providers.py`)

**Contrat `Provider`** : un attribut `source_id` et une méthode `fetch(kind, ticker, as_of) -> List[RawItem]`. En cas d'échec, elle
lève `ProviderError(code, message)`. Le code est stable et se retrouve dans la lacune (section 12).

**`CachedProvider(directory, source_id="cache")`.** Lit `<dossier>/<type>/<symbole>.json` (liste d'éléments bruts) et marque chaque
élément `from_cache=True`. Le symbole doit correspondre à `^[A-Za-z0-9._-]+$`, ce qui empêche de sortir du dossier. Codes :
`invalid_ticker`, `not_cached` (fichier absent), `cache_corrupt` (JSON ou validation illisibles). `has(kind, ticker)` teste la présence du fichier ;
`store(kind, ticker, items)` écrit le fichier (JSON trié, sans le champ `from_cache`). Aucun appel réseau.

**`CachingProvider(live, cache)`.** Appelle le fournisseur en direct et mémorise le résultat dans le cache. Si l'appel lève
`ProviderError`, il tente le cache. Si le cache échoue aussi, l'erreur d'origine du direct remonte. Le filtre temporel s'applique de
la même façon aux éléments du cache : un cache plus récent que `as_of` ne fuit pas dans la simulation (exemple 8).

**`EdgarProvider(user_agent, retrieved_on, http_get=None)`.** SEC EDGAR, source officielle (niveau A). `source_id = "sec_edgar"`.
`retrieved_on` est la date de collecte consignée sur les éléments. `http_get(url, headers) -> bytes` est injectable : les tests et
les exemples y mettent une fonction simulée ; par défaut, `_urllib_get` (délai d'attente de 20 secondes).

Ordre des contrôles de `fetch` :

1. Type autre que `filings` ou `fundamentals` : `unsupported`.
2. `user_agent` vide ou absent : `auth_required`. Rien n'est envoyé.
3. Symbole finissant par `.TO` ou `.V` (titres canadiens, SEDAR+ sans API publique documentée) : `unsupported`.
4. Symbole contenant `-` (par exemple `BTC-USD`) : `unsupported`.
5. Recherche du CIK dans la liste des déclarants (`company_tickers.json`) ; symbole absent : `not_found`.

**Dépôts (`filings`).** Lit la liste des dépôts récents de la société. Ne retient que les formulaires de `FILING_FORMS` : `10-K`, `10-Q`,
`8-K`, `20-F`, `40-F`, `6-K`. Ignore les dépôts postérieurs à `as_of`, s'arrête à `MAX_FILINGS = 20`. Chaque dépôt devient un `RawItem` avec
`structured` = formulaire, numéro d'accès, date de dépôt, date de rapport, CIK. `date_publication` est la date de dépôt ;
`date_observation` est la date du rapport, ou la date de dépôt si elle manque, plafonnée à la date de publication. Seules les
métadonnées sont lues, pas le texte des dépôts.

**Fondamentaux (`fundamentals`, `_fundamentals`).** Lit les données XBRL « companyfacts » (taxonomie `us-gaap`, unité `USD`).

1. *Rapport de référence.* Parmi les faits des formulaires annuels (`ANNUAL_FORMS` : `10-K`, `10-K/A`, `20-F`, `20-F/A`, `40-F`,
   `40-F/A`), déposés au plus tard à `as_of`, pour tous les concepts suivis, l'agent prend le plus récent par date de dépôt puis par
   date de fin. Aucun fait : `ProviderError("no_data_before_as_of")`. Tous les chiffres viennent de ce seul rapport (même numéro d'accès).
2. *Concepts par indicateur.* Pour chaque indicateur, le premier concept (par ordre de préférence) qui a une valeur dans ce rapport est retenu.
   Le concept retenu est consigné dans `xbrl_concepts`.

   | Indicateur | Type de période | Concepts XBRL, par ordre de préférence |
   |---|---|---|
   | `revenue` | durée | `RevenueFromContractWithCustomerExcludingAssessedTax`, `Revenues`, `SalesRevenueNet`, `RevenuesNetOfInterestExpense` |
   | `net_income` | durée | `NetIncomeLoss`, `ProfitLoss` |
   | `operating_cash_flow` | durée | `NetCashProvidedByUsedInOperatingActivities` |
   | `cash` | instant | `CashAndCashEquivalentsAtCarryingValue`, `CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents` |
   | `long_term_debt` | instant | `LongTermDebtNoncurrent`, `LongTermDebt` |
   | `equity` | instant | `StockholdersEquity`, `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` |
   | `total_assets` | instant | `Assets` |

3. *Durée d'exercice.* Pour un indicateur de type « durée » (flux), le fait doit avoir une date de début et une durée comprise entre
   `ANNUAL_DAYS = (330, 400)` jours (exercices de 52 ou 53 semaines). Pour un « instant » (solde), le fait n'a pas de date de début.
   Parmi les faits restants, l'exercice du rapport (date de fin la plus récente) est pris, pas les années comparatives.
4. *Omission sans estimation.* Un indicateur sans fait valide est absent de `metrics`. Si aucun indicateur n'est trouvé :
   `ProviderError("no_matching_data")`.
5. *Résultat.* Un seul `RawItem` : `date_publication` = date de dépôt, `date_observation` = fin de période (plafonnée à la date de dépôt), URL du dossier du
   dépôt sur sec.gov. `structured` contient `form`, `accession_number`, `filing_date`, `period_end`, `cik`, `currency` (`USD`),
   `metrics` et `xbrl_concepts`. Les montants sont repris tels quels, sans conversion.

**Débit et identification.** Avant chaque requête, `_json` attend pour qu'au moins `_MIN_INTERVAL_S = 0.15` seconde sépare deux requêtes
de la même instance (la SEC limite à 10 requêtes par seconde, le commentaire du code). L'en-tête envoyé est `User-Agent` (valeur fournie) et
`Accept: application/json`. Réponses HTTP 403 et 429 : `rate_limited`. Autres erreurs HTTP, réseau, délai dépassé ou réponse JSON
illisible : `unavailable`.

**Liste des déclarants lue une fois.** `company_tickers.json` est téléchargé à la première recherche de CIK et gardé dans l'instance. Comme
`AlphaForge.analyze` construit un nouvel agent à chaque analyse (et donc un nouvel `EdgarProvider`), la liste est lue une fois par analyse
(test : `test_the_list_of_filers_is_downloaded_once_per_provider`).

**Titres canadiens et crypto.** Non pris en charge : `.TO` et `.V` (SEDAR+), tout symbole avec un tiret. Le résultat est une lacune
`unsupported`, jamais un contournement.

## 10. Branchement dans l'application (`app/pipeline.py`, `orchestrator/orchestrator.py`)

**`build_enrichment_agent(sec_user_agent=None, cache_dir=None, today=None, snapshot_dir=None)`** construit l'agent avec les sources
réellement disponibles. Valeurs par défaut : cache `alphaforge/app/cache/enrichment` (`ENRICHMENT_CACHE_DIR`, ignoré par git), instantané
`alphaforge_starter/enrichment_snapshot` (`ENRICHMENT_SNAPSHOT_DIR`), `today` = `date.today()`.

Pour `fetch_filings` et `fetch_fundamentals`, la chaîne est essayée dans l'ordre par `_FirstAvailable`, qui rend le premier résultat
obtenu, ou l'erreur du premier fournisseur essayé si tous échouent :

1. **SEC en direct** (`CachingProvider` autour d'un `EdgarProvider`), seulement si une identification non vide est fournie. Le résultat est mémorisé dans le cache, qui sert de repli si l'appel échoue.
2. **Cache local**, seulement si aucune identification n'est fournie et si le dossier `<cache>/<type>/` existe.
3. **Instantané versionné**, si le dossier `<instantané>/<type>/` existe.

L'identification vient de l'argument `sec_user_agent` ou, à défaut, de la variable `ALPHAFORGE_SEC_USER_AGENT` (lue dans `AlphaForge.analyze`).

Pour `fetch_esg`, `fetch_news` et `fetch_etf_holdings`, aucune source en direct n'est branchée : le cache local est servi seulement si
`<cache>/<type>/` existe. Sinon l'outil n'a pas de fournisseur et le besoin devient une lacune `no_provider`.

L'agent est créé sans `SourceRegistry` personnalisé : seules les sources par défaut (`sec_edgar`, `sedar_plus`) sont documentées. Une donnée
tierce déposée dans le cache avec un autre `source_id` serait donc plafonnée au niveau C (section 7).

**`enrichment_coverage(sec_user_agent, cache_dir, snapshot_dir)`** renvoie, pour chaque outil, ce qui serait branché :

| Valeur | Condition |
|---|---|
| `live` | Outil SEC (`fetch_filings`, `fetch_fundamentals`) et identification non vide. |
| `cache` | Le dossier `<cache>/<type>/` existe. |
| `snapshot` | Outil SEC et le dossier `<instantané>/<type>/` existe. |
| `absent` | Aucun des cas précédents : ce qui n'est pas branché se voit. |

`live` décrit la configuration, pas le succès des appels (exemple 9). L'interface affiche ce résultat tel quel.

**Appel par l'orchestrateur (`Orchestrator._enrich`).**

- Si l'analyste n'expose pas de `store`, une note `enrichment_failed` est écrite et l'analyse continue.
- `detect_needs` est appelé avec le profil, la demande, le magasin, `as_of` et le registre. Sans lacune, `_enrich` retourne sans rien écrire.
- Une note `needs_detected` (ou `needs_relaunched`) consigne le nombre de besoins comblables et hors périmètre, avec la liste des `Gap`.
- Sans besoin comblable, `run` n'est pas appelé.
- `run(needs, ledger, needs.as_of)` s'exécute dans un `try` : toute exception écrit une note `enrichment_failed` (« le noyau poursuit sans lui »).
- Une note `enrichment_done` consigne les comptes (`written`, `gaps`, `excluded_after_as_of`, `rejected_facts`, `degraded`) et, si présents,
  les besoins de contexte indisponibles.
- `_enrich` retourne vrai si le registre a reçu des données ou des lacunes nouvelles.

**Relance unique (alternatives).** Après la boucle de retour de la gouvernance, l'orchestrateur relève les symboles des preuves
`compute_metrics` qui ne figuraient pas dans la demande. S'il y en a, il appelle `_enrich` une seule fois avec `relaunch=True`
(donc `auto_context=False`). Si des données ou lacunes ont été écrites, la gouvernance réévalue.

## 11. L'instantané hors ligne (`alphaforge_starter/enrichment_snapshot/`)

Détail de la source, des conditions et du traitement : `SOURCE.md` du dossier (non répété ici).

**Contenu** (`manifest.json`) : données arrêtées au `as_of` du 2026-11-12 (dernière date du jeu de départ), collectées le 2026-11-15, pour
les 10 actions américaines de l'univers. Fichiers `filings/<SYMBOLE>.json` (10 fichiers) et `fundamentals/<SYMBOLE>.json` (9 fichiers).

| Actif | Dépôts | Fondamentaux |
|---|---|---|
| AAPL, MSFT, NVDA, GOOGL, AMZN, TSLA, JPM, JNJ, PG | 20 | 1 rapport annuel |
| XOM | 8 | aucun |

**Lacune XOM.** À la génération, la SEC associait le symbole à un nouveau déclarant (CIK 2115436) sans rapport annuel : le manifeste
note `lacune : no_data_before_as_of` et aucun fichier `fundamentals/XOM.json` n'est écrit. À l'exécution sur l'instantané seul, la
lacune écrite est `not_cached` (fichier absent), pas `no_data_before_as_of` (exemple 3). Les 8 dépôts de XOM restent disponibles.
Les codes diffèrent parce qu'à l'exécution c'est `CachedProvider` qui répond, pas l'API.

**Date d'analyse antérieure.** Les éléments de l'instantané portent leur date de publication d'origine. Pour un `as_of` antérieur,
le filtre temporel écarte ceux qui sont postérieurs : la donnée devient une lacune `no_data_before_as_of` (exemple 3).

**Régénérer.** `ALPHAFORGE_SEC_USER_AGENT="Equipe exemple contact@exemple.org" python3 alphaforge_starter/build_sec_snapshot.py`
(commande à lancer avec une vraie identification ; **elle appelle la SEC et n'a pas été exécutée pour ce document**). Le script
(`build_sec_snapshot.py`) :

- s'arrête si la variable est vide ;
- prend comme `as_of` la dernière date du jeu de données (`store.last_date`) ;
- retient les actifs `equity` en `USD` ;
- appelle `EdgarProvider.fetch` pour `filings` et `fundamentals`, stocke avec `CachedProvider.store` ;
- consigne une erreur de fournisseur dans le manifeste sous la forme `lacune : <code>`, sans la combler ;
- n'écrit l'identification dans aucun fichier (test : `test_the_snapshot_never_contains_the_sec_identification`).

## 12. Codes de lacune (`reason_code`)

Un `reason_code` est soit posé par l'agent, soit repris du `ProviderError` d'un fournisseur, soit produit par `fetchers._fetch`.

| Code | Origine | Sens |
|---|---|---|
| `no_provider` | `agent.py` | Aucun fournisseur branché pour l'outil. |
| `no_data` | `agent.py` | La source a répondu sans aucun élément. |
| `no_data_before_as_of` | `agent.py`, `EdgarProvider` | Tous les éléments sont postérieurs à `as_of` ; pour les fondamentaux EDGAR, aucun rapport annuel déposé avant `as_of`. |
| `no_matching_data` | `agent.py`, `EdgarProvider` | L'outil a répondu mais rien d'utilisable n'en est sorti (aucun fait validé, donnée ESG sans controverses pour le besoin controverses) ; pour EDGAR, le rapport ne porte aucun indicateur suivi. |
| `provider_failure` | `fetchers.py` | Exception imprévue du fournisseur (type et message consignés). |
| `provider_mismatch` | `fetchers.py` | Le fournisseur a renvoyé des éléments d'un autre type ou symbole : lot rejeté. |
| `unavailable` | `EdgarProvider` | Erreur HTTP autre que 403 et 429, réseau inaccessible, délai dépassé, réponse illisible. |
| `rate_limited` | `EdgarProvider` | HTTP 403 ou 429. |
| `auth_required` | `EdgarProvider` | Aucune identification de l'appelant. |
| `unsupported` | `EdgarProvider` | Type de donnée non fourni par EDGAR, titre canadien (`.TO`, `.V`) ou symbole avec tiret. |
| `not_found` | `EdgarProvider` | Symbole absent de la liste des déclarants EDGAR. |
| `invalid_ticker` | `CachedProvider` | Symbole refusé par le cache (caractères hors liste). |
| `not_cached` | `CachedProvider` | Aucun fichier en cache pour ce type et ce symbole. |
| `cache_corrupt` | `CachedProvider` | Fichier de cache illisible ou invalide. |

Ne pas confondre avec `Gap.reason` (`hors_univers`, `non_applicable_<classe>`), qui décrit un besoin hors périmètre et n'écrit aucun
enregistrement. L'interface traduit une partie de ces codes (`app/labels.py` : `no_provider`, `no_data`, `no_data_before_as_of`,
`no_matching_data`, `rate_limited`, `auth_required`, `unsupported`, `invalid_ticker`).

## 13. Exemples vérifiés

Chaque exemple a été exécuté avec `cd /workshop/datathon2026 && ALPHAFORGE_LLM=0 python3 -W ignore -c "..."` (Python 3.9.25), sans réseau. Les
exemples 4, 5, 6, 7 et 8 utilisent des fournisseurs ou des réponses factices ; les exemples 2 et 3 lisent l'instantané versionné.

### Exemple 1. Besoins détectés pour le profil Mateo

Thèmes du profil (ESG, éviter les controverses) et mot « résultats » de la demande. BTC-USD est hors périmètre.

```python
from alphaforge.enrichment import detect_needs
from alphaforge.orchestrator import RuleBasedParser, load_profile
from alphaforge.quant import DataStore

store = DataStore.from_directory()
assets = store.assets.to_dict("records")
profile = load_profile("mateo")
request = RuleBasedParser().parse("Évalue NVDA et ses résultats, avec BTC-USD", assets)
report = detect_needs(profile, request, store)
print("as_of :", report.as_of)
for g in report.gaps:
    print(g.kind, g.ticker, g.fillable, g.reason, g.triggered_by, g.tools)
```

```text
as_of : 2026-11-12
esg NVDA True None ['profile:preference:theme:esg'] ['fetch_esg']
controversies NVDA True None ['profile:preference:theme:eviter_controverses'] ['fetch_esg', 'fetch_news']
fundamentals NVDA True None ['request:fundamentals'] ['fetch_fundamentals', 'fetch_filings']
esg BTC-USD False non_applicable_crypto ['profile:preference:theme:esg'] []
controversies BTC-USD False non_applicable_crypto ['profile:preference:theme:eviter_controverses'] []
fundamentals BTC-USD False non_applicable_crypto ['request:fundamentals'] []
```

### Exemple 2. Chaîne complète sur l'instantané (AAPL)

Aucune identification SEC : les deux outils lisent l'instantané. Le profil de Robert n'a pas de thème ; « résultats » déclenche les
fondamentaux, donc aussi la liste des dépôts (20 dépôts et 1 rapport annuel : 21 enregistrements).

```python
from pathlib import Path
from alphaforge.enrichment import CachedProvider, EnrichmentAgent
from alphaforge.orchestrator import Ledger, RuleBasedParser, load_profile
from alphaforge.quant import DataStore

store = DataStore.from_directory()
snapshot = CachedProvider(Path("alphaforge_starter/enrichment_snapshot"), source_id="sec_edgar")
agent = EnrichmentAgent({"fetch_fundamentals": snapshot, "fetch_filings": snapshot})
request = RuleBasedParser().parse("Évalue AAPL et ses résultats", store.assets.to_dict("records"))
ledger = Ledger()
needs = agent.detect_needs(load_profile("robert"), request, store, ledger=ledger)
report = agent.run(needs, ledger)
print("écrites :", len(report.written), "| lacunes :", len(report.gaps_written), "| écartées :", report.excluded_after_as_of)
for e in ledger.entries("enrichment")[:2]:
    p = e.payload
    print(p["kind"], p["confidence"], p["usable_for_calculation"], p["date_publication"], p.get("value", {}).get("metrics"))
print("fiches source :", [e.payload["source_id"] for e in ledger.entries("source_card")])
```

```text
écrites : 21 | lacunes : 0 | écartées : 0
fundamentals A True 2026-11-03 {'cash': 36305000000, 'equity': 112093000000, 'long_term_debt': 68208000000, 'net_income': 132175000000, 'operating_cash_flow': 145097000000, 'revenue': 478834000000, 'total_assets': 404548000000}
filings A True 2026-11-09 None
fiches source : ['sec_edgar']
```

### Exemple 3. Lacune XOM et date d'analyse antérieure

Première boucle : `as_of` par défaut. Deuxième : le 2024-01-02, où les 8 dépôts de XOM sont postérieurs.

```python
from datetime import date
from pathlib import Path
from alphaforge.enrichment import CachedProvider, EnrichmentAgent
from alphaforge.orchestrator import Ledger, RuleBasedParser, load_profile
from alphaforge.quant import DataStore

store = DataStore.from_directory()
snapshot = CachedProvider(Path("alphaforge_starter/enrichment_snapshot"), source_id="sec_edgar")
agent = EnrichmentAgent({"fetch_fundamentals": snapshot, "fetch_filings": snapshot})
request = RuleBasedParser().parse("Évalue XOM et ses résultats", store.assets.to_dict("records"))
for as_of in (None, date(2024, 1, 2)):
    ledger = Ledger()
    needs = agent.detect_needs(load_profile("robert"), request, store, as_of, ledger)
    report = agent.run(needs, ledger)
    print("as_of", needs.as_of, "| écrites", len(report.written), "| lacunes", len(report.gaps_written), "| écartées", report.excluded_after_as_of)
    for e in ledger.entries("enrichment"):
        if e.payload["status"] == "lacune_non_comblee":
            print("  ", e.payload["kind"], e.payload["reason_code"], "-", e.payload["message"])
```

```text
as_of 2026-11-12 | écrites 8 | lacunes 1 | écartées 0
   fundamentals not_cached - Aucune donnée en cache pour fundamentals/XOM.
as_of 2024-01-02 | écrites 0 | lacunes 2 | écartées 8
   fundamentals not_cached - Aucune donnée en cache pour fundamentals/XOM.
   filings no_data_before_as_of - 8 élément(s) trouvé(s), tous publiés après as_of (2024-01-02) : écartés.
```

### Exemple 4. Fournisseurs factices : niveau B, niveau C, publication postérieure

Une cote ESG d'un fournisseur fictif enregistré (méthodologie fournie : niveau B), et deux nouvelles dont une est postérieure à `as_of`
(écartée). La nouvelle conservée ne donne qu'un fait, de niveau C. La donnée ESG répond aux besoins `esg` et `controversies` (une seule collecte).

```python
from datetime import date
from alphaforge.enrichment import EnrichmentAgent, RawItem, SourceDefinition, SourceRegistry
from alphaforge.enrichment.source_card import DEFAULT_SOURCES
from alphaforge.orchestrator import Ledger, RuleBasedParser, load_profile
from alphaforge.quant import DataStore


class Factice:
    """Fournisseur factice : une cote ESG avec méthodologie, ou des nouvelles dont une est postérieure à as_of."""
    def __init__(self, source_id, items):
        self.source_id, self._items = source_id, items
    def fetch(self, kind, ticker, as_of):
        return [i for i in self._items if i.kind == kind and i.ticker == ticker]


common = dict(ticker="NVDA", retrieved_on=date(2026, 11, 13))
esg = RawItem(**common, kind="esg", source_id="fournisseur_fictif", url="https://exemple.org/esg/NVDA",
              date_publication=date(2026, 3, 1), date_observation=date(2025, 12, 31),
              structured={"rating": "BBB", "controversies": ["enquête X"]}, methodology="Méthodologie fictive 2025")
texte = "NVIDIA a publié ses résultats. Une enquête antitrust a été ouverte en 2026. Le titre a fluctué."
nouvelles = [
    RawItem(**common, kind="news", source_id="agence_fictive", url="https://exemple.org/n/1", date_publication=date(2026, 5, 2),
            date_observation=date(2026, 5, 2), text=texte),
    RawItem(**common, kind="news", source_id="agence_fictive", url="https://exemple.org/n/2", date_publication=date(2026, 12, 1),
            date_observation=date(2026, 12, 1), text="Une enquête postérieure."),
]
registre = SourceRegistry(DEFAULT_SOURCES + [SourceDefinition(
    source_id="fournisseur_fictif", name="Fournisseur ESG fictif", origin_url="https://exemple.org", source_class="third_party",
    reuse_terms="Licence fictive")])
agent = EnrichmentAgent({"fetch_esg": Factice("fournisseur_fictif", [esg]), "fetch_news": Factice("agence_fictive", nouvelles)},
                        sources=registre)
store = DataStore.from_directory()
profil = load_profile("mateo")
demande = RuleBasedParser().parse("Évalue NVDA", store.assets.to_dict("records"))
ledger = Ledger()
rapport = agent.run(agent.detect_needs(profil, demande, store, ledger=ledger, auto_context=False), ledger)
print("écrites", len(rapport.written), "| lacunes", len(rapport.gaps_written), "| écartées", rapport.excluded_after_as_of,
      "| faits rejetés", rapport.rejected_facts, "| dégradé", rapport.degraded)
for e in ledger.entries("enrichment"):
    p = e.payload
    if p["status"] == "ok":
        print(p["need"], p["kind"], "niveau", p["confidence"], "calcul", p["usable_for_calculation"], p["label"],
              [f["statement"] for f in p["facts"]] or p["value"])
    else:
        print(p["need"], p["kind"], "LACUNE", p["reason_code"])
```

```text
écrites 3 | lacunes 0 | écartées 1 | faits rejetés 0 | dégradé False
esg esg niveau B calcul True fait {'rating': 'BBB', 'controversies': ['enquête X']}
controversies esg niveau B calcul True fait {'rating': 'BBB', 'controversies': ['enquête X']}
controversies news niveau C calcul False signal_qualitatif ['Une enquête antitrust a été ouverte en 2026.']
```

### Exemple 5. Extraction de faits, niveaux de confiance et fiche d'une source inconnue

```python
from datetime import date
from alphaforge.enrichment import extract_facts, source_card
from alphaforge.enrichment.facts import Candidate
from alphaforge.enrichment.records import assign_confidence

texte = "Le revenu a atteint 5 milliards de dollars. Une enquête a été ouverte en 2025. Phrase neutre sans intérêt ici."
base = dict(url="https://exemple.org/a", date_publication=date(2025, 5, 1), as_of=date(2025, 6, 30))

print("règles :", [f.statement for f in extract_facts(texte, topic="controversies", **base).facts])


class Propose:
    def propose(self, text, topic):
        return [
            Candidate(statement="Revenu de 7 milliards", excerpt="Le revenu a atteint 5 milliards de dollars."),
            Candidate(statement="Fraude massive", excerpt="Une fraude massive a été révélée."),
            Candidate(statement="Revenu de 5 milliards", excerpt="Le revenu a atteint 5 milliards de dollars."),
        ]


res = extract_facts(texte, extractor=Propose(), **base)
print("acceptés :", [f.statement for f in res.facts])
for r in res.rejected:
    print("rejeté :", r.statement, "->", r.reason)
print("sans URL :", extract_facts(texte, **{**base, "url": ""}).rejected[0].reason)
print("après as_of :", extract_facts(texte, **{**base, "date_publication": date(2025, 8, 1)}).rejected[0].reason)

for args in [("filings", "official", True, False), ("esg", "third_party", True, True), ("esg", "third_party", True, False),
             ("esg", "unknown", True, True), ("news", "official", True, True), ("esg", "third_party", False, True)]:
    niveau, notices = assign_confidence(*args)
    print(args, "->", niveau, [n.code for n in notices])

carte = source_card("blog_inconnu", date(2026, 11, 13))
print(carte.source_class, "|", carte.reuse_terms, "|", carte.limits)
print(source_card("sec_edgar", date(2026, 11, 13)).source_class)
```

```text
règles : ['Une enquête a été ouverte en 2025.']
acceptés : ['Revenu de 5 milliards']
rejeté : Revenu de 7 milliards -> nombre(s) de l'énoncé absent(s) de l'extrait : ['7']
rejeté : Fraude massive -> extrait introuvable mot pour mot dans le texte source
sans URL : URL absente ou invalide : un fait sans trace est rejeté
après as_of : publié le 2025-08-01, après as_of (2025-06-30)
('filings', 'official', True, False) -> A []
('esg', 'third_party', True, True) -> B []
('esg', 'third_party', True, False) -> C ['METHODOLOGY_MISSING']
('esg', 'unknown', True, True) -> C ['SOURCE_UNDOCUMENTED']
('news', 'official', True, True) -> C []
('esg', 'third_party', False, True) -> C []
unknown | non documentées : à vérifier avant toute réutilisation | ['Source « blog_inconnu » non répertoriée : origine et conditions de réutilisation non vérifiées.']
official
```

### Exemple 6. Échec d'une source : contexte silencieux ou lacune explicite

```python
from alphaforge.enrichment import EnrichmentAgent, ProviderError
from alphaforge.orchestrator import Ledger, RuleBasedParser, load_profile
from alphaforge.quant import DataStore


class EnPanne:
    source_id = "sec_edgar"
    def fetch(self, kind, ticker, as_of):
        raise ProviderError("unavailable", "source en panne")


store = DataStore.from_directory()
robert = load_profile("robert")
agent = EnrichmentAgent({"fetch_fundamentals": EnPanne(), "fetch_filings": EnPanne()})
parse = lambda texte: RuleBasedParser().parse(texte, store.assets.to_dict("records"))

# 1. Besoin de contexte automatique : l'échec est compté, aucune lacune n'est écrite.
ledger = Ledger()
rapport = agent.run(agent.detect_needs(robert, parse("Évalue NVDA"), store, ledger=ledger), ledger)
print("contexte :", rapport.context_unavailable, "| écrit :", len(ledger.entries("enrichment")), "| dégradé :", rapport.degraded)

# 2. Besoin explicite (mot « résultats ») : une lacune par outil, et l'état dégradé.
ledger = Ledger()
rapport = agent.run(agent.detect_needs(robert, parse("Évalue NVDA et ses résultats"), store, ledger=ledger), ledger)
print("explicite :", [(e.payload["tool"], e.payload["reason_code"]) for e in ledger.entries("enrichment")], "| dégradé :", rapport.degraded)

# 3. Sans fournisseur branché, le besoin automatique disparaît de detect_needs.
vide = EnrichmentAgent({})
print("sans source :", vide.detect_needs(robert, parse("Évalue NVDA"), store).gaps)
# 4. Relance des alternatives : pas de besoin automatique.
print("relance :", agent.detect_needs(robert, parse("Évalue NVDA"), store, auto_context=False).gaps)
```

```text
contexte : ['fundamentals:NVDA (unavailable)'] | écrit : 0 | dégradé : False
explicite : [('fetch_fundamentals', 'unavailable'), ('fetch_filings', 'unavailable')] | dégradé : True
sans source : []
relance : []
```

### Exemple 7. `EdgarProvider` avec une réponse simulée

La fonction `http_get` remplace le réseau. L'exercice comparatif (900) est écarté. Le bénéfice net négatif (-50) est repris tel quel. Les indicateurs absents du
rapport (trésorerie, dette, flux de trésorerie, capitaux propres) sont omis. Les contrôles finaux montrent les codes de lacune
renvoyés sans appel réseau.

```python
import json
from datetime import date
from alphaforge.enrichment import EdgarProvider, fetch_fundamentals, fetch_filings


def fait(val, end, filed, start=None, form="10-K", accn="0000000000-26-000001"):
    d = {"val": val, "end": end, "filed": filed, "form": form, "accn": accn}
    if start:
        d["start"] = start
    return d


COMPANYFACTS = {"facts": {"us-gaap": {
    "Revenues": {"units": {"USD": [fait(1000, "2025-12-31", "2026-02-20", start="2025-01-01"),
                                   fait(900, "2024-12-31", "2026-02-20", start="2024-01-01")]}},   # exercice précédent, même rapport
    "NetIncomeLoss": {"units": {"USD": [fait(-50, "2025-12-31", "2026-02-20", start="2025-01-01")]}},
    "Assets": {"units": {"USD": [fait(5000, "2025-12-31", "2026-02-20")]}},
}}}


def http_get(url, headers):
    assert headers["User-Agent"] == "Equipe exemple contact@exemple.org"
    if "company_tickers" in url:
        return json.dumps({"0": {"cik_str": 1234567, "ticker": "EXMP"}}).encode()
    return json.dumps(COMPANYFACTS).encode()


edgar = EdgarProvider("Equipe exemple contact@exemple.org", date(2026, 11, 13), http_get)
res = fetch_fundamentals(edgar, "EXMP", date(2026, 6, 30))
v = res.items[0].structured
print(v["metrics"], v["xbrl_concepts"], v["period_end"])
print(res.items[0].url)
print("avant le dépôt :", fetch_fundamentals(edgar, "EXMP", date(2026, 1, 1)).error_code)
print("titre canadien :", fetch_fundamentals(edgar, "RY.TO", date(2026, 6, 30)).error_code)
print("crypto         :", fetch_filings(edgar, "BTC-USD", date(2026, 6, 30)).error_code)
print("inconnu        :", fetch_filings(edgar, "ZZZZ", date(2026, 6, 30)).error_code)
anonyme = EdgarProvider(None, date(2026, 11, 13), http_get)
print("sans identité  :", fetch_filings(anonyme, "EXMP", date(2026, 6, 30)).error_code)
```

```text
{'revenue': 1000, 'net_income': -50, 'total_assets': 5000} {'revenue': 'Revenues', 'net_income': 'NetIncomeLoss', 'total_assets': 'Assets'} 2025-12-31
https://www.sec.gov/Archives/edgar/data/1234567/000000000026000001/
avant le dépôt : no_data_before_as_of
titre canadien : unsupported
crypto         : unsupported
inconnu        : not_found
sans identité  : auth_required
```

### Exemple 8. Repli sur le cache et refus d'un enregistrement incohérent

```python
import tempfile
from datetime import date
from pydantic import ValidationError
from alphaforge.enrichment import CachedProvider, CachingProvider, EnrichmentRecord, ProviderError, RawItem

item = RawItem(ticker="NVDA", kind="esg", source_id="fournisseur_fictif", url="https://exemple.org/esg/NVDA",
               date_publication=date(2026, 3, 1), date_observation=date(2025, 12, 31), retrieved_on=date(2026, 11, 13),
               structured={"rating": "BBB"}, methodology="Méthodologie fictive")


class Direct:
    source_id = "fournisseur_fictif"
    def __init__(self, en_panne):
        self.en_panne = en_panne
    def fetch(self, kind, ticker, as_of):
        if self.en_panne:
            raise ProviderError("rate_limited", "trop d'appels")
        return [item]


with tempfile.TemporaryDirectory() as dossier:
    cache = CachedProvider(dossier)
    print("direct  from_cache =", CachingProvider(Direct(False), cache).fetch("esg", "NVDA", date(2026, 6, 30))[0].from_cache)
    print("panne   from_cache =", CachingProvider(Direct(True), cache).fetch("esg", "NVDA", date(2026, 6, 30))[0].from_cache)
    try:
        CachingProvider(Direct(True), cache).fetch("esg", "AAPL", date(2026, 6, 30))
    except ProviderError as e:
        print("ni direct ni cache :", e.code)
    try:
        cache.fetch("esg", "../secret", date(2026, 6, 30))
    except ProviderError as e:
        print("symbole dangereux :", e.code)

try:
    EnrichmentRecord(record_id="x", status="ok", kind="esg", need="esg", ticker="NVDA", as_of=date(2026, 6, 30), tool="fetch_esg",
                     tool_version="1.0.0", date_publication=date(2026, 7, 1), date_observation=date(2026, 7, 1), confidence="B",
                     usable_for_calculation=True, source_card_id="sc_x", value={"rating": "BBB"})
except ValidationError as e:
    print("refusé :", e.errors()[0]["msg"])
```

```text
direct  from_cache = False
panne   from_cache = True
ni direct ni cache : rate_limited
symbole dangereux : invalid_ticker
refusé : Value error, Publié le 2026-07-01, après as_of (2026-06-30) : refusé.
```

### Exemple 9. Couverture des sources

Dossier de cache temporaire vide : l'instantané est affiché, puis le mode direct, puis un cache de nouvelles.

```python
import tempfile
from pathlib import Path
from alphaforge.app.pipeline import enrichment_coverage

with tempfile.TemporaryDirectory() as vide:
    cache = Path(vide)
    print("sans identification :", enrichment_coverage(None, cache_dir=cache))
    print("avec identification :", enrichment_coverage("Equipe exemple contact@exemple.org", cache_dir=cache))
    (cache / "news").mkdir()
    print("cache de nouvelles  :", enrichment_coverage(None, cache_dir=cache)["fetch_news"])
```

```text
sans identification : {'fetch_filings': 'snapshot', 'fetch_esg': 'absent', 'fetch_fundamentals': 'snapshot', 'fetch_news': 'absent', 'fetch_etf_holdings': 'absent'}
avec identification : {'fetch_filings': 'live', 'fetch_esg': 'absent', 'fetch_fundamentals': 'live', 'fetch_news': 'absent', 'fetch_etf_holdings': 'absent'}
cache de nouvelles  : cache
```

## 14. Tests associés

Commande : `python3 -W ignore -m pytest tests/enrichment -q`, puis `python3 -W ignore -m pytest tests/integration -q` (un dossier à la fois, voir `tests/README.md`).
Aucun test n'appelle la SEC ni le réseau.

| Fichier | Contenu |
|---|---|
| `tests/enrichment/conftest.py` | Univers synthétique de 5 actifs (RY.TO, NVDA, ESGV, AGG, BTC-USD), profils (`mateo`, profil neutre), registre avec la source fictive `esg_vendor`. |
| `tests/enrichment/helpers_enr.py` | `FakeProvider`, `failing`, `esg_item`, `news_item`, `raw`. |
| `tests/enrichment/test_enr_tools.py` | `detect_needs` (thèmes, mots de la demande, contexte automatique, classes), filtre temporel, validation de `EnrichmentRecord`, `extract_facts`, `source_card`, niveaux de confiance, codes d'erreur des `fetch_*`, `CachedProvider`, `CachingProvider`, `EdgarProvider` (dépôts). |
| `tests/enrichment/test_enr_agent.py` | Chaîne complète, rien de postérieur à `as_of`, lacunes (panne, absence de fournisseur, aucune donnée correspondante), plafonds à C, idempotence, une collecte par couple (outil, symbole), `run` sans `as_of`, `ledger_write`. |
| `tests/enrichment/test_enr_fundamentals.py` | Fondamentaux SEC simulés : rapport annuel retenu, indicateur omis, `as_of` ultérieur, absence de rapport, requêtes non prises en charge sans appel réseau, liste des déclarants lue une fois. |
| `tests/integration/conftest.py` | Univers NVDA, AMD, INTC, RY.TO, `Fake`, `failing`, fabrique de profil prudent, fixture `run` (orchestrateur, gouvernance et explicabilité branchés). |
| `tests/integration/test_enrichment_integration.py` | L'agent avec les quatre autres, règle par règle (`Agents.md` 2.2 et 2.4) : blocage selon le niveau, niveau C sans blocage, publication postérieure, panne de source, panne de l'agent, relance unique, ordre des entrées du registre, fondamentaux jusqu'à l'explication vérifiée. |
| `tests/app/test_app_aws.py` | Instantané servi sans réseau ni identification (`test_offline_snapshot_serves_sec_data_without_network_or_identification`), aucune identification dans l'instantané. |
| `tests/app/test_app_streamlit.py` | Cas d'application : demande sans besoin, thèmes sans source branchée, mode sans enrichissement, fondamentaux de contexte depuis l'instantané. Fichier non relu en détail pour ce document. |

## 15. Limites connues

- **Sources absentes.** Aucune source en direct pour les cotes ESG, les controverses documentées, les nouvelles et la composition des FNB. Ces
  besoins deviennent des lacunes `no_provider` tant qu'aucune donnée n'est déposée dans `<cache>/esg/`, `<cache>/news/` ou
  `<cache>/etf_holdings/`. Une donnée déposée dans le cache avec un `source_id` autre que `sec_edgar` serait de niveau C (source non répertoriée).
- **SEDAR+.** Une fiche source existe, mais aucun fournisseur : les titres canadiens (`.TO`, `.V`) donnent une lacune `unsupported` pour les
  dépôts et les fondamentaux. Sans mot-clé dans la demande, les fondamentaux de contexte ne sont d'ailleurs jamais demandés pour eux (devise CAD).
- **Actions américaines seulement.** Les fondamentaux de contexte se limitent à `equity` en `USD`. Un symbole avec tiret (par exemple une
  action à plusieurs catégories écrite avec un tiret) est refusé par `EdgarProvider`.
- **Fondamentaux annuels.** Un seul rapport annuel par appel, jamais de trimestriel, aucun ratio calculé, aucune conversion de devise (unité `USD`
  seulement). Un exercice hors de 330 à 400 jours n'est pas retenu pour un flux.
- **L'analyste n'utilise pas les fondamentaux.** Aucun outil de l'analyste, de l'orchestrateur ni règle de la gouvernance ne les lit (recherche du mot `fundamentals`
  dans `quant/`, `orchestrator/` et `governance/` : aucune occurrence). Ils sont affichés comme contexte et composés par l'explicabilité
  (`explainability/compose.py`). `Agents.md` 2.4 et le `README.md` le disent aussi.
- **`structured` vide.** Un fournisseur qui renvoie un élément avec `structured={}` passe la validation de `RawItem` (le champ n'est pas nul),
  mais `EnrichmentRecord` refuse un enregistrement `ok` sans `value`. `run` lève alors `ValidationError`. Comportement observé par un essai
  local, sans test dédié parmi ceux lus ; dans l'application, l'orchestrateur l'attrape et écrit `enrichment_failed`.
- **Cache.** `CachedProvider` ne vérifie ni l'âge ni l'origine d'un fichier : il est cru dans la limite du filtre temporel et de la validation de `RawItem`.
- **Débit.** L'attente de 0,15 s est propre à une instance d'`EdgarProvider`. Plusieurs processus qui interrogent la SEC en parallèle ne partagent pas ce délai.
- **Non vérifié ici.** Les appels réels à la SEC (aucun appel réseau n'a été fait) : le format exact des réponses actuelles de l'API, les codes HTTP réellement renvoyés en cas de limite de débit, et la régénération de l'instantané.
