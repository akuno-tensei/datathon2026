# AlphaForge AI

Analyste agentique d'aide à la décision d'investissement, **en simulation**. Elle met des données publiques en rapport avec le profil d'un investisseur, vérifie la cohérence d'une idée avec ses contraintes, et explique, preuves à l'appui, ce qui soutient ses conclusions. Elle éclaire la décision, elle ne la prend pas.

> **Simulation seulement.** Aucune transaction réelle, aucun conseil financier personnalisé, aucune prédiction de rendements. Les données historiques décrivent le passé.

Projet du Datathon 2026 (PolyFinances), cas AlphaForge AI. Les documents de référence sont `Datathon - Case Officiel 2026 FR.pdf` et `Agents.md` (architecture, avec l'état de l'implémentation en fin de document).

**Démo hébergée** (compte AWS de l'événement, actif jusqu'au lundi 16 novembre 2026 à 13 h 59) : https://d3rh9mi9cqh6mn.cloudfront.net/

Après la fermeture du compte, l'application reste utilisable en local et sur un hébergement sans AWS : voir [Après l'événement](#après-lévénement).

## Démarrage rapide

```bash
pip install -r requirements.txt pytest              # versions exactes, testées sous Python 3.9 et 3.12
streamlit run alphaforge/app/streamlit_app.py       # interface, http://localhost:8501
python3 -m pytest                                    # toute la suite de tests (aussi lancée à chaque envoi par la CI)
```

L'interface se lance depuis n'importe quel dossier et fonctionne hors ligne : les données du jeu de départ sont dans `alphaforge_starter/`, et un instantané des données SEC dans `alphaforge_starter/enrichment_snapshot/`. Sans compte AWS, la demande est interprétée par des règles (voir [Après l'événement](#après-lévénement)).

Utilisation en bref :
1. Choisir un profil (Amira, Mateo, Robert, ou en créer un avec le bouton **➕ Ajouter**, sous la liste des profils ; **✏️ Modifier** change le profil créé sélectionné ; **🗑 Supprimer** retire un profil créé), la date d'analyse, la devise de base et la langue. L'interrupteur **Enrichissement** active l'agent d'enrichissement (activé par défaut) ; la ligne à côté dit quelles sources sont branchées.
2. Écrire une demande (« Évalue NVDA », « Compare NVDA et BTC-USD »), ou construire un scénario de portefeuille en saisissant les poids, ou les écrire dans la demande (« 60 % SPY et 40 % AGG, sans rééquilibrage »).
3. Lire le verdict, l'explication, la sensibilité à la période, les limites. Chaque bouton **Preuve** ouvre le calcul qui soutient un chiffre, avec la commande qui le reproduit à l'identique (l'identifiant complet apparaît au survol). Le panneau **📖 Lexique** explique les termes de l'analyse.
4. L'onglet « Comparer deux profils » met la même demande en face de deux profils. L'onglet « Données et sources » liste les sources, leurs conditions, les fiches source de l'enrichissement, et accepte l'identification SEC. Le bouton d'export produit un rapport Markdown.

## Documentation détaillée

Ce README donne la vue d'ensemble. Chaque partie a sa propre documentation, écrite à partir du code et avec des exemples exécutés :

| Partie | Document |
|---|---|
| Carte du paquet, déroulé d'une analyse, qui écrit et qui lit le registre | [`alphaforge/README.md`](alphaforge/README.md) |
| Orchestrateur (profil, interprétation de la demande, plan, registre) | [`alphaforge/orchestrator/README.md`](alphaforge/orchestrator/README.md) |
| Analyste quantitatif (outils, fenêtres, métriques, contrat d'une preuve) | [`alphaforge/quant/README.md`](alphaforge/quant/README.md) |
| Enrichissement (besoins, sources, niveaux de confiance, SEC EDGAR, instantané) | [`alphaforge/enrichment/README.md`](alphaforge/enrichment/README.md) |
| Gouvernance (limites dérivées, contrôles, codes de raison, verdict, alternatives) | [`alphaforge/governance/README.md`](alphaforge/governance/README.md) |
| Explicabilité (rédaction, vérificateur, provenance, formats des nombres) | [`alphaforge/explainability/README.md`](alphaforge/explainability/README.md) |
| Interface et assemblage (pipeline, vues, Streamlit, libellés, archive S3) | [`alphaforge/app/README.md`](alphaforge/app/README.md) |
| Déploiement AWS, image, banc d'essai Bedrock, CI | [`deploy/README.md`](deploy/README.md) |
| Intégration AWS : schémas, justification, bonnes pratiques, preuves observables | [`docs/architecture-aws.md`](docs/architecture-aws.md) |
| Validation des résultats et des explications : recalcul indépendant, reproductibilité, banc d'essai, limites | [`docs/validation.md`](docs/validation.md) |
| Suite de tests (lancement, doublures, contenu de chaque dossier) | [`tests/README.md`](tests/README.md) |
| Données de départ et instantané SEC | [`alphaforge_starter/README.md`](alphaforge_starter/README.md), [`alphaforge_starter/enrichment_snapshot/SOURCE.md`](alphaforge_starter/enrichment_snapshot/SOURCE.md) |

## Valeur pour une institution financière

L'argument central est la **traçabilité** : chaque conclusion se rejoue à l'identique et se rattache à ses preuves.
- **Auditabilité.** Chaque chiffre affiché porte un identifiant de preuve, l'empreinte des données lues et la commande qui le recalcule. Le registre complet d'une analyse a une empreinte SHA-256 stable : la même analyse redonne la même empreinte ([`docs/validation.md`](docs/validation.md)). Un contrôleur peut donc vérifier après coup ce qui a été dit, sur quelles données et selon quelles règles.
- **Conformité.** Les refus sont explicites et codés (transaction, prédiction, conseil personnalisé, exclusion déclarée) ; aucun nombre n'est produit par un modèle de langage ; aucune donnée postérieure à la date d'analyse n'est lue. Les seuils de risque sont des paramètres de politique nommés, regroupés dans un seul fichier (`alphaforge/governance/config.py`) que la conformité peut relire, sans règle propre à un client.
- **Confiance du client.** Le client voit pourquoi une idée est jugée cohérente ou non avec son profil, avec les limites et les incertitudes, dans sa langue et en termes simples. La décision lui revient, et l'outil le dit.
- **Qui l'utilise, et ce que l'outil n'est pas.** L'utilisateur est l'investisseur (ici fictif) qui veut comprendre une idée d'investissement au regard de son profil. Il peut s'en servir seul, ou avec la personne qui l'accompagne, comme support factuel commun. L'outil **n'est pas un conseiller** : il présente des faits (mesures passées, données sourcées, écarts aux limites du profil) et ne dit jamais quoi acheter, vendre ou garder. Ses statuts (conforme, avertissement, bloqué) décrivent la cohérence avec un profil fictif selon des règles documentées ; ce n'est pas une évaluation de convenance au sens réglementaire, et l'écran le dit sous chaque verdict. La décision, et la responsabilité qui va avec, restent à l'humain. Tout se passe en simulation : l'outil n'exécute rien.

## Architecture : un noyau de quatre agents, un agent d'enrichissement, un registre de preuves

```
Demande + profil ─► Orchestrateur ─► (Enrichissement) ─► Analyste quantitatif ─┐
                         ▲                                                      ▼
                         └──────────── Gouvernance ◄────────── REGISTRE DE PREUVES
                                            │                                   │
                                            └────────────► Explicabilité ◄──────┘ ─► Réponse expliquée
```

| Agent | Rôle | Code |
|---|---|---|
| Orchestrateur | Charge et valide le profil, interprète la demande (Amazon Bedrock, revérifié par des règles ; règles seules sans AWS), planifie, route, tient le registre. Ne calcule rien, ne rend aucun verdict. | `alphaforge/orchestrator/` |
| Enrichissement | Va chercher les données publiques absentes du jeu de départ quand un besoin est détecté, les date, leur donne un niveau de confiance (A, B, C) et les écrit au registre avec leur fiche source. Ne conclut rien. Désactivable : le noyau à quatre agents travaille alors seul et le dit. | `alphaforge/enrichment/` |
| Analyste quantitatif | Produit des faits chiffrés reproductibles, sans prédiction. Outils déterministes, sans accès réseau. | `alphaforge/quant/` |
| Gouvernance | Compare les preuves aux limites **dérivées du profil** et rend un verdict visible : `conforme`, `avertissement`, `bloqué`. Peut demander à l'analyste de chiffrer des alternatives (2 itérations au plus). | `alphaforge/governance/` |
| Explicabilité | Rédige l'explication depuis le seul registre, puis la vérifie (chiffres, ton, provenance de chaque mot). Un énoncé non prouvé est retiré. | `alphaforge/explainability/` |

L'interface (`alphaforge/app/`) assemble les agents sur les données réelles et ne contient aucune logique métier : elle rend des vues testables (`views.py`).

Principes tenus partout :
- **Le profil est une donnée, pas du code.** Aucune règle propre à un persona : les limites (volatilité, perte maximale, plafonds par actif et par classe) sont dérivées du niveau de risque et de l'horizon.
- **Aucun nombre n'est produit par un modèle de langage.** Bedrock ne fait qu'interpréter la demande ; tous les chiffres viennent d'outils déterministes ou de sources officielles, et l'explicabilité les relie à une preuve ou les retire.
- **Contraintes dures et préférences souples sont distinguées** : une exclusion bloque, une préférence oriente.
- **Bloquer seulement sur une règle explicite, avertir sur un écart chiffré.** Le mandat demande de vérifier, de signaler les conflits et de rendre les contrôles visibles, sans exiger de blocage. Le statut `bloqué` est réservé à un refus déclaré par l'investisseur (exclusion, controverse documentée sur un thème exclu), à une classe d'actifs que son niveau de risque n'autorise pas (la crypto pour un profil prudent) et à une limite du défi (aucune transaction réelle). Un dépassement de volatilité ou de perte maximale reste un avertissement, chiffré, avec des alternatives. Sauf pour un profil contradictoire, l'analyse complète est toujours affichée. Justification et tests : [`alphaforge/governance/README.md`](alphaforge/governance/README.md), section 7.5.
- **L'incertitude fait partie du résultat** : limites et incertitudes sont toujours affichées ; les hypothèses de calcul sont dans un panneau repliable, toujours accessible.
- **Aucune prédiction** : un scénario de portefeuille est un rejeu historique, étiqueté « scénario rétrospectif fictif ».

### Outils de l'analyste

| Outil | Rôle |
|---|---|
| `asset_lookup` | Confirme qu'un actif existe (symbole ou nom libre), renvoie ses métadonnées et sa couverture. Hors univers : `NOT_IN_UNIVERSE` avec les plus proches. |
| `get_prices` | Série de prix sur une fenêtre explicite, dans la devise native. Renvoie un `series_id`, jamais la série. |
| `data_quality_check` | Couverture, trous, valeurs invalides, sauts, fractionnements suspects, début tardif. Un statut `insuffisant` bloque les calculs suivants. Il signale, il ne corrige jamais. |
| `convert_currency` | CAD/USD au taux du même jour, avant le calcul des rendements. Report borné, jamais de taux futur. Refuse une double conversion. |
| `compute_metrics` | Rendement cumulatif et annualisé, volatilité, drawdown (sommet, creux, récupération), pire jour, pire mois, rendements par année civile. Effet de change rapporté. Sharpe optionnel. |
| `stress_window` | Rejoue une mesure sur plusieurs fenêtres (complet, 2022, hors 2022, 1 an, 3 ans) et dit si elle est stable ou sensible, avec le seuil affiché. |
| `correlation_matrix` | Corrélations des rendements, avec la méthode d'alignement des calendriers et le nombre d'observations communes. |
| `portfolio_metrics` | Rejeu historique d'une allocation fictive : indicateurs, concentration (HHI, nombre effectif), répartition, contribution au risque, ratio de diversification. |

### Le registre de preuves

Chaque outil de l'analyste renvoie une entrée de forme fixe : `evidence_id`, outil et version, fenêtre, paramètres explicites, données lues avec leur empreinte (`inputs`, SHA-256 des lignes lues jusqu'à `as_of`), nombre d'observations, avertissements, hypothèses, limites. L'`evidence_id` dépend de l'outil, de sa version, des paramètres et des données : le même appel redonne le même identifiant. Un paramètre `as_of` est accepté partout, et aucune donnée postérieure n'est lue. Les données de l'enrichissement y sont écrites avec leur date de publication, leur niveau de confiance et le lien vers leur fiche source.

Exemple de reproduction (la commande est affichée dans le tiroir de preuve) :
```python
from alphaforge.quant import DataStore, run_tool
store = DataStore.from_directory()
run_tool(store, "compute_metrics", {"ticker": "NVDA", "window": {"alias": "2022"}, "base_currency": "CAD"})
```

### Vérification de l'explication

Avant affichage, chaque énoncé passe par `claim_verifier` (`alphaforge/explainability/verifier.py`) :
1. chaque référence existe dans le registre, et un énoncé « fait » en cite au moins une ;
2. chaque chiffre se retrouve dans la preuve citée, arrondi tel qu'affiché ; il ne reste aucun autre nombre dans le texte ;
3. aucune tournure de conseil, de prédiction ou de promesse (liste de mots interdits ; un mot cité entre guillemets est une mention, pas un conseil) ;
4. **provenance** (`provenance.py`) : chaque mot, chaque enchaînement et chaque citation vient d'un gabarit de rédaction ou du registre, sans le texte libre de la demande. C'est une liste blanche : elle attrape une recommandation déguisée, une faute de frappe ou un caractère d'apparence identique, que la liste de mots interdits laisse passer.

Un énoncé fautif est retiré et l'explication est rédigée à nouveau sans lui (2 tentatives au plus). La bannière dit si tout a été vérifié.

**Synthèse** (`alphaforge/explainability/synthesis.py`). Juste après le verdict, quelques phrases étiquetées « Synthèse » relient ce verdict à ses raisons, par exemple : « NVDA dépasse des limites de risque de votre profil : volatilité annualisée de 61,3 % pour une limite de 15,0 % ; perte maximale de 63,5 % pour une limite de 20,0 %. », puis « Ces mesures décrivent le passé et ne prédisent pas l'avenir. La décision revient à l'investisseur. » (Robert, « Évalue NVDA »). Elle constate, elle ne prescrit rien. Elle est rédigée par gabarits après la vérification du corps, puis passe les mêmes contrôles, avec une règle de plus : **aucun fait nouveau**, chaque chiffre doit déjà figurer, avec la même preuve, dans un énoncé vérifié du corps. Un énoncé de synthèse fautif ou sans preuve est retiré, jamais rétrogradé ; sans verdict, il n'y a pas de synthèse. Détail : [`alphaforge/explainability/README.md`](alphaforge/explainability/README.md), section 5.9.

**Conclusion en langage simple** (`alphaforge/explainability/conclusion.py`). Chaque analyse (évaluer un actif, comparer des actifs, simuler un portefeuille) se termine par « En résumé » : ce que dit le verdict, sans chiffre et sans jargon, pour un débutant. Par exemple, pour Robert et NVDA : « En bref : l'idée n'est pas exclue, mais certains points demandent de la prudence avant de décider. » puis « NVDA : son prix a varié plus fortement que ce que ce profil accepte ; il a déjà connu une baisse plus forte que ce que ce profil accepte. C'est un signal de prudence, pas une interdiction. » Elle passe les mêmes contrôles que la synthèse. L'onglet « Comparer deux profils » a aussi sa conclusion. Détail : section 5.10 du même document.

## Agent d'enrichissement

Il s'active quand un **besoin de donnée** dépasse le jeu de départ : un thème du profil (ESG, éviter les controverses), un mot de la demande (« résultats », « dette », « controverses », « nouvelles »…), ou, pour une action américaine demandée, ses fondamentaux de contexte. Chaque donnée passe par un filtre temporel obligatoire (rien de publié après la date d'analyse), reçoit un niveau de confiance (A : source officielle ; B : fournisseur tiers avec méthodologie ; C : texte ou nouvelle, jamais utilisé dans un calcul) et une fiche source. Une source qui échoue devient une lacune nommée, jamais une valeur supposée. Une panne de l'agent n'arrête pas les quatre autres.

| Besoin | Source | État |
|---|---|---|
| Dépôts réglementaires | SEC EDGAR (niveau A) : métadonnées des derniers dépôts (formulaire, dates, lien), pas leur texte | Branché pour les actions américaines |
| Fondamentaux | SEC EDGAR, données XBRL « companyfacts » (niveau A) : chiffre d'affaires, bénéfice net, flux de trésorerie d'exploitation, trésorerie, dette à long terme, capitaux propres, actif total, tous tirés du dernier rapport annuel déposé avant la date d'analyse ; un indicateur absent du rapport est omis | Branché pour les actions américaines |
| Cotes ESG et controverses, nouvelles, composition des FNB | — | Aucune source : lacune nommée |

**Fondamentaux de contexte.** Toute demande sur une action américaine affiche ses fondamentaux, sans mot-clé. Ils n'ont aucun effet sur le verdict : aucune règle de la gouvernance ne les utilise et aucun seuil n'est inventé, et l'explication le dit. Les alternatives proposées par la gouvernance n'en reçoivent pas, pour garder l'explication lisible. Un profil débutant en voit trois montants dans l'explication (chiffre d'affaires, bénéfice net, dette à long terme), les autres les sept ; le tableau complet est toujours affiché. Si la source ne répond pas, rien n'est inventé et le verdict ne bouge pas : l'indisponibilité est notée dans la trace des agents. Pour une autre classe d'actifs, les fondamentaux ne sont recherchés que si la demande les évoque (et deviennent alors une lacune explicite).

**Identification SEC.** La SEC exige que chaque programme qui l'interroge se présente avec un nom et un courriel de contact, et limite le débit à 10 requêtes par seconde (le code attend au moins 0,15 s entre deux requêtes, soit moins de 7 par seconde, et lit la liste des sociétés une seule fois). L'identification se saisit dans l'onglet « Données et sources » (gardée dans la session seulement) ou dans la variable `ALPHAFORGE_SEC_USER_AGENT` ; elle n'est écrite dans aucun fichier du dépôt. Chaîne des sources : SEC en direct, puis le cache local `alphaforge/app/cache/` (ignoré par git), puis l'instantané versionné `alphaforge_starter/enrichment_snapshot/` (données arrêtées au 12 novembre 2026, voir son `SOURCE.md`), affiché comme tel.

**Règles de blocage.** Une controverse ne bloque une exclusion stricte que si elle vient d'une source de niveau A ou B ; un signal de niveau C n'est qu'un avertissement.

**À quoi servent les fondamentaux s'ils ne changent pas le verdict ?** Ils donnent le contexte que les prix ne montrent pas, pour que l'investisseur juge lui-même. Pour Robert (objectif de préservation du capital), la dette à long terme d'une entreprise se lit à côté de son flux de trésorerie d'exploitation ; pour un profil de croissance, le chiffre d'affaires et le bénéfice net. Aucun seuil n'est défini sur ces montants, et en inventer un serait un conseil déguisé : c'est pourquoi ils n'entrent pas dans le verdict. Ils sont datés, sourcés au niveau A et reproductibles comme les autres preuves.

### Critères non financiers (Mateo)

Mateo tient à l'ESG et veut éviter les controverses. Le jeu de départ ne contient aucune cote ESG, et aucune source publique gratuite et documentée n'a été branchée. L'outil traite ce cas en disant ce qu'il ne sait pas, plutôt qu'en devinant :

| Critère | Ce que fait l'outil | Effet |
|---|---|---|
| Exclusion d'un secteur, d'une classe d'actifs ou d'un titre | Comparée à la colonne `sector` ou `asset_class` du jeu de départ, ou au symbole | **Bloqué** (`EXCLUDED_SECTOR`, `EXCLUDED_ASSET_CLASS`, `EXCLUDED_TICKER`) : calculable, donc bloquant légitime |
| Préférence ESG | Aucune donnée : « l'alignement n'est pas vérifié » (`THEME_DATA_MISSING`) ; le FNB ESGV est le substitut déclaré, avec sa limite : c'est un fonds filtré par son émetteur, pas une cote | Avertissement, jamais une conclusion |
| Éviter les controverses | Aucune source : lacune nommée dans l'enrichissement et dans les limites | Avertissement ; le blocage n'existe que pour une controverse de niveau A ou B |

Exemple vérifié : Mateo tel que livré n'exclut aucun secteur, donc XOM sort en avertissement (volatilité au-dessus de sa limite, données ESG absentes). Un profil créé par copie de Mateo avec l'exclusion du secteur « Energy » bloque XOM et CNQ.TO (`EXCLUDED_SECTOR`). Le profil livré n'est pas modifié : le cas ne lui attribue pas cette exclusion.

## Décisions de calcul

| Sujet | Règle |
|---|---|
| Rendements | Sur `adj_close` (dividendes et fractionnements inclus), rendements simples. |
| Annualisation | 252 pour actions, FNB et or ; 365 pour la crypto ; 52 pour tout ce qui mélange crypto et actions. La base est nommée dans chaque résultat. Rendement annualisé seulement si la période couvre au moins 350 jours. |
| Devises | Prix convertis chaque jour avec le taux USD/CAD de la Banque du Canada du même jour, **avant** les rendements. Report vers l'avant seulement, borné à 5 jours civils, chaque report compté. CAD par défaut, champ du profil. |
| Calendriers | Actif seul : ses propres jours. Corrélations : dates communes, jamais de report (il fabriquerait des rendements nuls). Crypto et actions mélangées : hebdomadaire de référence. |
| Fenêtre « hors 2022 » | On retire les **rendements** datés de 2022, pas les prix : aucun faux rendement n'enjambe le trou. |
| Sensibilité | « Sensible » si l'écart relatif entre fenêtres dépasse 25 %, sauf si l'écart absolu reste sous 2 points (une mesure presque nulle n'est pas sensible). |
| Portefeuille | Poids strictement positifs de somme 1, aucune vente à découvert, hypothèse de rééquilibrage obligatoire, aucun frais. Sans poids demandés, pondération égale **déclarée comme hypothèse**. |
| Fondamentaux | Repris tels quels du rapport annuel, en dollars US, affichés en milliards (ou millions) avec une décimale ; une perte se dit par le libellé (« perte nette »), jamais par un signe. |

Les limites par niveau de risque (de « faible » à « élevé ») sont dans `alphaforge/governance/config.py`. Ce sont des choix de politique, pas des faits de marché, et l'explication le dit.

## Données

Jeu de départ fourni par les organisateurs, dans `alphaforge_starter/` : 31 actifs (10 actions canadiennes, 10 américaines, 9 FNB, Bitcoin, Ether), prix quotidiens du 2021-11-12 au 2026-11-12 (Yahoo Finance via `yfinance`), taux USD/CAD et séries macro (Banque du Canada, FRED). Sources, dates, traitements, limites et conditions d'utilisation : `alphaforge_starter/sources_metadata.csv` et `alphaforge_starter/README.md`.

Pour régénérer les données : `pip install yfinance` puis `python alphaforge_starter/build_dataset.py`. Les valeurs de `adj_close` des dates passées changent si on retélécharge, d'où l'empreinte des données dans chaque preuve.

Ajout de l'équipe : `alphaforge_starter/enrichment_snapshot/`, instantané SEC EDGAR (dépôts et fondamentaux des 10 actions américaines, arrêté au 12 novembre 2026, environ 160 Ko), avec son `SOURCE.md` et son script `alphaforge_starter/build_sec_snapshot.py`. XOM n'y a pas de fondamentaux : la SEC associe aujourd'hui ce symbole à un nouveau déclarant, qui n'a encore déposé aucun rapport annuel.

Points d'attention connus, signalés par `data_quality_check` : le « fractionnement » de 1,021 de CNQ.TO (le 2022-08-22) est probablement une distribution spéciale ; ZST.TO a un jour à volume nul. Biais du survivant : l'univers a été choisi en 2026 parmi des titres aujourd'hui importants.

## Profils

Trois personas fictifs dans `alphaforge/orchestrator/profiles/` (`amira.json`, `mateo.json`, `robert.json`). Un nouveau profil se crée dans l'interface (bouton « ➕ Ajouter » sous la liste des profils, qui ouvre le formulaire sous le bandeau : formulaire vierge ou copie du profil actif, préférences et exclusions ajoutées par boutons, limites de risque dérivées affichées en direct) ou en ajoutant un fichier JSON : aucun code à modifier. Le profil créé devient le profil actif.

Les profils créés dans l'interface sont enregistrés dans `alphaforge/app/profiles/user/` (ignoré par git). Un profil créé se modifie avec **✏️ Modifier** (le profil sélectionné) : le formulaire reprend tous ses champs, l'identifiant reste fixe, et l'enregistrement remplace le profil, qui reste actif ; l'analyse suivante applique les nouvelles valeurs. Garde-fous : un profil d'exemple ne peut être ni modifié, ni remplacé, ni supprimé (le bouton est désactivé et le dit) ; remplacer un profil créé depuis « Ajouter », ou le supprimer, demande une confirmation explicite ; un profil incohérent (par exemple un secteur à la fois préféré et exclu) ne peut pas être enregistré.

## Interface

- **Bilingue** (français, anglais) : chaque texte visible a sa version dans les deux langues, vérifiée par un test.
- **Lisible par un débutant** : les codes internes sont traduits à l'affichage seulement (« faible_modere » devient « Faible à modéré », « fenêtre « 2022 » » devient « en 2022 », les dates sont écrites « 12 nov. 2021 »). Les valeurs enregistrées et les messages des agents ne changent pas, et un test vérifie que les nombres restent identiques. Le panneau **📖 Lexique** explique 16 termes avec un exemple.
- **Trace en direct** : chaque étape des agents s'affiche pendant l'analyse, puis reste consultable.
- **Décisions des agents** : en tête de la trace, les choix faits pendant l'analyse et leur raison. Exemple réel (Robert, « Évalue NVDA ») : « besoin de données détecté (fondamentaux · NVDA) → l'enrichissement est sollicité » ; « NVDA (avertissement) → demande à l'analyste d'évaluer d'autres actifs de l'univers, à titre de comparaison : AAPL (avertissement), CSU.TO (avertissement), MSFT (avertissement), SHOP.TO (avertissement) (itération 1/2). Chacun reçoit son propre statut ; ce n'est pas une recommandation. » ; « 69 énoncé(s) vérifié(s), aucun retiré ». Le bloc dit aussi quand l'enrichissement n'est pas sollicité, quand un signal hors périmètre est transmis à la gouvernance, et quand le vérificateur retire des énoncés et fait rédiger à nouveau l'explication.
- **Aucune couleur seule** : chaque statut est aussi écrit, et chaque barre dit la part de la limite utilisée et la marge en points (« marge : 3,7 points sous la limite »). Une note sous le verdict rappelle que les limites sont des choix de politique documentés, donc des hypothèses.

## Tests

```bash
python3 -m pytest                  # tout
python3 -m pytest tests/quant      # un dossier à la fois : quant, governance, orchestrator, explainability, enrichment, integration, app
python3 -m pytest -m smoke         # tests de fumée sur les données réelles
```

Séries synthétiques à résultat connu (série constante, doublement en un an, drawdown 100→50→100, conversion USD→CAD→USD, deux actifs identiques à 50/50, deux actifs anticorrélés), non-régression temporelle (aucun résultat ne change si on ajoute des données postérieures à `as_of`), enrichissement de bout en bout avec des sources simulées (`tests/integration/`), et interface testée avec le banc d'essai de Streamlit. Les tests n'appellent jamais AWS, la SEC ni le réseau : Bedrock, S3 et EDGAR sont remplacés par des doublures.

La CI (`.github/workflows/tests.yml`, GitHub Actions) rejoue toute la suite à chaque envoi et à chaque demande de fusion, dans l'image Python 3.9 de production, et vérifie les modèles CloudFormation avec `cfn-lint`.

## AWS

AWS intervient à trois endroits du flux, chacun avec un rôle précis :

| Service | Rôle dans la solution | Si le service est indisponible |
|---|---|---|
| **Amazon Bedrock** (Claude Haiku 4.5, profil d'inférence global) | L'orchestrateur lui fait interpréter la demande en langage naturel : intention, actifs désignés (« l'or » → GLD), signaux de périmètre (prédiction, transaction, conseil). Le modèle propose, le code dispose : actifs revérifiés contre l'univers, poids lus dans le texte d'origine, signaux de périmètre jamais retirés. Le modèle ne calcule rien et ne recommande rien. | Repli sur l'interprétation par règles, affiché sous le verdict. |
| **Amazon S3** | Chaque analyse archive son registre complet (`ledger.jsonl`) et un résumé (`summary.json`) sous `runs/AAAA-MM-JJ/<heure>-<empreinte>/`. L'empreinte SHA-256 du registre figure dans le chemin : une analyse rejouée doit donner la même. Bucket privé, chiffré, HTTPS seulement, expiration à 60 jours. | Note « archive indisponible » ; l'analyse est quand même affichée. |
| **Amazon ECS Fargate, ALB, CloudFront, ECR, CodeBuild** | Hébergement de l'interface : CloudFront (HTTPS) → équilibreur de charge joignable seulement depuis CloudFront, avec un en-tête d'origine → une tâche Fargate. L'image est construite par CodeBuild (pas de Docker requis sur le poste) et scannée à l'envoi dans ECR. | — |

**Choix du modèle.** Banc d'essai `deploy/bench_llm.py` : 12 demandes réalistes (FR et EN, noms d'entreprises, descriptions comme « l'or », demandes de prédiction, d'achat et de conseil), 3 points par demande (intention, actifs, signaux de périmètre), 3 passages par modèle. Résultats dans `deploy/bench_results.json` (relevé du 15 novembre 2026) : Claude Haiku 4.5 obtient 108/108 en environ 0,9 s par demande ; Amazon Nova 2 Lite obtient 105/108 en 0,5 s, car il manque à chaque passage le signal « transaction » de « Tout le monde parle de Tesla, je devrais en acheter ? ». Un signal de périmètre manqué est l'erreur la plus coûteuse ici, d'où Haiku 4.5. L'interprétation par règles seule obtient 30/36 (un passage : elle est déterministe).

**Lire ces scores sur la même base.** Un passage vaut 12 demandes × 3 contrôles = 36 points. Les modèles Bedrock sont passés 3 fois (3 × 36 = 108 points possibles), parce que leur réponse peut varier d'un appel à l'autre ; les règles une seule fois, parce qu'elles redonnent toujours la même réponse. Par passage : Claude Haiku 4.5 obtient 36/36 à chacun des 3 passages, Nova 2 Lite 35/36 à chacun, les règles 30/36. **Douze demandes forment un petit échantillon** : ces scores départagent les interpréteurs sur des cas typiques, ils ne mesurent pas un taux d'erreur général. Un test vérifie que ces chiffres sont ceux du fichier de résultats et que le score des règles se recalcule à l'identique ; relancer `python3 deploy/bench_llm.py` remet le fichier à jour.

**Droits (moindre privilège).** Le rôle de la tâche ne peut qu'appeler ce seul modèle (`bedrock:InvokeModel`) et écrire sous `runs/` (`s3:PutObject`) : aucune lecture S3, aucun autre service. Aucun identifiant AWS dans le code : boto3 utilise le rôle de la machine ou de la tâche. L'identification SEC de la version hébergée est une variable d'environnement de la tâche (paramètre `SecUserAgent`), jamais un fichier du dépôt.

**Déployer ou supprimer** (modèles CloudFormation dans `deploy/`, vérifiés par `cfn-lint`) :

```bash
python3 deploy/deploy.py --bucket <bucket-des-archives>           # archive du code -> CodeBuild -> ECR -> ECS, ALB, CloudFront
ALPHAFORGE_SEC_USER_AGENT="Nom contact@exemple.org" python3 deploy/deploy.py --bucket <bucket-des-archives>   # idem, SEC branchée
python3 deploy/deploy.py --bucket <bucket-des-archives> --delete  # supprime les deux piles ; le bucket des archives reste
```

Sans `ALPHAFORGE_SEC_USER_AGENT`, une mise à jour conserve l'identification déjà déployée.

**En local**, Bedrock et S3 sont activés par variables d'environnement : `ALPHAFORGE_LLM=0` désactive Bedrock (les tests le font), `ALPHAFORGE_LLM_MODEL` change de modèle, `ALPHAFORGE_S3_BUCKET` active l'archive. Sans identifiants AWS, Bedrock n'est pas tenté.

## Après l'événement

Le compte AWS de l'événement ferme le 16 novembre 2026 à 13 h 59, avec la démo hébergée, Bedrock et l'archive S3. Le projet reste utilisable :

| Élément | Sans compte AWS |
|---|---|
| Démo hébergée (https://d3rh9mi9cqh6mn.cloudfront.net/) | **Ne répond plus** : ECS, l'équilibreur, CloudFront et ECR disparaissent. Il faut lancer l'application en local ou l'héberger ailleurs (ci-dessous). |
| Prototype local (`streamlit run alphaforge/app/streamlit_app.py`) | Fonctionne hors ligne : données des 31 actifs dans `alphaforge_starter/`. Les cinq agents, les calculs, la gouvernance, les explications vérifiées, la conclusion « En résumé », les profils (ajout, modification, suppression, comparaison) et les deux langues sont inchangés. |
| Interprétation des demandes | Par règles, détecté automatiquement (aucun identifiant AWS : Bedrock n'est pas tenté) et affiché sous le verdict. Les règles comprennent moins de formulations : 30/36 au banc d'essai, contre 108/108 pour Claude Haiku 4.5 (`deploy/bench_results.json`). Par exemple, « l'or » n'est pas relié à GLD. |
| Archive des registres | Absente ; aucune analyse n'est archivée et les archives déjà écrites disparaissent avec le bucket. L'export Markdown de l'interface reste disponible. |
| Preuves de l'intégration AWS | Plus de console : seules les captures faites avant la fermeture en témoignent. |
| SEC EDGAR (dépôts, fondamentaux) | Gratuit et indépendant d'AWS : en direct avec une identification SEC, sinon l'instantané versionné. |
| Tests et CI | Inchangés : ils n'utilisent jamais AWS. |

**Héberger sans AWS (Streamlit Community Cloud, gratuit)** : l'application tourne telle quelle, avec l'interprétation par règles et l'instantané SEC.
1. Sur https://share.streamlit.io, se connecter avec le compte GitHub qui a accès au dépôt, puis « Create app ».
2. Dépôt `akuno-tensei/datathon2026`, branche `main`, fichier `alphaforge/app/streamlit_app.py`.
3. « Advanced settings » : Python 3.12 (suite complète vérifiée sous 3.12). Les dépendances viennent de `requirements.txt` à la racine.
4. Facultatif, pour la SEC en direct : dans « Secrets », ajouter `ALPHAFORGE_SEC_USER_AGENT = "Nom de l'équipe contact@exemple.org"`.

Différence connue sans Bedrock : une question comme « je devrais en acheter ? » est refusée comme demande de conseil personnalisé (avertissement), et non comme demande de transaction (bloqué) ; les deux refusent de conseiller. Un ordre explicite (« Achète 10 actions AAPL ») reste bloqué, et une demande de prédiction reste hors périmètre.

**Vérifié le 15 novembre 2026** : application lancée en local sans aucun accès AWS (ni identifiants, ni rôle, ni bucket, ni identification SEC). Parcours dans le navigateur : 12 vérifications sur 13 passent, la seule différence étant la question « je devrais en acheter ? » ci-dessus ; création, modification, analyse et suppression d'un profil : toutes passent. Les fondamentaux SEC s'affichent depuis l'instantané, au niveau A.

**Redéployer sur un autre compte AWS** : `python3 deploy/deploy.py --bucket <bucket>` recrée tout (modèles CloudFormation dans `deploy/`) ; ajouter `ALPHAFORGE_SEC_USER_AGENT="Nom contact@exemple.org"` devant la commande pour brancher la SEC. Coût indicatif, non vérifié sur la grille tarifaire : quelques dollars par jour pour Fargate et l'équilibreur de charge, plus une fraction de cent par demande Bedrock. `--delete` supprime les deux piles.

**Mettre à jour l'instantané SEC** : `ALPHAFORGE_SEC_USER_AGENT="Nom contact@exemple.org" python3 alphaforge_starter/build_sec_snapshot.py` ; l'identification n'est écrite dans aucun fichier.

## Ce qui n'est pas couvert

- **Cotes ESG, controverses, nouvelles, composition des FNB** : aucune source branchée. Ces besoins apparaissent comme des lacunes nommées. Les préférences ESG de Mateo ne sont donc pas vérifiables par des données ; la gouvernance le signale et nomme le substitut déclaré (le FNB filtré ESGV). Conséquence : la règle qui bloque sur une controverse documentée (niveau A ou B) est en place et testée, mais ne peut pas se déclencher tant qu'aucune source de controverses n'est branchée. Voir [Critères non financiers (Mateo)](#critères-non-financiers-mateo).
- **Titres canadiens** : pas de dépôts ni de fondamentaux (SEDAR+ n'a pas d'API publique documentée). Extension prévue : un fournisseur SEDAR+ s'ajouterait comme une source de plus, sans changer les agents (même contrat de fournisseur que SEC EDGAR).
- **Fondamentaux dans les calculs** : l'analyste ne les utilise pas (aucun ratio, aucun seuil) ; ils restent un contexte affiché.
- **Transparence des FNB** : sans la composition des fonds, un FNB est traité comme une position unique.
- **Actifs hors univers** : signalés comme tels, sans collecte de leurs prix.
- **Pas de projection** : tout est descriptif ou rétrospectif.

## Structure du dépôt

```
alphaforge/
  quant/            analyste : datastore, outils, registre, fenêtres, métriques
  orchestrator/     profils, interprétation de la demande (règles, Bedrock), plan, registre (ledger)
  governance/       contraintes dérivées du profil, contrôles, verdict
  explainability/   rédaction, vérificateur (chiffres, ton, provenance de chaque mot), limites, étiquettes
  enrichment/       besoins, collecte (SEC EDGAR), filtre as_of, faits sourcés, fiches source, écriture au registre
  app/              pipeline des agents, vues, libellés, archive S3, application Streamlit
alphaforge_starter/ données de départ et script de régénération ; instantané SEC (enrichment_snapshot/) et son script
tests/              un dossier par agent, l'enrichissement de bout en bout (integration/) et l'interface (app/)
deploy/             modèles CloudFormation, script de déploiement, banc d'essai Bedrock (Dockerfile à la racine)
.github/workflows/  CI : tests et cfn-lint à chaque envoi
.kiro/steering/     conventions de travail pour les assistants de code (socle + règles par dossier)
Agents.md           architecture des agents et état de l'implémentation
```
