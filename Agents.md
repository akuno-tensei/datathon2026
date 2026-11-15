# AlphaForge AI : Architecture agentique

> **Positionnement :** AlphaForge AI est une **analyste agentique d'aide à la décision, en simulation**. Elle met les données publiques en rapport avec le profil d'un investisseur, vérifie la cohérence de chaque idée avec ses contraintes et explique, preuves à l'appui, ce qui soutient ses conclusions. Elle éclaire la décision, elle ne la prend pas.
>
> **Test pour trancher :** qui conclut ? Si le système dit « achète ceci », c'est un conseiller (hors cadre). S'il dit « voici ce que les données montrent, voici où ça cadre ou non avec ton profil, voici ce qui est incertain », c'est une analyste (ce que le mandat valorise).

---

# Section 1 : Architecture principale (4 agents)

Le mandat encourage une approche agentique **simple, focalisée et vérifiable**. L'architecture repose sur peu d'agents, des outils déterministes et un **registre de preuves** que tous alimentent, mais que seul l'agent d'explicabilité lit pour écrire.

## 1.1 Principes directeurs

1. **Le profil est une donnée, pas du code.** Un schéma (horizon, tolérance, objectifs, préférences, exclusions) dont dérivent les règles. Aucune logique du genre `if persona == "Robert"`.
2. **Contraintes dures vs préférences souples.** Une exclusion éthique bloque, alors qu'un intérêt pour l'IA oriente.
3. **Le LLM orchestre et explique, mais ne calcule pas.** Tous les chiffres viennent d'outils déterministes.
4. **Registre de preuves (*evidence ledger*).** Chaque affirmation pointe vers un appel d'outil, un jeu de données, une période et des paramètres.
5. **La gouvernance rend un verdict visible** (`conforme`, `avertissement`, `bloqué`) avec ses raisons.
6. **L'incertitude fait partie du résultat**, pas un avertissement ajouté en bas de page.

## 1.2 Les 4 agents

### Agent 1 : Orchestrateur

**Rôle :** comprendre la demande, charger le profil, planifier, router, assembler la réponse finale.

| Outil | Fonction |
|---|---|
| `load_profile` | Renvoie le profil structuré (horizon, tolérance, préférences, exclusions) |
| `parse_request` | Convertit la demande libre en intention structurée (évaluer un actif, comparer, construire un scénario, expliquer un risque) avec les actifs visés |
| `validate_profile` | Détecte les profils internes incohérents (risque élevé avec horizon d'un an) ou incomplets |
| `ledger_write` | Consigne chaque étape du plan |

**Ne fait jamais :** de calcul, ni de verdict.

### Agent 2 : Analyste quantitatif

**Rôle :** produire des faits chiffrés et reproductibles, **sans prédiction**.

| Outil | Fonction |
|---|---|
| `asset_lookup` | Vérifie qu'un actif est dans l'univers (31 actifs) et retourne ses métadonnées |
| `get_prices` | Prix ajustés sur une fenêtre explicite |
| `convert_currency` | Conversion CAD/USD à la date de chaque observation |
| `compute_metrics` | Rendement annualisé, volatilité, drawdown maximal |
| `correlation_matrix` / `portfolio_metrics` | Diversification et concentration |
| `stress_window` | Recalcule sur une période (2022, ou avec et sans 2022) pour tester la sensibilité |
| `data_quality_check` | Données manquantes, fractionnements, couverture de dates |

**Sortie :** des objets `{valeur, source, fenêtre, paramètres}` écrits au registre.

### Agent 3 : Gouvernance

**Rôle :** vérifier que l'idée ou le scénario respecte le profil et les limites du défi.

| Outil | Fonction |
|---|---|
| `derive_constraints` | Traduit le profil en limites numériques (volatilité maximale, drawdown toléré, plafonds par actif et par classe). Règles **dérivées du profil**, pas codées par persona |
| `check_hard_constraints` | Exclusions et interdits (bloquant) |
| `check_soft_preferences` | Alignement avec les préférences (orientant) |
| `check_horizon_fit` | Cohérence horizon et profil de risque de l'actif |
| `check_data_gaps` | Signale ce qui manque (ex. absence de données ESG) et le substitut utilisé |
| `check_scope_limits` | Refuse prédiction, transaction réelle, formulation de conseil personnalisé |
| `issue_verdict` | `conforme`, `avertissement` ou `bloqué`, avec raisons et mesures chiffrées |

**Particularité :** peut renvoyer une requête à l'analyste pour chiffrer un écart ou tester une alternative.

### Agent 4 : Explicabilité (rédaction + vérification)

**Rôle :** produire l'explication finale **à partir du seul registre de preuves**.

| Outil | Fonction |
|---|---|
| `ledger_read` | Seul accès aux faits |
| `compose_explanation` | Adapte le niveau de langage et la structure au profil (expérience, langue) |
| `claim_verifier` | Contrôle que chaque nombre et chaque affirmation correspond à une entrée du registre. Une affirmation sans preuve est supprimée ou marquée comme hypothèse |
| `limits_builder` | Compile hypothèses, limites, incertitudes et ce qui n'est pas couvert |
| `label_content` | Étiquette chaque énoncé : fait, hypothèse ou scénario fictif. *Implémenté avec une quatrième étiquette, « synthèse », pour les phrases qui relient le verdict à ses raisons (`synthesis.py`).* |

## 1.3 Liens entre les agents

```
                    Demande + profil
                           │
                    ┌──────▼───────┐
                    │ Orchestrateur│◄─────────────┐
                    └──┬───────┬───┘              │
          plan         │       │                  │
              ┌────────▼──┐    │                  │
              │ Analyste  │    │                  │
              │ quantitatif│   │                  │
              └────┬──────┘    │                  │
                   │ faits     │                  │
                   ▼           │                  │
        ╔═════════════════════════════╗           │
        ║  REGISTRE DE PREUVES        ║           │
        ╚═════════╤═══════════▲═══════╝           │
                  │           │ verdict + raisons │
                  │      ┌────┴────────┐          │
                  │      │ Gouvernance │──────────┘
                  │      └────┬────────┘  si bloqué :
                  │           │           demande alternative
                  ▼           ▼
              ┌───────────────────┐
              │  Explicabilité    │
              │ rédaction + vérif │
              └─────────┬─────────┘
                        ▼
                Réponse expliquée
```

**Règles de communication :**

1. **Messages structurés, pas de bavardage libre.** Chaque agent reçoit et renvoie des objets typés (JSON), ce qui rend les traces observables.
2. **Un seul sens pour les faits :** Analyste → registre → Explicabilité. L'explicabilité ne « se souvient » de rien et ne recalcule rien.
3. **Une seule boucle de retour :** Gouvernance → Orchestrateur → Analyste, pour chiffrer une tension ou tester une alternative (maximum 2 itérations, pour rester prévisible).
4. **Le vérificateur peut rejeter** une explication et la renvoyer à la rédaction.

## 1.4 Exemple de trace : Robert demande une action très médiatisée

1. L'orchestrateur charge le profil et identifie l'intention « évaluer un actif ».
2. L'analyste calcule volatilité, drawdown et corrélations sur deux fenêtres, puis écrit au registre.
3. La gouvernance dérive les limites de Robert (horizon de 3 à 5 ans, risque faible à modéré), constate un dépassement chiffré et émet un **avertissement**.
4. L'orchestrateur demande une alternative : l'analyste évalue un actif plus stable du même secteur.
5. L'explicabilité rédige : ce qui attire, l'écart mesuré avec le profil, l'alternative, les limites (le passé ne prédit pas l'avenir). Le vérificateur confirme chaque chiffre.

## 1.5 Garde-fous et choix de simplicité

- **Pas plus de 4 agents** dans le noyau : le nombre d'agents n'est pas une fin en soi.
- **Aucune règle propre à un persona.** Toute spécificité vient du profil.
- **Bloquer seulement sur une règle explicite.** Le mandat demande de vérifier et de signaler, pas de bloquer : `bloqué` est réservé à un refus déclaré par l'investisseur, à une classe que son niveau de risque n'autorise pas et aux limites du défi ; un écart chiffré reste un avertissement. *Justification détaillée : `alphaforge/governance/README.md`, section 7.5.*
- **Aucun chiffre produit par le modèle de langage.** Seulement les outils.
- **Simulation uniquement :** aucune transaction réelle, aucun conseil personnalisé, aucune prédiction de rendements futurs.
- **Sorties en fourchettes** et étiquetées « scénario simulé », plutôt qu'une allocation unique « optimale ».

## 1.6 Comportement du noyau face aux cas non entraînés

| Situation connexe | Comportement attendu |
|---|---|
| Nouveau persona (ex. retraité de 70 ans voulant des cryptos) | La gouvernance bloque ou avertit via les règles dérivées du profil |
| Profil incohérent (risque élevé, horizon d'un an) | `validate_profile` signale le conflit interne |
| Actif hors univers | Le système dit qu'il n'a pas les données et propose d'en ajouter avec documentation. *Implémenté : il le dit ; l'ajout d'une série de prix n'est pas encore possible (aucune source de prix branchée).* |
| « Prédis NVDA l'an prochain » | `check_scope_limits` refuse la prédiction et offre un descriptif historique et des scénarios |
| Préférence ESG sans donnée ESG | Avertissement explicite du substitut utilisé (ex. FNB filtré ESG du jeu de départ) |

---

# Section 2 : Extension avec l'agent d'enrichissement (architecture à 5 agents)

L'agent d'enrichissement ne tourne pas à chaque requête. Il s'active quand un **besoin de donnée** dépasse le jeu de départ (ESG, controverses, fondamentaux, événements récents). Dans l'implémentation, une demande sur une action américaine crée toujours un besoin de contexte (ses fondamentaux SEC, sans effet sur le verdict) ; pour les autres actifs, seuls le profil et le texte de la demande en créent. Le noyau à 4 agents reste donc fonctionnel seul, ce qui offre une **version de repli** pour la démo.

## 2.1 L'agent d'enrichissement

**Rôle :** aller chercher des données publiques absentes du jeu de départ, les normaliser, les dater, et les écrire au registre avec leur provenance complète. Il **ne conclut rien** : il fournit des faits sourcés et des signaux qualitatifs.

| Outil | Fonction |
|---|---|
| `detect_needs` | Compare les besoins (profil + requête) aux données disponibles et produit une liste de lacunes comblables (ESG, controverses, fondamentaux, nouvelles) |
| `fetch_esg` | Information ESG publiée (secteur, émissions déclarées, controverses documentées) avec la méthodologie du fournisseur |
| `fetch_filings` | Dépôts réglementaires (SEC EDGAR, SEDAR+) et communiqués de résultats |
| `fetch_fundamentals` | Revenus, bénéfices, dette, flux de trésorerie, ratios |
| `fetch_news` | Nouvelles datées et documents publics |
| `fetch_etf_holdings` | Titres détenus par un FNB, pondérations, frais (utile pour vérifier l'ESG « par transparence ») |
| `point_in_time_filter` | **Filtre obligatoire** : ne garde que ce qui était publié avant la date d'analyse (`as_of`) |
| `extract_facts` | Transforme un texte en faits structurés `{énoncé, extrait court, URL, date de publication, date d'observation}` |
| `source_card` | Fiche source : origine, date, traitement, limites, conditions d'utilisation et de réutilisation |
| `ledger_write` | Écrit chaque donnée enrichie au registre avec son niveau de confiance |

## 2.2 Garde-fous propres à cet agent

C'est l'agent le plus risqué (texte non structuré, hallucination possible) : il porte donc les règles les plus strictes.

1. **Cohérence temporelle.** Chaque donnée porte `date_observation` et `date_publication`. Le filtre `point_in_time_filter` s'applique avant toute écriture au registre. Une nouvelle postérieure à `as_of` n'existe pas pour la simulation.
2. **Aucun fait sans trace.** Un fait extrait d'un texte doit contenir l'URL, la date et un court extrait. Sans cela, il est rejeté.
3. **Niveaux de confiance :**
   - **A :** donnée structurée d'une source officielle (dépôt réglementaire, banque centrale).
   - **B :** donnée structurée d'un fournisseur tiers (cote ESG, avec sa méthodologie).
   - **C :** information tirée d'un texte ou d'une nouvelle (qualitative, jamais utilisée pour un calcul).
4. **Les nouvelles n'alimentent aucun calcul.** Elles contextualisent et déclenchent un avertissement, jamais un chiffre ni un blocage à elles seules.
5. **Licences.** La fiche source consigne les conditions de réutilisation. Le registre stocke des métadonnées et de courts extraits, pas des articles complets.
6. **Gestion de l'échec.** Si une source est inaccessible ou limitée par une API, l'agent écrit un enregistrement `lacune_non_comblée` plutôt que de combler par une supposition.

## 2.3 Nouvelle architecture

```
                    Demande + profil
                           │
                    ┌──────▼───────┐
                    │ Orchestrateur│◄──────────────────┐
                    └──┬───────┬───┘                   │
        besoins de     │       │ plan                  │
        données        │       │                       │
              ┌────────▼───┐   │                       │
              │Enrichisse- │   │                       │
              │ment (à la  │   │                       │
              │ demande)   │   │                       │
              └────┬───────┘   │                       │
                   │ faits sourcés + as_of             │
                   ▼           ▼                       │
              ┌─────────────────────┐                  │
              │ Analyste quantitatif│                  │
              └──────────┬──────────┘                  │
                         │ faits chiffrés              │
                         ▼                             │
          ╔═══════════════════════════════╗            │
          ║  REGISTRE DE PREUVES          ║            │
          ║  (valeur, source, as_of,      ║            │
          ║   confiance A/B/C)            ║            │
          ╚════════╤══════════════▲═══════╝            │
                   │              │ verdict + raisons  │
                   │        ┌─────┴───────┐            │
                   │        │ Gouvernance │────────────┘
                   │        └─────────────┘  alternative
                   │                         ou donnée manquante
                   ▼
          ┌──────────────────┐
          │  Explicabilité   │
          │ rédaction + vérif│
          └────────┬─────────┘
                   ▼
           Réponse expliquée
```

## 2.4 Ce qui change par rapport au noyau

| Élément | Noyau (4 agents) | Avec extension (5 agents) |
|---|---|---|
| Sources de données | Jeu de départ uniquement | Jeu de départ + données publiques enrichies |
| Orchestrateur | Planifie analyste, gouvernance, explicabilité | Ajoute l'appel à `detect_needs` après `parse_request` et `validate_profile` |
| Analyste | Utilise prix, macro, devises | Peut aussi utiliser les fondamentaux de niveau A ou B (ex. dette sur flux de trésorerie). **Jamais le niveau C**. *Pas encore implémenté : les fondamentaux sont affichés comme contexte, aucun outil de l'analyste ne les calcule.* |
| Registre | `{valeur, source, fenêtre, paramètres}` | Ajoute `date_publication`, `as_of`, `confiance A/B/C` et le lien vers la `source_card` |
| Gouvernance | Signale les lacunes de données | `check_data_gaps` indique ce qui a été comblé, avec quelle confiance, et ce qui reste manquant |
| Explicabilité | Étiquette fait, hypothèse, scénario | Ajoute niveau de confiance et date de publication à chaque énoncé enrichi, et limites propres aux sources |
| Boucles de retour | Gouvernance → Orchestrateur → Analyste | + Gouvernance → Orchestrateur → Enrichissement (une seule relance). *Implémenté pour les alternatives chiffrées par l'analyste : elles reçoivent les données que la demande ou le profil exigent, pas les fondamentaux de contexte.* |

**Détail des nouveaux échanges :**

1. **Orchestrateur → Enrichissement.** Exemple : le profil contient « éviter les controverses » et le jeu de départ n'a aucune donnée ESG, donc l'enrichissement s'active.
2. **Enrichissement → Analyste.** Seules les données de niveau A ou B alimentent les calculs.
3. **Enrichissement → Registre.** Écriture directe avec la fiche source, pour que l'explicabilité cite l'origine exacte de chaque donnée.
4. **Gouvernance → Orchestrateur → Enrichissement.** Si `check_data_gaps` constate une lacune comblable (ex. controverse non vérifiée sur un actif retenu), l'orchestrateur relance l'enrichissement **une seule fois**, puis la gouvernance réévalue. Si la lacune persiste, elle devient une limite explicite.
5. **Règles de blocage mises à jour :**
   - `check_hard_constraints` peut bloquer sur une exclusion **seulement** si la donnée est de niveau A ou B.
   - Une alerte de niveau C ne produit qu'un **avertissement**.

## 2.5 Exemple de trace : Mateo (préférences ESG)

> *Scénario cible.* Aucune source de cotes ESG ni de nouvelles n'est branchée à ce jour : pour Mateo, ces besoins deviennent des lacunes nommées, et la gouvernance signale que ses préférences ESG ne sont pas vérifiables (substitut déclaré : le FNB filtré ESGV). Les étapes ci-dessous décrivent le comportement attendu une fois une source branchée ; les règles de blocage (A ou B seulement) sont implémentées et testées avec des sources simulées.

1. L'orchestrateur charge le profil (modéré, horizon de 8 à 12 ans, ESG important, éviter les controverses). `detect_needs` signale : cotes ESG et controverses absentes du jeu de départ.
2. L'enrichissement récupère l'information ESG et les documents publics pour les actifs candidats, applique le filtre `as_of` et crée les fiches sources. Résultat : cotes de niveau B pour 6 actifs sur 8, et pour 2 actifs une seule nouvelle de niveau C.
3. L'analyste calcule volatilité, drawdown et corrélations sur deux fenêtres, puis écrit au registre.
4. La gouvernance dérive les limites de Mateo. Les actifs à cote ESG faible sont **bloqués** (niveau B). Les deux actifs documentés seulement par une nouvelle reçoivent un **avertissement** (niveau C, à vérifier).
5. L'explicabilité rédige : ce qui est retenu et pourquoi, les sources et dates, les niveaux de confiance, les limites (méthodologies ESG différentes selon les fournisseurs, couverture incomplète, passé ne prédisant pas l'avenir). Le vérificateur confirme chaque affirmation.

## 2.6 Impacts à anticiper

- **Intégration AWS (15 % de la grille) :** les appels de collecte et d'extraction se prêtent à des fonctions serverless, avec un cache des données déjà récupérées dans le stockage objet. L'enrichissement devient reproductible et évite de refaire les appels externes en démo. Vérifier les services et conditions d'accès officiels avant de s'engager. *Choix retenu : AWS sert à l'interprétation des demandes (Bedrock), à l'archive des registres (S3) et à l'hébergement (ECS Fargate, CloudFront) ; la collecte SEC tourne dans l'application, avec un cache local et un instantané versionné dans le dépôt, pour que la démo hors ligne survive au compte de l'événement.*
- **Démo fiable :** pré-charger en cache les données d'enrichissement des actifs de la démo. Un appel réseau qui échoue en direct est le risque le plus évident de cette extension. *Fait : `alphaforge_starter/enrichment_snapshot/` (données SEC arrêtées au 12 novembre 2026).*
- **Mode dégradé :** si l'enrichissement est désactivé ou échoue, le système revient au noyau à 4 agents et le dit explicitement. C'est un argument fort pour le jury : le système sait ce qu'il ne sait pas.
- **Licences et conformité :** documenter pour chaque source la pertinence, l'origine, la date, les traitements, les limites et les conditions de réutilisation, comme l'exige le mandat.

## 2.7 Récapitulatif des agents

| # | Agent | Activation | Produit |
|---|---|---|---|
| 1 | Orchestrateur | Toujours | Plan, routage, réponse assemblée |
| 2 | Enrichissement | À la demande | Faits sourcés, datés, avec niveau de confiance |
| 3 | Analyste quantitatif | Toujours | Faits chiffrés reproductibles |
| 4 | Gouvernance | Toujours | Verdict visible et raisons |
| 5 | Explicabilité | Toujours | Explication vérifiée, limites |

---

## État de l'implémentation (15 novembre 2026)

Les étapes suggérées à l'origine (schéma du profil, schéma du registre, pile technique, démo à profils contrastés) sont faites. Ce qui est implémenté, par rapport à cette architecture :

| Élément | État | Écart avec la conception |
|---|---|---|
| Orchestrateur | Implémenté. `parse_request` (`LLMParser`, `RuleBasedParser`) passe par Amazon Bedrock (Claude Haiku 4.5), revérifié par des règles : actifs contre l'univers, poids lus dans le texte, signaux de périmètre jamais retirés. Règles seules sans accès AWS. | — |
| Analyste quantitatif | Les 8 outils de 1.2 sont implémentés, déterministes et reproductibles (même appel, même `evidence_id`). | N'utilise pas encore les fondamentaux (voir 2.4). |
| Gouvernance | Implémentée, avec `derive_constraints`, les contrôles de 1.2 (le verdict est rendu par `Governance.assess`), la boucle de retour (2 itérations au plus) et les règles de blocage de 2.4. | — |
| Explicabilité | Implémentée. `claim_verifier` contrôle chaque chiffre contre le registre, les tournures de conseil (liste de mots interdits) et la **provenance** de chaque mot (liste blanche : gabarits de rédaction et registre, sans le texte de la demande). | Contrôle de provenance ajouté à la conception. |
| Enrichissement | Implémenté avec les 10 outils de 2.1 et les garde-fous de 2.2. Source branchée : SEC EDGAR (niveau A) pour les dépôts et les fondamentaux des actions américaines. Chaîne : SEC en direct (identification exigée), cache local, instantané versionné. | Aucune source pour l'ESG, les nouvelles, la composition des FNB ni SEDAR+ : lacunes nommées. Les fondamentaux d'une action américaine sont recherchés à chaque demande (contexte, sans effet sur le verdict). |
| Registre de preuves | Implémenté : entrées de l'analyste (`evidence_id`, paramètres, empreinte des données), de l'enrichissement (`date_publication`, `as_of`, confiance, fiche source), notes et verdicts. Archivé dans S3 en version hébergée. | — |
| Interface | Streamlit, bilingue, trace des agents en direct, preuve ouvrable pour chaque chiffre, lexique pour débutant, comparaison de deux profils, export Markdown. | — |

Les limites connues et le mode d'emploi sont dans `README.md` (sections « Agent d'enrichissement », « Ce qui n'est pas couvert », « Après l'événement »).
