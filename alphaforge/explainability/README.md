# Agent d'explicabilité

Cet agent écrit l'explication finale d'AlphaForge AI (une simulation d'aide à la décision d'investissement, Python 3.9) à partir du seul registre de preuves, puis vérifie chaque énoncé avant de l'afficher. Un énoncé qui ne passe pas la vérification est retiré.

Ce document décrit le code du dossier `alphaforge/explainability/`. Pour la place de l'agent dans l'architecture (les 4 agents, le registre, les règles de communication), voir [`Agents.md`](../../Agents.md) (section 1.2, « Agent 4 : Explicabilité », et l'état de l'implémentation en fin de document). Pour le projet dans son ensemble et la liste des contrôles du vérificateur vue de l'extérieur, voir le [`README.md`](../../README.md) racine (section « Vérification de l'explication »). Ces points ne sont pas répétés ici.

## Table des matières

1. [Rôle et interdits](#1-rôle-et-interdits)
2. [Modules](#2-modules)
3. [La boucle rédaction, vérification, rédaction](#3-la-boucle-rédaction-vérification-rédaction)
4. [Lecture du registre](#4-lecture-du-registre)
5. [Rédaction](#5-rédaction)
6. [Limites et incertitudes](#6-limites-et-incertitudes)
7. [Étiquetage](#7-étiquetage)
8. [Formatage des nombres](#8-formatage-des-nombres)
9. [Le vérificateur](#9-le-vérificateur)
10. [Le contrôle de provenance](#10-le-contrôle-de-provenance)
11. [Configuration](#11-configuration)
12. [Exemples vérifiés](#12-exemples-vérifiés)
13. [Tests associés](#13-tests-associés)
14. [Limites connues](#14-limites-connues)

---

## 1. Rôle et interdits

L'explicabilité est le quatrième agent du noyau. Elle reçoit un profil d'investisseur (`InvestorProfile`) et le registre (`Ledger`) rempli par les autres agents, et renvoie une `Explanation` (`models.py`) dont le champ `text` est un document Markdown.

Ce que l'agent ne fait jamais :

- **Aucun calcul de métrique.** Les volatilités, pertes maximales, corrélations, poids et montants viennent des preuves du registre. Le code ne fait que trois choses avec un nombre : le choisir (par exemple la fenêtre de volatilité la plus basse et la plus haute dans `limits.py`, la plus forte contribution au risque dans `compose.py`), l'arrondir (`formatting.py`) et le rattacher à l'entrée qui le contient (`Builder.num`).
- **Aucune mémoire.** `Explainer.explain` ne garde rien entre deux appels. Tout fait passe par `ledger_read` (`ledger_read.py`), qui lit le registre en lecture seule. La seule écriture est une note de fin de passe (voir [section 3](#3-la-boucle-rédaction-vérification-rédaction)).
- **Aucun verdict.** Le statut (`conforme`, `avertissement`, `bloqué`) vient du dernier verdict de la gouvernance. L'agent le restitue, il ne le recalcule pas.
- **Aucune recommandation, prédiction ou promesse.** Les gabarits n'en contiennent pas, et le vérificateur retire tout énoncé qui en contient (contrôles 6 et 7, sections [9](#9-le-vérificateur) et [10](#10-le-contrôle-de-provenance)).
- **Aucune branche par persona.** `compose.py` le dit dans sa docstring : seuls la langue (`profile.language`) et l'expérience (`profile.experience`) changent la forme. Le test `test_same_data_same_facts_whatever_the_profile_name` (`test_expl_run.py`) vérifie que deux identifiants de profil différents donnent le même texte.

L'orchestrateur appelle l'agent à l'étape 6 (`alphaforge/orchestrator/orchestrator.py`, lignes 304 à 310) : `Explainer` est appelable (`Explainer()(profile, ledger) -> str`). Si aucune gouvernance n'est branchée, l'explication n'est pas produite et `explicabilite` reste dans `pending_agents`.

## 2. Modules

| Fichier | Rôle |
|---|---|
| `__init__.py` | Exporte l'API publique : `Explainer`, `ExplainConfig`, `Explanation`, `Figure`, `Statement`, `Limits`, `VerificationReport`, `LedgerView`, `ledger_read`, `compose_explanation`, `render`, `limits_builder`, `label_content`, `claim_verifier`. |
| `explainer.py` | Classe `Explainer` : la boucle rédaction, vérification, rédaction, l'écriture de la note au registre, l'assemblage de l'`Explanation`. |
| `ledger_read.py` | `ledger_read(ledger) -> LedgerView` : vue typée du registre et index des références. |
| `compose.py` | `compose_explanation` (énoncés du corps), `Builder` (chiffres, références, chaînes autorisées), `render` (mise en forme Markdown). |
| `config.py` | `ExplainConfig`, `SECTIONS`, `LABELS`, `TEMPLATES` (gabarits français et anglais). |
| `limits.py` | `limits_builder` : hypothèses, limites, incertitudes, non couvert. |
| `label_content.py` | `label_content`, `label_all` : étiquette « fait », « hypothèse » ou « scénario fictif ». |
| `formatting.py` | `shown_value`, `format_number`, `parse_shown` : même code pour écrire et pour contrôler un nombre. |
| `verifier.py` | `claim_verifier`, `check_statement`, `advice_wording`, `ADVICE_PATTERNS` : les 7 contrôles. |
| `provenance.py` | `Vocabulary`, `provenance_issues` : liste blanche de mots, enchaînements et citations (contrôle 7). |
| `models.py` | Modèles Pydantic stricts (`extra="forbid"`) : `Statement`, `Figure`, `Issue`, `VerificationReport`, `Limits`, `Explanation`. |

Un énoncé (`Statement`) porte : `sid` (identifiant du type `fit.3`), `section`, `text`, `basis` (`evidence`, `assumption` ou `scenario`), `label`, `refs` (identifiants de preuve ou `seq:N`), `figures` (chaque nombre affiché, avec sa valeur, son unité, son texte exact et sa référence), `literals` (chaînes contenant des chiffres, comme `LOW.TO` ou une date) et `note`.

## 3. La boucle rédaction, vérification, rédaction

Le code est dans `Explainer.explain` (`explainer.py`).

1. `ledger_read(ledger)` produit la `LedgerView`.
2. Si `ExplainConfig.provenance_check` est vrai, `Vocabulary.build(TEMPLATES[langue], view.vocab_strings)` construit la liste blanche (voir [section 10](#10-le-contrôle-de-provenance)). Sinon le vocabulaire vaut `None`.
3. Une passe fait, dans l'ordre : `compose_explanation(view, profile, exclude)`, puis `limits_builder(view, profile, config, exclude)`, puis `label_all(corps + limites)`, puis `claim_verifier(view, énoncés, attempts=n, vocabulary=vocab)`.
4. La boucle s'arrête si le rapport est approuvé, ou si rien n'a été retiré, ou si le nombre de passes atteint `max_attempts`. Sinon les identifiants retirés (`report.removed`) sont ajoutés à `exclude` et une nouvelle passe commence.

**Nombre de tentatives.** `ExplainConfig.max_attempts = 2` (`config.py`). Le commentaire du code l'explique : rédaction, vérification, rédaction ; le vérificateur peut rejeter une fois. Le `README.md` racine répète « 2 tentatives au plus ».

**Ce qui est exclu à la deuxième passe.** Les `sid` des énoncés retirés à la passe précédente, par exemple `fit.99` ou `assumptions.2`. Les deux générateurs filtrent sur ce `sid` après avoir numéroté leurs énoncés (`compose_explanation` filtre à la fin de `run()`, `limits_builder` filtre à la fin de sa construction). La numérotation ne bouge donc pas d'une passe à l'autre, et un `sid` désigne le même énoncé aux deux passes.

**Garanties à la sortie.** `claim_verifier` ne renvoie que les énoncés gardés. Même quand la dernière passe retire encore des énoncés, ils ne figurent pas dans le texte final (test `test_attempts_are_bounded`, `test_expl_verifier.py`).

**Rapport final** (`VerificationReport`) :

- `approved = report.approved and not removed_all` : vrai seulement si la dernière passe n'a aucune anomalie et si aucune passe n'a retiré d'énoncé ;
- `attempts` : nombre de passes effectuées ;
- `verified` et `downgraded` : ceux de la dernière passe ;
- `removed` et `issues` : cumulés sur toutes les passes, sans doublon pour `removed`.

Un énoncé simplement rétrogradé (voir section 9) laisse une anomalie `no_evidence` dans le rapport, donc `approved` est faux, mais la boucle s'arrête quand même après cette passe puisque rien n'a été retiré. C'est ce que donne la lecture du code. Aucun test ne couvre ce cas précis.

**Note au registre.** À la fin, l'agent écrit une entrée de type `note`, agent `explicabilite`, avec :

- `code` : `explanation_verified` si le rapport final est approuvé, sinon `explanation_repaired` ;
- `message` : `Explication vérifiée contre le registre.` (la même phrase dans les deux cas) ;
- `removed` et `downgraded` : les listes du rapport final.

Chaque appel à `explain` écrit une note. L'exemple 6 en montre une.

**`Explanation` renvoyée.** `language`, `experience`, `statements` (les énoncés du corps, c'est-à-dire ceux dont la section n'est pas `assumptions`, `limits`, `uncertainties` ou `not_covered`), `limits` (un objet `Limits` qui regroupe les quatre sections de limites), `report`, `text` (produit par `render`).

## 4. Lecture du registre

`ledger_read(ledger)` (dans `ledger_read.py`) parcourt `ledger.entries()` une fois et remplit une `LedgerView` :

| Champ | Contenu |
|---|---|
| `profile`, `profile_seq` | Le profil consigné (`payload["profile"]`) et son numéro. |
| `request`, `request_seq` | Le payload de la demande interprétée. |
| `verdict`, `verdict_seq` | Le dernier verdict valide (`GovernanceVerdict`). Chaque nouveau verdict remplace le précédent, ce qui suit la boucle de retour de la gouvernance. Un verdict qui échoue à la validation Pydantic est ignoré, pas deviné. |
| `facts` | `build_facts(view.evidence)` (`governance/facts.py`) : faits par actif, dont les fenêtres de mesure. |
| `evidence` | Les payloads d'entrées de type `evidence`. |
| `notes` | Couples `(seq, payload)` des entrées de type `note`. |
| `enrichment` | Les payloads de type `enrichment` (données et lacunes de l'agent d'enrichissement). |
| `cards` | `card_id` vers `(seq, fiche source)`, pour les entrées `source_card`. |
| `refs` | Index de toutes les références citables vers leur payload : `seq:N` pour chaque entrée sans exception, l'`evidence_id` de chaque preuve, le `record_id` de chaque enregistrement d'enrichissement. |
| `corpus` | Toutes les chaînes du registre (valeurs et clés de dictionnaire), séparées par des sauts de ligne. Le vérificateur y cherche les chaînes autorisées et les dates. |
| `vocab_strings` | Les mêmes chaînes en liste, **sans** la valeur `raw_text` de l'entrée de type `request`. |

`has_ref(ref)` dit si une référence existe. Les fonctions `numeric_leaves` (tous les nombres d'un payload, booléens exclus), `string_leaves` et `seq_ref` sont réutilisées par le vérificateur et la rédaction.

**Pourquoi `vocab_strings` exclut le texte libre de la demande.** `vocab_strings` sert à construire le vocabulaire autorisé du contrôle de provenance. Le texte que l'utilisateur a tapé n'est pas une source de confiance : s'il écrivait « vous devriez acheter », ces mots deviendraient autorisés. Le `corpus` complet garde ce texte, car le vérificateur l'utilise seulement pour confirmer qu'une chaîne ou une date existe dans le registre. Le test `test_the_request_text_never_enters_the_allowed_vocabulary` (`test_expl_provenance.py`) couvre cette règle, et l'exemple 9 la montre.

Les entrées de type `plan` et `step` ne remplissent aucun champ dédié. Elles sont indexées par `seq:N` et leurs chaînes entrent dans `corpus` et `vocab_strings`.

## 5. Rédaction

### 5.1 Sections et ordre

`compose_explanation(view, profile, exclude)` renvoie les énoncés du corps. `config.SECTIONS` fixe l'ordre : `summary`, `data`, `fit`, `alternatives`, `scope`. Un tri stable à la fin de `_Composer.run` garde l'ordre d'écriture à l'intérieur d'une section. Le titre de chaque section vient de `TEMPLATES[langue]["titles"]`.

| Section | Contenu (méthode de `_Composer`) |
|---|---|
| `summary` | `summary()` : phrase d'introduction (débutant seulement), statut du verdict, demande examinée, profil appliqué, limites dérivées du profil, et la mention « limites resserrées d'un niveau » quand `constraints.adjustments` n'est pas vide. |
| `data` | Pour chaque actif du verdict : `data()` (une phrase par fenêtre mesurée), `sensitivity()` (sensibilité à la période). Puis `correlation()`, `portfolio()`, `enrichment()`. |
| `fit` | Pour chaque actif : « statut » puis une phrase par raison de gouvernance (`assessment`, `reason`). Puis les raisons globales. |
| `alternatives` | Pour chaque alternative : statut, mesures (`data()`), raisons sans celles de gravité `info`. Aucune donnée enrichie pour les alternatives. |
| `scope` | Refus de périmètre de la gouvernance, sauf si leur texte contient un chiffre (ils sont sautés, car un chiffre non rattaché serait retiré). Plus la phrase `scenario` quand l'intention de la demande est `build_scenario`. |

Sans verdict dans le registre, `run()` écrit seulement la section `summary` (phrase `no_verdict`, qui dit que l'explication se limite aux données brutes) et les mesures `data` de chaque actif connu.

Les sections de limites (`assumptions`, `limits`, `uncertainties`, `not_covered`) viennent de `limits_builder` (section 6). `render` les range sous le titre `limits_group`.

### 5.2 Gabarits français et anglais

`TEMPLATES` (`config.py`) contient deux dictionnaires, `fr` et `en`, avec exactement les mêmes clés de premier niveau (65) et les mêmes codes de raison (29 codes plus `_generic`). La langue est `profile.language` (`fr` ou `en`, `fr` par défaut dans `orchestrator/profile.py`). Le fichier ne contient aucun chiffre : le docstring de `config.py` l'affirme et les nombres viennent toujours du registre. `LABELS` donne les étiquettes affichées (« Fait », « Hypothèse », « Scénario fictif » en français ; « Fact », « Assumption », « Fictional scenario » en anglais).

Deux conséquences à connaître :

- Les chaînes qui viennent du registre ne sont pas traduites. Dans le texte anglais de l'exemple 3 on lit « basis 252 jours de bourse par an » et la fenêtre `"complet"`, car ce sont des valeurs de preuve.
- En anglais, une raison de gouvernance sans gabarit reçoit toujours la phrase `_generic`. En français, elle reçoit son message brut quand il ne contient aucun chiffre (voir 5.6).

### 5.3 Adaptation au niveau d'expérience

`profile.experience` vaut `debutant` (défaut), `intermediaire` ou `avance`. Voici tout ce qui change, relevé dans le code.

| Élément | Débutant | Intermédiaire | Avancé |
|---|---|---|---|
| Phrase d'introduction `intro_debutant` (en hypothèse) | oui | non | non |
| Phrase de mesure (`data_debutant`, `data_intermediaire`, `data_avance`) | Phrase complète avec définitions : « c'est sa volatilité : l'ampleur des variations de prix », « (la perte maximale) » | Forme courte : fenêtre, volatilité annualisée, perte maximale | Forme courte plus la base d'annualisation (`basis`) |
| Phrase de sensibilité : explication « une mesure sensible dépend beaucoup de la période » | ajoutée si le verdict de sensibilité est `sensible` | non | non |
| Corrélation : explication de la lecture d'un coefficient (`corr_debutant`) | oui | non | non |
| Scénario de portefeuille : explication du ratio de diversification (`pf_risk_debutant`) | oui | non | non |
| Dépassement de limite : « soit X fois la limite » (`ratio`) | omis | affiché si la mesure `ratio` existe | affiché si la mesure `ratio` existe |
| Fondamentaux d'entreprise (section 5.5) | 3 montants (`FUND_BEGINNER`) | 7 montants (`FUND_ORDER`) | 7 montants (`FUND_ORDER`) |
| Hypothèses de méthode affichées (`ExplainConfig.assumptions_cap`) | au plus 2 | au plus 5 | toutes (`None`) |
| Références `(sources : ...)` sous chaque énoncé (`render`) | non | non | oui |

Le plafond d'hypothèses vient de `ExplainConfig.assumptions_cap` : `{"debutant": 2, "intermediaire": 5, "avance": None}` (`config.py`). Seules les hypothèses sont plafonnées. Les limites ne sont jamais tronquées (commentaire de `config.py`, et section 6). L'exemple 2 montre 2, 5 et 10 hypothèses pour le même registre (10 est le nombre d'hypothèses distinctes de ce registre, pas un plafond).

### 5.4 `Builder` : chiffres, références, chaînes autorisées

`Builder` (`compose.py`) accumule les trois choses qu'un énoncé doit déclarer pour passer le vérificateur :

- `num(value, unit, ref) -> str` formate le nombre avec `format_number`, enregistre une `Figure(value, unit, shown, ref)` et ajoute `ref` aux références. Le texte inséré dans la phrase est `shown`, exactement la chaîne que le vérificateur retrouvera ;
- `ref(*refs)` ajoute des références sans doublon ;
- `lit(s)` renvoie `s` tel quel. Si `s` contient un chiffre (ticker comme `LOW.TO`, fenêtre `2022`, date, code), la chaîne est enregistrée dans `literals`, et le vérificateur contrôlera qu'elle existe dans le registre. Une chaîne sans chiffre n'est pas enregistrée (elle est contrôlée mot à mot par la provenance).

`match_ref(view, ids, value, default)` choisit, parmi les preuves de l'analyste citées par une raison, celle qui contient réellement la valeur (tolérance relative 1e-9). À défaut, la référence est celle du verdict. Les limites chiffrées sont toujours rattachées au verdict (`self.vref`, soit `seq:N`).

### 5.5 Données enrichies

`_Composer.enrichment("data")` lit `view.enrichment`. Il ne traite que les enregistrements de statut `ok`. Pour chacun, la fiche source (`view.cards`) donne le nom de la source (à défaut : « source non documentée »).

- **Dépôts** (`kind == "filings"`) : gabarit `enr_filing`, ou `enr_filing_period` quand une date de clôture existe. Il indique formulaire, date de dépôt, niveau de confiance et source, et précise que seules les métadonnées sont lues.
- **Fondamentaux** (`kind == "fundamentals"`) : gabarit `enr_fundamentals`. Chaque montant passe par `Builder.num(..., "money", record_id)`. Un libellé et son montant forment une seule unité de gabarit (`fund_labels`). Le signe se dit par le libellé et jamais par le chiffre : un montant négatif change la clé (`net_income` devient `net_loss`, `operating_cash_flow` devient `negative_operating_cash_flow`, `equity` devient `negative_equity`). L'ordre de lecture est `FUND_ORDER` = `revenue`, `net_income`, `operating_cash_flow`, `cash`, `long_term_debt`, `equity`, `total_assets`. Un profil débutant ne voit que `FUND_BEGINNER` = `revenue`, `net_income`, `long_term_debt`. Les profils intermédiaire et avancé voient les sept ; une métrique hors de cette liste n'est affichée que si `fund_labels` a un libellé pour elle (dans le code actuel, les dix clés de `fund_labels` sont les sept métriques et leurs trois variantes négatives). Une seule fois par explication, la phrase `enr_fund_note` (en hypothèse) précise que ces chiffres sont en dollars US, décrivent l'exercice passé, ne modifient pas le verdict et ne prédisent rien.
- **Signaux de niveau C** (enregistrements qui portent des `facts`) : au plus `MAX_ENRICHED_FACTS = 3` faits par enregistrement (`compose.py`), chacun dans le gabarit `enr_fact`, qui donne la date de publication, la source et l'extrait cité entre guillemets. Dès qu'un enregistrement `ok` porte des faits, `limits_builder` ajoute à l'explication la note `enr_c_note` : ces signaux viennent de textes, sont à vérifier et ne servent jamais à un calcul ni à bloquer à eux seuls.

Montants en dollars US : `format_number(..., "money", ...)` écrit « 215,9 milliards $ US » en français et « USD 215.9 billion » en anglais (voir section 8 et exemple 3).

La note `enr_c_note` et les conditions de réutilisation des sources sont décrites en section 6. Les chemins dépôts et signaux de niveau C ne sont pas exercés par `tests/explainability/`. Ils le sont par `tests/integration/test_enrichment_integration.py` (assertions sur « Signal qualitatif de niveau C » et « Fondamentaux de NVDA »).

### 5.6 Raisons de gouvernance

`_Composer.reason(section, ticker, r)` transforme une `Reason` de la gouvernance en une phrase. Pour chaque code, le gabarit est `TEMPLATES[langue]["reasons"][code]`, rempli avec les mesures de la raison (`r.measures`). Les références sont celles du verdict plus `r.evidence_ids`.

Codes avec gabarit (29) : `VOLATILITY_ABOVE_LIMIT`, `DRAWDOWN_ABOVE_LIMIT`, `RECOVERY_LONGER_THAN_HORIZON`, `EXCLUDED_ASSET_CLASS`, `EXCLUDED_SECTOR`, `EXCLUDED_TICKER`, `CLASS_NOT_PERMITTED`, `THEME_EXCLUSION_UNVERIFIED`, `THEME_PROXY`, `THEME_DATA_MISSING`, `PREFERENCE_MATCH`, `OUT_OF_UNIVERSE`, `METRICS_UNAVAILABLE`, `HIGH_CORRELATION`, `PORTFOLIO_WEIGHT_ABOVE_CAP`, `PORTFOLIO_CLASS_CAP`, `PORTFOLIO_VOLATILITY_ABOVE_LIMIT`, `PORTFOLIO_DRAWDOWN_ABOVE_LIMIT`, `ENRICHED_DATA`, `ENRICHMENT_GAP`, `EXCLUDED_THEME_CONTROVERSY`, `PREFERENCE_CONTRADICTED`, `THEME_VERIFIED`, `CONTROVERSY_SIGNAL_LEVEL_C`, `ESG_DATA_NO_THRESHOLD`, `AMBIGUOUS_ASSET`, `NO_METRICS`, `NO_ALTERNATIVE`, `EXCLUSION_VALUE_UNKNOWN`.

Règles particulières :

- `TOOL_UNAVAILABLE` n'est pas écrit dans le corps : `limits_builder` le traite en « non couvert ».
- Pour les dépassements de limite, la valeur mesurée est rattachée à la preuve de l'analyste qui la contient (`match_ref`), la limite au verdict.
- `THEME_PROXY` est la seule raison étiquetée « hypothèse » : un substitut déclaré n'est pas une mesure.
- Un code sans gabarit, ou dont la branche de remplissage ne le reconnaît pas, reçoit le message brut `r.message` si la langue est `fr` et si ce message n'a aucun chiffre. Dans tous les autres cas, la phrase `_generic` s'applique (« Point d'attention consigné au registre (code ...) » en français).

### 5.7 Sensibilité, corrélation, portefeuille

- **Sensibilité** (`sensitivity`) : la dernière preuve `stress_window` non refusée pour l'actif. Si son verdict n'est ni `stable` ni `sensible`, ou si l'écart relatif manque, la phrase `sensitivity_undetermined` est écrite. Sinon la phrase donne le nombre de fenêtres, les valeurs basse et haute, l'écart relatif et le seuil. Si le plancher d'écart absolu a joué, la phrase `sensitivity_floor` est ajoutée.
- **Corrélation** (`correlation`) : la dernière preuve `correlation_matrix` non refusée, sur la matrice de référence. Une seule paire donne `corr_one`, plusieurs donnent `corr_many` (la plus forte et la plus faible). La mention `corr_mixed` s'ajoute si les calendriers sont mixtes.
- **Portefeuille** (`portfolio`) : la dernière preuve `portfolio_metrics` non refusée. Trois énoncés « scénario fictif » (principal, concentration, risque), puis un quatrième qui dit d'où viennent les poids : `pf_equal` (en hypothèse) quand les poids sont égaux par défaut, `pf_user` (en fait) quand ils viennent de la demande. L'origine se lit dans `weights_basis` de la preuve. Si ce champ manque, des poids tous égaux sur plus d'une position sont réputés « supposés égaux ». Le test `test_user_weights_that_happen_to_be_equal_stay_user_weights` couvre le cas d'un 50/50 saisi par l'utilisateur.

### 5.8 Mise en forme (`render`)

`render(statements, limits, profile, report)` produit le Markdown : un titre `# ...`, une section `## ...` par section non vide, puis `## Hypothèses, limites et incertitudes` avec une sous-section `###` par groupe non vide, puis une ligne `---` et le pied de page `footer`.

Chaque énoncé est une ligne `- [Étiquette] texte`. Pour un profil avancé, une seconde ligne ajoute `(sources : ref1, ref2)` (avec un espace avant les deux-points en français, sans espace en anglais). Le pied de page dit : nombre d'énoncés contrôlés (`verified` plus nombre de rétrogradés), nombre retirés, nombre rétrogradés en hypothèse.

## 6. Limites et incertitudes

`limits_builder(view, profile, config, exclude)` (`limits.py`) compile quatre sections à partir du registre seul. Il suit le même principe que la rédaction : un énoncé = un texte, des références, des chiffres déclarés.

| Section | Origine | Étiquette |
|---|---|---|
| `assumptions` | Les listes `assumptions` des preuves, texte repris tel quel, un énoncé par texte distinct, dans l'ordre d'apparition. Plafonnées selon l'expérience (`ExplainConfig.assumptions_cap`, section 5.3). | hypothèse |
| `limits` | Les listes `limits` des preuves, reprises telles quelles, **jamais tronquées**. Plus, pour chaque fiche source d'un enregistrement d'enrichissement `ok`, ses `limits` et ses conditions de réutilisation (gabarit `enr_card`). | fait |
| `uncertainties` | Avis (`notices`) des preuves de gravité `warning`, ou de code `DRAWDOWN_NOT_RECOVERED`, sans doublon de message. La fourchette de volatilité selon la fenêtre (gabarit `range`) pour les actifs qui ont au moins deux fenêtres. Les notes `feedback_limit_reached`. La phrase `past` (« Le passé ne prédit pas l'avenir... »). La note `enr_c_note` si des signaux de niveau C existent. | fait, sauf `past` et `enr_c_note` (hypothèse) |
| `not_covered` | Notes `tool_unavailable` ; actifs hors univers (`nc_oou`) ; thèmes sans données ou non vérifiables (`nc_theme`, une fois par actif et par thème) ; lacunes d'enrichissement non comblées (`nc_enrich_gap`, sans doublon par actif, nature et motif) ; notes `enrichment_failed` ; la phrase `sim` (simulation, aucune transaction, aucun conseil, aucune prédiction). | fait, sauf `sim` (hypothèse) |

Précisions :

- La fourchette `range` n'est écrite que si les deux volatilités extrêmes s'affichent différemment après arrondi. Sinon elle serait trompeuse (`test_window_sensitivity_shown_only_when_visible`).
- Les textes repris tels quels qui contiennent un chiffre sont déclarés comme chaînes autorisées (`_verbatim` appelle `Builder.lit`). Le vérificateur contrôle alors qu'ils sont dans le registre.
- Les `sid` sont du type `uncertainties.2`. `exclude` en retire à la fin de la construction.
- Les deux phrases fixes `past` et `sim` sont toujours présentes (test `test_limits_always_include_past_and_simulation`).

## 7. Étiquetage

`label_content(statement)` (`label_content.py`) renvoie une copie de l'énoncé avec le champ `label`, déduit de `basis` :

| `basis` | `label` | Affichage français | Affichage anglais |
|---|---|---|---|
| `evidence` | `fait` | Fait | Fact |
| `assumption` | `hypothese` | Hypothèse | Assumption |
| `scenario` | `scenario_fictif` | Scénario fictif | Fictional scenario |

`label_all(statements)` l'applique à une liste. `Explainer` l'appelle avant la vérification : un énoncé sans étiquette est rejeté (anomalie `unlabeled`). L'étiquette est une propriété de l'origine de l'énoncé, jamais du contenu. Un `fait` reste soumis à une condition de preuve : le vérificateur le rétrograde en hypothèse s'il ne cite rien (section 9).

Origines dans le code : `scenario` pour les trois énoncés de portefeuille et la phrase `scenario` de la section `scope` ; `assumption` pour `intro_debutant`, `no_verdict`, `pf_equal`, `enr_fund_note`, la raison `THEME_PROXY`, les hypothèses de méthode, `past`, `enr_c_note` et `sim` ; `evidence` pour le reste.

## 8. Formatage des nombres

`formatting.py` est partagé par la rédaction (`Builder.num`) et le vérificateur : le même code écrit un nombre et contrôle le texte. `Unit` (`models.py`) vaut `pct`, `years`, `ratio`, `corr` ou `money`.

`shown_value(value, unit)` donne le nombre attendu dans le texte, après arrondi. Le signe n'est jamais conservé : la valeur absolue est utilisée, parce que la phrase dit « perte » ou « limite ».

| Unité | Valeur attendue | Affichage français | Affichage anglais |
|---|---|---|---|
| `pct` | `round(abs(v) * 100, 1)` | `47,7 %` | `47.7%` |
| `corr` | `round(abs(v), 2)` | `0,87` ; `-0,35` | `0.87` ; `-0.35` |
| `money` | `abs(v)` en milliards, une décimale, si `abs(v) >= 1e9` ou si l'arrondi en millions atteint 1000 (`_money_in_billions`) ; sinon en millions, une décimale. Pluriel français à partir de 2 | `1,2 milliard $ US` ; `950,0 millions $ US` | `USD 1.2 billion` ; `USD 950.0 million` |
| `years`, `ratio` | entier s'il est entier, sinon une décimale | `3` ; `3,8` | `3` ; `3.8` |

Règles qui se déduisent du code :

- **Pourcentages** : une décimale toujours, virgule et espace avant `%` en français, point et `%` collé en anglais.
- **Corrélations** : deux décimales. Le signe porte le sens d'une corrélation, donc il est affiché (`-` si la valeur est négative et que la valeur arrondie n'est pas nulle). Une corrélation de `-0.001` s'affiche `0,00`, sans signe.
- **Montants** : toujours en dollars US, jamais de signe (le libellé le porte, voir 5.5). L'échelle est choisie après arrondi : 999,96 millions s'affiche « 1,0 milliard $ US », pas « 1000,0 millions » (test `test_money_scale_is_chosen_after_rounding_and_french_plural_follows_the_number`).
- **Années et ratios** : sans décimale quand la valeur est entière (`3`, pas `3,0`).
- `parse_shown(shown)` renvoie le premier nombre du texte affiché (virgule ou point). Le vérificateur le compare à `shown_value(value, unit)` avec une tolérance de 1e-9.

L'exemple 4 exécute ces règles.

## 9. Le vérificateur

`claim_verifier(view, statements, attempts=1, vocabulary=None)` (`verifier.py`) vérifie chaque énoncé avec `check_statement`. Chaque anomalie est un `Issue(sid, code, message)`. Les 7 contrôles :

| # | Contrôle | Code d'anomalie |
|---|---|---|
| 1 | Toute référence de `refs` existe dans le registre (`view.has_ref`). | `unknown_ref` |
| 2 | Un énoncé « fait » cite au moins une référence valide. | `no_evidence` |
| 3 | Chaque `Figure` : sa référence existe (`unknown_ref`) ; sa valeur figure, en valeur absolue, parmi les nombres du payload cité, avec une tolérance relative de 1e-9 (`figure_mismatch`) ; son texte affiché est l'arrondi de cette valeur selon `shown_value` (`figure_format`) ; son texte affiché apparaît bien dans le texte (`figure_format`). | `unknown_ref`, `figure_mismatch`, `figure_format` |
| 4 | Aucun autre nombre dans le texte. Après retrait des dates ISO, des chaînes déclarées et des chiffres déclarés, il ne reste aucun chiffre. | `unsupported_number` |
| 5 | Chaque chaîne déclarée (`literals`) et chaque date ISO `AAAA-MM-JJ` du texte figure dans `view.corpus`. | `unknown_literal` |
| 6 | Aucune tournure de conseil, de prédiction ou de promesse (liste `ADVICE_PATTERNS`). | `advice_wording` |
| 7 | Provenance : chaque mot, chaque enchaînement et chaque citation vient d'un gabarit ou du registre. Actif seulement si un `Vocabulary` est fourni. | `unsourced_wording` |

Le code `unlabeled` s'ajoute avant ces contrôles : il se produit si `label` vaut `None`. Les codes de `Issue.code` listés dans `models.py` sont : `unknown_ref`, `no_evidence`, `figure_mismatch`, `figure_format`, `unsupported_number`, `unknown_literal`, `unlabeled`, `advice_wording`, `unsourced_wording`.

Détails du contrôle 4 (`_residual`) : les dates ISO sont retirées du texte en premier, pour qu'une fenêtre « 2022 » ne ronge pas `2022-01-03`. Puis les chaînes déclarées, de la plus longue à la plus courte. Puis chaque texte affiché d'une `Figure`, uniquement quand il n'est pas collé à d'autres chiffres (expressions régulières avec garde-fous `(?<![\d.,])` et `(?!\d|[.,]\d)`).

### Ce qui est retiré ou rétrogradé

Règle de `claim_verifier` :

- Toute anomalie autre que `no_evidence` : l'énoncé est **retiré** (son `sid` va dans `removed`).
- `no_evidence` seul : si l'énoncé a des chiffres (une `Figure`, ou un chiffre dans le texte), il est **retiré**. Sinon il est **rétrogradé** : gardé avec `label = "hypothese"` et `note = "downgraded_no_evidence"`, et son `sid` va dans `downgraded`.
- Un énoncé sans anomalie est gardé tel quel.

`VerificationReport.approved` vaut `not issues` : la moindre anomalie, même résolue par une rétrogradation, rend le rapport non approuvé. `verified = len(gardés) - len(rétrogradés)`.

L'exemple 5 exécute neuf cas, un par comportement.

### La liste de mots interdits (contrôle 6)

`ADVICE_PATTERNS` contient **29 motifs** : conseils et ordres français (`achetez`, `vendez`, `investissez`, `vous devriez`, `je vous recommande`...), promesses (`garanti`, `sans risque`, `à coup sûr`), prédictions (`va monter`, `vont baisser`...), formulations de « meilleur choix », puis leurs équivalents anglais (`buy`, `sell`, `you should`, `guaranteed`, `risk-free`, `will rise`, `best choice`...). Le critère est « mots entiers, insensible à la casse » : le motif ne doit être précédé ni suivi d'une lettre, d'un chiffre, d'un `_` ou d'un `-`. Exception écrite dans le code : `buy and hold` (« achat-et-conservation » dans les gabarits) nomme une méthode de simulation et n'est pas signalé.

**Tolérance des citations.** Avant la recherche, le texte entre guillemets (`« ... »`, `“ ... ”` ou `"..."`, regex `_QUOTED`) est retiré. Un mot cité est une **mention**, pas un conseil. Cela permet au refus de périmètre de la gouvernance de citer « achète » (test `test_scope_refusal_for_advice_request_survives`). Les apostrophes simples ne délimitent pas une citation.

Ce que la docstring du module écrit sur la portée : le contrôle 6 repère les formulations explicites et ne détecte pas une recommandation déguisée sans mot interdit. Le contrôle 7 existe pour ce cas.

## 10. Le contrôle de provenance

Le contrôle 7 (`provenance.py`) fait l'inverse du contrôle 6. Il ne cherche pas ce qui est interdit : il exige que tout soit autorisé. Il attrape donc ce que la liste de mots interdits laisse passer : une recommandation déguisée, une faute de frappe, un caractère d'apparence identique (« А » cyrillique à la place de « A »).

### Vocabulaire autorisé

`Vocabulary.build(templates, corpus_strings)` apprend, pour chaque explication :

- toutes les chaînes des gabarits de la langue (`config.TEMPLATES`). Un emplacement `{...}` devient un jeton générique `§`. Les valeurs de gabarit sans emplacement (statuts, intentions) sont aussi citables ;
- toutes les chaînes du registre passées en argument (`LedgerView.vocab_strings`), c'est-à-dire sans le texte libre de la demande : messages de la gouvernance, hypothèses de l'analyste, noms d'actifs.

Il en tire : `words` (mots), `pairs` (paires de mots consécutifs), `before_slot` (mots qui précèdent un emplacement dans un gabarit), `after_slot` (mots qui suivent un emplacement), `quotable` (textes qu'une citation peut reprendre), `atoms` (valeurs d'un seul mot du registre, comme un symbole ou un code) et `corpus`.

Les nombres, les chaînes déclarées (`Statement.literals`) et les textes affichés des chiffres (`Figure.shown`) valent `§` : leur exactitude est contrôlée par les contrôles 3 à 5.

### Règles

`provenance_issues(text, literals, vocab)` renvoie la liste des problèmes (vide si tout est autorisé).

1. **Mots.** Chaque mot hors citation appartient au vocabulaire. Message : « mot hors gabarit et hors registre : 'x' ».
2. **Enchaînements.** Chaque paire de mots consécutifs, dans un même segment entre deux signes de ponctuation (`. , ; : ! ? ( ) [ ] — – / = + × ÷ * ^ < > | →` et le saut de ligne), existe dans un gabarit ou dans le registre (`pairs`). Sont aussi acceptées : une paire dont le premier mot précède un emplacement dans un gabarit (`before_slot`), une paire dont le second mot suit un emplacement (`after_slot`), et une paire avec `§` quand l'autre mot est une valeur d'un seul mot du registre (`atoms`, par exemple « SPY 60 % »). Message : « enchaînement absent des gabarits et du registre : 'a' 'b' ».
3. **Citations.** Chaque citation entre guillemets reprend une chaîne du registre (`vocab.corpus`), une chaîne déclarée, ou un texte de gabarit (`quotable`). Une citation composée comme « sector: Technologie » est acceptée si chacun de ses mots est connu (mot ou atome), jamais si l'un est nouveau. Message : « citation sans source : '« x »' ».
4. **Symboles composés.** `LOW.TO` ou `BTC-USD` (regex `_SYMBOL`) sont traités comme un seul jeton, pour que le point ou le tiret ne coupe pas le segment.
5. **Possessif.** `NVDA's` et `{t}'s` : le possessif devient un jeton à part (`'s`), pour que le gabarit anglais « {t}'s » reste lisible.

Les jetons qui contiennent un chiffre valent `§`. La casse est ignorée (`lower()`).

### Ce qu'il attrape

L'exemple 7 exécute le contrôle sur du texte sans mot de la liste interdite. Aucun de ces textes n'est vu par le contrôle 6 (« mots interdits : [] ») :

- `AAPL est le placement idéal pour vous.` : mots « placement », « idéal », « vous » inconnus ;
- `Vous devriiez acheter AAPL.` (faute de frappe) : « devriiez » et « acheter » inconnus ;
- `Аchetez AAPL.` avec un « А » cyrillique : mot « аchetez » inconnu ;
- `Profil appliqué : « foncez ».` : citation sans source.

Les tests de `test_expl_provenance.py` ajoutent « HIGH progressera sûrement. » et « Ne ratez pas cette occasion sur HIGH. ».

Les phrases produites par les gabarits ne sont jamais rejetées : le test `test_every_template_sentence_passes` essaie 4 demandes dans 2 langues et 3 niveaux d'expérience.

### La limite, telle qu'écrite dans le code

La docstring de `provenance.py` dit : la règle 2 tolère n'importe quel mot du vocabulaire juste après ou juste avant un emplacement ; un assemblage de mots tous autorisés, dans un ordre que les gabarits permettent, n'est pas détecté. Les deux contrôles (6 et 7) restent donc actifs ensemble : la liste de mots interdits « dit », la provenance « bloque ».

Deux constats complémentaires, vérifiés en exécutant le code (exemples 8 et 11) :

- Un énoncé faux composé de mots et d'enchaînements autorisés passe. `AAPL : statut conforme.` est accepté alors que le verdict consigné est `avertissement` : aucun contrôle ne compare un statut au verdict.
- La tolérance de `before_slot` et `after_slot` s'applique aussi entre deux mots ordinaires : `sommet limite` passe parce que « sommet » précède un emplacement dans un gabarit, alors que `limite sommet` est rejeté.

## 11. Configuration

`ExplainConfig` (`config.py`, modèle Pydantic) se passe à `Explainer(config)`. Sans argument, `Explainer()` utilise `ExplainConfig()`.

| Champ | Valeur par défaut | Effet |
|---|---|---|
| `max_attempts` | `2` | Nombre maximal de passes rédaction, vérification. Avec `1`, un énoncé rejeté est quand même absent du texte final, mais il n'y a pas de seconde rédaction. |
| `provenance_check` | `True` | Active le contrôle 7. À `False`, `Explainer` ne construit pas de `Vocabulary` et le vérificateur ne fait que les contrôles 1 à 6. |
| `assumptions_cap` | `{"debutant": 2, "intermediaire": 5, "avance": None}` | Nombre maximal d'hypothèses de méthode affichées selon l'expérience ; `None` pour toutes. Les limites ne sont jamais tronquées. Une expérience absente du dictionnaire donne `None` (`.get`), donc toutes les hypothèses. |

L'exemple 10 les exécute. Il montre aussi qu'avec `provenance_check=False` une phrase déguisée (`AAPL est le placement idéal pour vous.`) reste dans le texte.

## 12. Exemples vérifiés

Chaque bloc a été exécuté avec :

```bash
cd /workshop/datathon2026 && ALPHAFORGE_LLM=0 python3 -W ignore -c "<code du bloc>"
```

`ALPHAFORGE_LLM=0` désactive le client Bedrock de l'application (`alphaforge/app/pipeline.py`, fonction `build_parser`). Les exemples utilisent `AlphaForge.from_directory()` (données du jeu de départ, date d'analyse `2026-11-12` dans cet environnement) et `enrichment=False`, sauf l'exemple 3, qui lit l'instantané SEC versionné. Les valeurs chiffrées dépendent de ces données. Les sorties ci-dessous sont celles de l'exécution, parfois raccourcies (marquées par `...`).

### Exemple 1 : une explication complète, et son rapport

```python
from alphaforge.app.pipeline import AlphaForge
af = AlphaForge.from_directory()
a = af.analyze("Évalue NVDA", "robert", enrichment=False)
lines = a.result.answer.splitlines()
print("\n".join(lines[:12]))
print("...")
print(lines[-1])
r = a.explanation.report
print(r.approved, r.attempts, r.verified, r.removed, r.downgraded, len(r.issues))
```

```text
# Explication AlphaForge (simulation, aucune transaction réelle)
## Résumé
- [Fait] Verdict de la gouvernance : avertissement.
  (sources : seq:29)
- [Fait] Demande examinée : évaluer un actif, actifs : NVDA.
  (sources : seq:2)
- [Fait] Profil appliqué : horizon de 3 à 5 ans, tolérance au risque « faible_modere ».
  (sources : seq:1)
- [Fait] Limites dérivées du profil (niveau « faible_modere ») : volatilité annualisée maximale 15,0 %, perte maximale depuis un sommet 20,0 %.
  (sources : seq:29)
...
Vérification : 56 énoncé(s) contrôlé(s) contre le registre de preuves, 0 retiré(s), 0 rétrogradé(s) en hypothèse.
True 1 56 [] [] 0
```

Robert est un profil avancé : chaque ligne porte ses sources (`seq:29` est le verdict). Le rapport dit : approuvé, 1 tentative, 56 énoncés vérifiés, aucun retiré, aucun rétrogradé, aucune anomalie.

### Exemple 2 : même registre, trois niveaux d'expérience

```python
from alphaforge.app.pipeline import AlphaForge
from alphaforge.explainability import Explainer
af = AlphaForge.from_directory()
a = af.analyze("Évalue NVDA", "robert", enrichment=False)
for exp in ("debutant", "intermediaire", "avance"):
    p = a.profile.model_copy(update={"experience": exp})
    e = Explainer().explain(p, a.result.ledger)
    first = next(s.text for s in e.statements if s.section == "data")
    print(exp, "| hypothèses :", len(e.limits.assumptions), "| limites :", len(e.limits.limits),
          "| sources affichées :", "(sources" in e.text)
    print("   ", first[:150])
```

```text
debutant | hypothèses : 2 | limites : 5 | sources affichées : False
    Sur la période « complet » (du 2021-11-12 au 2026-11-12), NVDA a varié de 51,1 % par an (c'est sa volatilité : l'ampleur des variations de prix) et a 
intermediaire | hypothèses : 5 | limites : 5 | sources affichées : False
    NVDA, fenêtre « complet » (du 2021-11-12 au 2026-11-12) : volatilité annualisée 51,1 %, perte maximale depuis un sommet 63,5 %.
avance | hypothèses : 10 | limites : 5 | sources affichées : True
    NVDA, fenêtre « complet » (du 2021-11-12 au 2026-11-12) : volatilité annualisée 51,1 % (base 252 jours de bourse par an), perte maximale depuis un sommet 63,5 %.
```

Les plafonds 2 et 5 viennent de `assumptions_cap`. Les 5 limites sont identiques pour les trois niveaux. Chaque appel à `explain` écrit une note dans le registre de `a`.

### Exemple 3 : fondamentaux en dollars US, selon l'expérience et la langue

```python
from alphaforge.app.pipeline import AlphaForge
from alphaforge.explainability import Explainer
af = AlphaForge.from_directory()
a = af.analyze("Évalue NVDA", "robert", enrichment=True)
for exp, lang in (("debutant", "fr"), ("avance", "en")):
    p = a.profile.model_copy(update={"experience": exp, "language": lang})
    e = Explainer().explain(p, a.result.ledger)
    s = next(s for s in e.statements if s.text.startswith(("Fondamentaux", "Fundamentals")))
    print(exp, lang, s.label, s.refs)
    print("   ", s.text.split(" : ", 1)[-1] if lang == "fr" else s.text.split("): ", 1)[-1])
    print("    unités des chiffres :", sorted({f.unit for f in s.figures}), "| approuvé :", e.report.approved)
```

```text
debutant fr fait ['en_cb1106d122452fcb']
    chiffre d'affaires 215,9 milliards $ US, bénéfice net 120,1 milliards $ US, dette à long terme 7,5 milliards $ US.
    unités des chiffres : ['money'] | approuvé : True
avance en fait ['en_cb1106d122452fcb']
    revenue USD 215.9 billion, net income USD 120.1 billion, operating cash flow USD 102.7 billion, cash USD 10.6 billion, long-term debt USD 7.5 billion, shareholders' equity USD 157.3 billion, total assets USD 206.8 billion.
    unités des chiffres : ['money'] | approuvé : True
```

Le débutant voit 3 montants, l'avancé 7. La référence est un `record_id` d'enrichissement (`en_...`), pas un `seq:N`.

### Exemple 4 : règles d'arrondi et d'affichage

```python
from alphaforge.explainability.formatting import format_number, shown_value
cas = [(0.4768, "pct"), (-0.2, "pct"), (3.0, "years"), (3.8, "years"), (4.123, "ratio"), (2.0, "ratio"),
       (-0.3512, "corr"), (0.8734, "corr"), (1.234e9, "money"), (9.5e8, "money"), (-5.0e6, "money")]
for v, u in cas:
    print(f"{v!r:>8} {u:5} fr={format_number(v, u, 'fr'):22} en={format_number(v, u, 'en'):18} attendu={shown_value(v, u)}")
```

```text
  0.4768 pct   fr=47,7 %                 en=47.7%              attendu=47.7
    -0.2 pct   fr=20,0 %                 en=20.0%              attendu=20.0
     3.0 years fr=3                      en=3                  attendu=3.0
     3.8 years fr=3,8                    en=3.8                attendu=3.8
   4.123 ratio fr=4,1                    en=4.1                attendu=4.1
     2.0 ratio fr=2                      en=2                  attendu=2.0
 -0.3512 corr  fr=-0,35                  en=-0.35              attendu=0.35
  0.8734 corr  fr=0,87                   en=0.87               attendu=0.87
1234000000.0 money fr=1,2 milliard $ US      en=USD 1.2 billion    attendu=1.2
950000000.0 money fr=950,0 millions $ US    en=USD 950.0 million  attendu=950.0
-5000000.0 money fr=5,0 millions $ US      en=USD 5.0 million    attendu=5.0
```

### Exemple 5 : le vérificateur, un cas par comportement

```python
from alphaforge.app.pipeline import AlphaForge
from alphaforge.explainability import Figure, Statement, claim_verifier, label_content, ledger_read
a = AlphaForge.from_directory().analyze("Évalue AAPL", "robert", enrichment=False)
view = ledger_read(a.result.ledger)
ev = next(e for e in view.evidence if e["tool"] == "compute_metrics")
eid, vol = ev["evidence_id"], ev["value"]["base"]["annualized_volatility"]
good = Figure(value=vol, unit="pct", shown="27,8 %", ref=eid)
def S(sid, text, refs=(), figs=()):
    return label_content(Statement(sid=sid, section="data", text=text, refs=list(refs), figures=list(figs)))
cas = [
    S("ok", "Volatilité 27,8 %.", [eid], [good]),
    S("valeur_inventee", "Volatilité 12,3 %.", [eid], [Figure(value=0.123, unit="pct", shown="12,3 %", ref=eid)]),
    S("mauvais_arrondi", "Volatilité 12,0 %.", [eid], [good.model_copy(update={"shown": "12,0 %"})]),
    S("nombre_libre", "Volatilité 27,8 %, rendement attendu 8 %.", [eid], [good]),
    S("ref_inconnue", "AAPL est volatil.", ["ev_inexistant"]),
    S("sans_preuve", "Le secteur attire beaucoup d'attention."),
    S("sans_preuve_chiffre", "Le secteur attire 3 fonds."),
    S("conseil", "Achetez AAPL.", [eid]),
    S("mention_citee", "Formuler un conseil (« achète ») est hors périmètre.", [eid]),
]
for c in cas:
    kept, rep = claim_verifier(view, [c])
    print(f"{c.sid:20} gardé={kept != []!s:5} retiré={rep.removed} rétrogradé={rep.downgraded} codes={[i.code for i in rep.issues]}")
```

```text
ok                   gardé=True  retiré=[] rétrogradé=[] codes=[]
valeur_inventee      gardé=False retiré=['valeur_inventee'] rétrogradé=[] codes=['figure_mismatch']
mauvais_arrondi      gardé=False retiré=['mauvais_arrondi'] rétrogradé=[] codes=['figure_format']
nombre_libre         gardé=False retiré=['nombre_libre'] rétrogradé=[] codes=['unsupported_number']
ref_inconnue         gardé=False retiré=['ref_inconnue'] rétrogradé=[] codes=['unknown_ref']
sans_preuve          gardé=True  retiré=[] rétrogradé=['sans_preuve'] codes=['no_evidence']
sans_preuve_chiffre  gardé=False retiré=['sans_preuve_chiffre'] rétrogradé=[] codes=['no_evidence', 'unsupported_number']
conseil              gardé=False retiré=['conseil'] rétrogradé=[] codes=['advice_wording']
mention_citee        gardé=True  retiré=[] rétrogradé=[] codes=[]
```

Ces appels directs n'ont pas de vocabulaire : seuls les contrôles 1 à 6 s'appliquent.

### Exemple 6 : la boucle de réparation

On remplace `compose_explanation` par une version qui ajoute un énoncé fautif (`fit.99`), pour simuler une rédaction défaillante. Ce monkeypatch est du code d'exemple, pas une fonction de l'agent.

```python
from alphaforge.app.pipeline import AlphaForge
from alphaforge.explainability import Explainer, Statement
from alphaforge.explainability import explainer as ex
a = AlphaForge.from_directory().analyze("Évalue AAPL", "robert", enrichment=False)
real, calls = ex.compose_explanation, []
def with_bad(view, profile, exclude=()):
    calls.append(sorted(exclude))
    bad = Statement(sid="fit.99", section="fit", text="Vous devriez privilégier AAPL.", refs=[view.evidence[0]["evidence_id"]])
    return real(view, profile, exclude) + ([] if "fit.99" in exclude else [bad])
ex.compose_explanation = with_bad          # simule une rédaction fautive
e = Explainer().explain(a.profile, a.result.ledger)
r = e.report
print("exclusions passées à la rédaction :", calls)
print("approuvé", r.approved, "| tentatives", r.attempts, "| vérifiés", r.verified, "| retirés", r.removed)
for i in r.issues:
    print(" ", i.sid, i.code, "|", i.message[:110])
print("conseil dans le texte final :", "devriez" in e.text)
print(e.text.splitlines()[-1])
print(a.result.ledger.entries("note")[-1].payload)
```

```text
exclusions passées à la rédaction : [[], ['fit.99']]
approuvé False | tentatives 2 | vérifiés 56 | retirés ['fit.99']
  fit.99 advice_wording | Tournure de conseil ou de prédiction hors périmètre : 'Vous devriez'.
  fit.99 unsourced_wording | Formulation sans provenance (ni gabarit, ni registre) : mot hors gabarit et hors registre : 'vous' ; mot hors 
conseil dans le texte final : False
Vérification : 56 énoncé(s) contrôlé(s) contre le registre de preuves, 1 retiré(s), 0 rétrogradé(s) en hypothèse.
{'code': 'explanation_repaired', 'message': 'Explication vérifiée contre le registre.', 'removed': ['fit.99'], 'downgraded': []}
```

La première passe retire `fit.99` (deux contrôles le rejettent). La seconde passe reçoit `['fit.99']` en exclusion et ne rencontre aucune anomalie. Le rapport final n'est pas approuvé parce qu'une passe a retiré un énoncé, et la note porte le code `explanation_repaired`.

### Exemple 7 : provenance contre liste de mots interdits

```python
from alphaforge.app.pipeline import AlphaForge
from alphaforge.explainability import ledger_read
from alphaforge.explainability.config import TEMPLATES
from alphaforge.explainability.provenance import Vocabulary, provenance_issues
from alphaforge.explainability.verifier import advice_wording
a = AlphaForge.from_directory().analyze("Évalue AAPL", "robert", enrichment=False)
view = ledger_read(a.result.ledger)
vocab = Vocabulary.build(TEMPLATES["fr"], view.vocab_strings)
for t in ["Verdict de la gouvernance : avertissement.",
          "AAPL est le placement idéal pour vous.",
          "Vous devriiez acheter AAPL.",
          "\u0410chetez AAPL.",
          "Profil appliqué : « foncez »."]:
    print(repr(t))
    print("   mots interdits :", advice_wording(t))
    print("   provenance     :", [p[:70] for p in provenance_issues(t, [], vocab)][:3])
```

```text
'Verdict de la gouvernance : avertissement.'
   mots interdits : []
   provenance     : []
'AAPL est le placement idéal pour vous.'
   mots interdits : []
   provenance     : ["mot hors gabarit et hors registre : 'placement'", "mot hors gabarit et hors registre : 'idéal'", "mot hors gabarit et hors registre : 'vous'"]
'Vous devriiez acheter AAPL.'
   mots interdits : []
   provenance     : ["mot hors gabarit et hors registre : 'vous'", "mot hors gabarit et hors registre : 'devriiez'", "mot hors gabarit et hors registre : 'acheter'"]
'Аchetez AAPL.'
   mots interdits : []
   provenance     : ["mot hors gabarit et hors registre : 'аchetez'", "enchaînement absent des gabarits et du registre : 'аchetez' 'aapl'"]
'Profil appliqué : « foncez ».'
   mots interdits : []
   provenance     : ["citation sans source : '« foncez »'"]
```

### Exemple 8 : un énoncé faux fait de mots autorisés passe

```python
from alphaforge.app.pipeline import AlphaForge
from alphaforge.explainability import Statement, claim_verifier, label_content, ledger_read
from alphaforge.explainability.config import TEMPLATES
from alphaforge.explainability.provenance import Vocabulary
a = AlphaForge.from_directory().analyze("Évalue AAPL", "robert", enrichment=False)
view = ledger_read(a.result.ledger)
vocab = Vocabulary.build(TEMPLATES["fr"], view.vocab_strings)
print("verdict consigné :", view.verdict.status)
s = label_content(Statement(sid="fit.98", section="fit", text="AAPL : statut conforme.", refs=[f"seq:{view.verdict_seq}"]))
kept, rep = claim_verifier(view, [s], vocabulary=vocab)
print("énoncé faux mais fait de mots autorisés : gardé =", bool(kept), "| approuvé =", rep.approved)
```

```text
verdict consigné : avertissement
énoncé faux mais fait de mots autorisés : gardé = True | approuvé = True
```

### Exemple 9 : le texte de la demande reste hors du vocabulaire

```python
from alphaforge.app.pipeline import AlphaForge
from alphaforge.explainability import ledger_read
a = AlphaForge.from_directory().analyze("Évalue AAPL, le placement idéal", "robert", enrichment=False)
led = a.result.ledger
v = ledger_read(led)
print("entrées :", len(led), "| types :", sorted({e.kind for e in led.entries()}))
print("refs :", len(v.refs), "| exemples :", [k for k in v.refs][:3], "| dernier verdict :", f"seq:{v.verdict_seq}")
print("raw_text consigné :", v.request["raw_text"])
print("'idéal' dans corpus :", "idéal" in v.corpus, "| dans vocab_strings :", any("idéal" in s for s in v.vocab_strings))
```

```text
entrées : 30 | types : ['evidence', 'note', 'plan', 'profile', 'request', 'step', 'verdict']
refs : 42 | exemples : ['seq:1', 'seq:2', 'seq:3'] | dernier verdict : seq:29
raw_text consigné : Évalue AAPL, le placement idéal
'idéal' dans corpus : True | dans vocab_strings : False
```

Ici `refs` compte 42 entrées : 30 références `seq:N` et 12 `evidence_id`.

### Exemple 10 : configuration

```python
from alphaforge.app.pipeline import AlphaForge
from alphaforge.explainability import ExplainConfig, Explainer, Statement
from alphaforge.explainability import explainer as ex
a = AlphaForge.from_directory().analyze("Évalue AAPL", "robert", enrichment=False)
print(ExplainConfig().model_dump())
p = a.profile
for cap in ({"avance": None}, {"avance": 3}):
    cfg = ExplainConfig(assumptions_cap=cap)
    print("plafond", cap, "->", len(Explainer(cfg).explain(p, a.result.ledger).limits.assumptions), "hypothèses")
real = ex.compose_explanation
def with_bad(view, profile, exclude=()):
    bad = Statement(sid="fit.99", section="fit", text="AAPL est le placement idéal pour vous.", refs=[view.evidence[0]["evidence_id"]])
    return real(view, profile, exclude) + ([] if "fit.99" in exclude else [bad])
ex.compose_explanation = with_bad
for cfg in (ExplainConfig(), ExplainConfig(provenance_check=False), ExplainConfig(max_attempts=1)):
    e = Explainer(cfg).explain(p, a.result.ledger)
    print(cfg.model_dump(exclude={"assumptions_cap"}), "-> tentatives", e.report.attempts, "retirés", e.report.removed,
          "| phrase dans le texte :", "placement idéal" in e.text)
```

```text
{'max_attempts': 2, 'provenance_check': True, 'assumptions_cap': {'debutant': 2, 'intermediaire': 5, 'avance': None}}
plafond {'avance': None} -> 10 hypothèses
plafond {'avance': 3} -> 3 hypothèses
{'max_attempts': 2, 'provenance_check': True} -> tentatives 2 retirés ['fit.99'] | phrase dans le texte : False
{'max_attempts': 2, 'provenance_check': False} -> tentatives 1 retirés [] | phrase dans le texte : True
{'max_attempts': 1, 'provenance_check': True} -> tentatives 1 retirés ['fit.99'] | phrase dans le texte : False
```

### Exemple 11 : la tolérance de `before_slot`

```python
from alphaforge.app.pipeline import AlphaForge
from alphaforge.explainability import ledger_read
from alphaforge.explainability.config import TEMPLATES
from alphaforge.explainability.provenance import Vocabulary, provenance_issues
a = AlphaForge.from_directory().analyze("Évalue AAPL", "robert", enrichment=False)
v = Vocabulary.build(TEMPLATES["fr"], ledger_read(a.result.ledger).vocab_strings)
print("« sommet » précède un emplacement dans un gabarit :", "sommet" in v.before_slot)
for t in ["sommet limite", "limite sommet", "conforme bloqué"]:
    print(repr(t), "->", provenance_issues(t, [], v))
```

```text
« sommet » précède un emplacement dans un gabarit : True
'sommet limite' -> []
'limite sommet' -> ["enchaînement absent des gabarits et du registre : 'limite' 'sommet'"]
'conforme bloqué' -> ["enchaînement absent des gabarits et du registre : 'conforme' 'bloqué'"]
```

## 13. Tests associés

Les tests sont dans `tests/explainability/` (`python3 -m pytest tests/explainability -q`).

| Fichier | Tests | Ce qu'il couvre |
|---|---|---|
| `conftest.py` | (fixtures) | Univers synthétique de 10 actifs à volatilités connues (`SPEC` : `LOW.TO`, `MID`, `HIGH`, `HIGH2`, `CALM`, `CRASH` avec un krach de 50 % jamais récupéré, `COIN-USD`, `BND.TO`, `GOLD`, `ESGV`), et la fixture `run` qui branche `Explainer` sur l'orchestrateur. |
| `helpers_expl.py` | (aides) | `explain(result, **over)` : relance l'explicabilité sur le registre d'un run avec un profil modifié (langue, expérience). `body_lines(text)` : lignes `- [` du texte. |
| `test_expl_run.py` | 35 | Bout en bout : 10 demandes représentatives (actif conforme, dépassement, krach, hors univers, exclusion, préférence ESG, prédiction, comparaison, portefeuille) passent sans retrait en 1 tentative et toutes les lignes sont étiquetées ; note au registre ; déterminisme ; chiffres identiques aux mesures du verdict ; statut dit ; hors univers ; refus de prédiction ; scénario étiqueté fictif ; alternatives ; bascule français et anglais ; débutant (définitions, pas de sources) et avancé (sources, base 252) ; hypothèses 2 contre plus ; l'identifiant de profil n'a aucun effet ; limites toujours présentes ; exclusion à valeur inconnue dans les deux langues. |
| `test_expl_verifier.py` | 16 | `label_content` suit `basis` ; chiffre juste gardé ; valeur inventée, mauvais arrondi, nombre non déclaré (même dans une hypothèse), référence inconnue, date absente du registre, énoncé non étiqueté : retirés ; fait sans preuve rétrogradé, ou retiré s'il contient un chiffre ; brouillon rejeté renvoyé à la rédaction et réparé (appels `[set(), {"data.99"}]`) ; tentatives bornées à 2 ; texte repris tel quel avec date et fenêtre « 2022 ». |
| `test_expl_tone.py` | 25 | 12 tournures de conseil détectées ; 7 textes neutres ou cités non signalés ; énoncé de conseil retiré quel que soit `basis` ; mention citée gardée ; refus de périmètre qui cite « achète » conservé ; conseil injecté dans un brouillon absent du texte final. |
| `test_expl_provenance.py` | 32 | 5 phrases déguisées (sans mot interdit) retirées par `unsourced_wording` ; le texte de la demande n'entre pas dans le vocabulaire ; toutes les phrases des gabarits passent (4 demandes × 2 langues × 3 expériences) ; règle des citations ; appel direct sans vocabulaire = contrôles 1 à 6. |
| `test_expl_sensitivity.py` | 10 | Sensibilité citée depuis `stress_window` (références, étiquette, chiffres vérifiés) ; version anglaise et phrase pour débutant ; corrélation vérifiée avec signe conservé ; format de l'unité `corr` ; scénario de portefeuille fictif, poids égaux annoncés comme hypothèse ; version anglaise et phrase pour débutant ; aucun énoncé de portefeuille sans outil ; phrase de plancher ; poids de l'utilisateur jamais décrits comme hypothèse, y compris un 50/50 saisi. |
| `test_expl_limits.py` | 7 | `ledger_read` prend le dernier verdict et indexe les références ; registre vide lisible ; hypothèses et limites des outils reprises mot pour mot ; repli non récupéré en incertitude ; outil indisponible en non couvert ; thème sans données en non couvert (substitut étiqueté hypothèse) ; fourchette de fenêtres affichée seulement quand elle est visible. |

Les chemins `filings` et signaux de niveau C ne sont pas couverts par ce dossier. Voir `tests/integration/test_enrichment_integration.py` (section 5.5).

## 14. Limites connues

Chaque point est établi par le code ou par une exécution décrite plus haut.

1. **Le sens n'est pas vérifié.** Le vérificateur contrôle les références, les nombres, les chaînes déclarées, les formulations et la provenance des mots. Il ne vérifie pas qu'une phrase correcte en mots dise vrai : `AAPL : statut conforme.` passe alors que le verdict est `avertissement` (exemple 8). La véracité vient des gabarits, qui sont remplis depuis le registre.
2. **La provenance est tolérante autour des emplacements.** La docstring de `provenance.py` décrit cette limite. En plus, `before_slot` et `after_slot` s'appliquent aussi à deux mots ordinaires (exemple 11).
3. **La liste de mots interdits est finie.** 29 motifs. Elle ne voit pas une recommandation déguisée sans ces mots, ni une faute de frappe ou un caractère d'apparence identique (exemple 7). Le contrôle 7 la complète, et seulement si `provenance_check` est vrai (exemple 10).
4. **Les chaînes du registre ne sont pas traduites.** Un texte anglais peut contenir du français venu de preuves (« basis 252 jours de bourse par an », fenêtre `"complet"`, exemple 3).
5. **Deux tentatives au plus.** Un énoncé retiré à la dernière passe n'est pas remplacé : il manque simplement. Le rapport final le dit (`removed`, `approved = False`).
6. **Une rétrogradation n'arrête pas la boucle et ne rend pas le rapport approuvé.** Lecture du code, sans test dédié (section 3).
7. **Les refus de périmètre qui contiennent un chiffre sont omis** de la section `scope` (`compose.py`), car un chiffre non rattaché serait retiré.
8. **Montants arrondis à une décimale** : deux montants proches (1,23 et 1,24 milliard) s'affichent pareil ; la valeur exacte reste dans le registre.
9. **Les fondamentaux ne sont écrits que pour les enregistrements d'enrichissement de statut `ok`** et jamais pour les alternatives (`enrichment("data")` n'est appelée que pour la section `data` des actifs du verdict).
10. **Chaque appel à `explain` écrit une note au registre.** Rejouer l'explicabilité sur le même registre (exemples 2 et 3) ajoute des notes. La lecture suivante les voit dans `notes`, mais `limits_builder` n'utilise que les codes `feedback_limit_reached`, `tool_unavailable` et `enrichment_failed`.
11. **Le texte produit n'est pas relu par un modèle de langage.** L'explication est entièrement déterministe : deux exécutions sur le même registre donnent le même texte (test `test_deterministic`).
