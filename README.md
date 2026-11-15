# AlphaForge AI

Analyste agentique d'aide à la décision d'investissement, **en simulation**. Elle met des données publiques en rapport avec le profil d'un investisseur, vérifie la cohérence d'une idée avec ses contraintes, et explique, preuves à l'appui, ce qui soutient ses conclusions. Elle éclaire la décision, elle ne la prend pas.

> **Simulation seulement.** Aucune transaction réelle, aucun conseil financier personnalisé, aucune prédiction de rendements. Les données historiques décrivent le passé.

Projet du Datathon 2026 (PolyFinances), cas AlphaForge AI. Les documents de référence sont `Datathon - Case Officiel 2026 FR.pdf` et `Agents.md` (architecture).

## Démarrage rapide

```bash
pip install -r alphaforge/requirements.txt          # Python 3.9 ou plus
streamlit run alphaforge/app/streamlit_app.py       # interface, http://localhost:8501
python3 -m pytest                                    # 405 tests
```

L'interface se lance depuis n'importe quel dossier. Les données du jeu de départ sont déjà dans `alphaforge_starter/` (voir plus bas pour les régénérer).

Utilisation en bref :
1. Choisir un profil (Amira, Mateo, Robert, ou en créer un avec le bouton **➕ Nouveau profil**, sous la liste des profils), la date d'analyse, la devise de base et la langue.
2. Écrire une demande (« Évalue NVDA », « Compare NVDA et BTC-USD »), ou construire un scénario de portefeuille en saisissant les poids, ou les écrire dans la demande (« 60 % SPY et 40 % AGG, sans rééquilibrage »).
3. Lire le verdict, l'explication, la sensibilité à la période, les limites. Cliquer sur un `🔎 ev_…` ouvre la preuve, avec la commande qui la reproduit à l'identique.
4. L'onglet « Comparer deux profils » met la même demande en face de deux profils. Le bouton d'export produit un rapport Markdown.

## Architecture : quatre agents, un registre de preuves

```
Demande + profil ─► Orchestrateur ─► Analyste quantitatif ─┐
                         ▲                                  ▼
                         └──── Gouvernance ◄──── REGISTRE DE PREUVES
                                    │                       │
                                    └──────► Explicabilité ◄┘ ─► Réponse expliquée
```

| Agent | Rôle | Code |
|---|---|---|
| Orchestrateur | Charge et valide le profil, interprète la demande, planifie, route, tient le registre. Ne calcule rien, ne rend aucun verdict. | `alphaforge/orchestrator/` |
| Analyste quantitatif | Produit des faits chiffrés reproductibles, sans prédiction. Outils déterministes, sans accès réseau. | `alphaforge/quant/` |
| Gouvernance | Compare les preuves aux limites **dérivées du profil** et rend un verdict visible : `conforme`, `avertissement`, `bloqué`. | `alphaforge/governance/` |
| Explicabilité | Rédige l'explication depuis le seul registre, puis vérifie chaque nombre et chaque énoncé. Un énoncé non prouvé est retiré. | `alphaforge/explainability/` |

L'interface (`alphaforge/app/`) assemble les quatre agents sur les données réelles et ne contient aucune logique métier : elle rend des vues testables (`views.py`).

Principes tenus partout :
- **Le profil est une donnée, pas du code.** Aucune règle propre à un persona : les limites (volatilité, perte maximale, plafonds par actif et par classe) sont dérivées du niveau de risque et de l'horizon.
- **Aucun nombre n'est produit par un modèle de langage.** Tous viennent d'outils déterministes ; l'explicabilité les relie à une preuve ou les retire.
- **Contraintes dures et préférences souples sont distinguées** : une exclusion bloque, une préférence oriente.
- **L'incertitude fait partie du résultat** : limites et hypothèses sont toujours affichées, jamais repliées.
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

Chaque outil renvoie une entrée de forme fixe : `evidence_id`, outil et version, fenêtre, paramètres explicites, données lues avec leur empreinte (`inputs`, SHA-256 des lignes lues jusqu'à `as_of`), nombre d'observations, avertissements, hypothèses, limites. L'`evidence_id` dépend de l'outil, de sa version, des paramètres et des données : le même appel redonne le même identifiant. Un paramètre `as_of` est accepté partout, et aucune donnée postérieure n'est lue.

Exemple de reproduction (la commande est affichée dans le tiroir de preuve) :
```python
from alphaforge.quant import DataStore, run_tool
store = DataStore.from_directory()
run_tool(store, "compute_metrics", {"ticker": "NVDA", "window": {"alias": "2022"}, "base_currency": "CAD"})
```

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

Les limites par niveau de risque (de « faible » à « élevé ») sont dans `alphaforge/governance/config.py`. Ce sont des choix de politique, pas des faits de marché, et l'explication le dit.

## Données

Jeu de départ fourni par les organisateurs, dans `alphaforge_starter/` : 31 actifs (10 actions canadiennes, 10 américaines, 9 FNB, Bitcoin, Ether), prix quotidiens du 2021-11-12 au 2026-11-12 (Yahoo Finance via `yfinance`), taux USD/CAD et séries macro (Banque du Canada, FRED). Sources, dates, traitements, limites et conditions d'utilisation : `alphaforge_starter/sources_metadata.csv` et `alphaforge_starter/README.md`.

Pour régénérer les données : `pip install yfinance` puis `python alphaforge_starter/build_dataset.py`. Les valeurs de `adj_close` des dates passées changent si on retélécharge, d'où l'empreinte des données dans chaque preuve.

Points d'attention connus, signalés par `data_quality_check` : le « fractionnement » de 1,021 de CNQ.TO (le 2022-08-22) est probablement une distribution spéciale ; ZST.TO a un jour à volume nul. Biais du survivant : l'univers a été choisi en 2026 parmi des titres aujourd'hui importants.

## Profils

Trois personas fictifs dans `alphaforge/orchestrator/profiles/` (`amira.json`, `mateo.json`, `robert.json`). Un nouveau profil se crée dans l'interface (bouton « ➕ Nouveau profil », qui ouvre le panneau « Créer ou supprimer un profil » : formulaire vierge ou copie du profil actif, préférences et exclusions ajoutées par boutons, limites de risque dérivées affichées en direct) ou en ajoutant un fichier JSON : aucun code à modifier. Le profil créé devient le profil actif.

Les profils créés dans l'interface sont enregistrés dans `alphaforge/app/profiles/user/` (ignoré par git). Garde-fous : un profil d'exemple ne peut ni être remplacé ni être supprimé ; remplacer ou supprimer un profil créé demande une confirmation explicite ; un profil incohérent (par exemple un secteur à la fois préféré et exclu) ne peut pas être enregistré.

## Tests

```bash
python3 -m pytest                  # tout
python3 -m pytest tests/quant      # un agent à la fois : quant, governance, orchestrator, explainability, app
python3 -m pytest -m smoke         # tests de fumée sur les données réelles
```

Séries synthétiques à résultat connu (série constante, doublement en un an, drawdown 100→50→100, conversion USD→CAD→USD, deux actifs identiques à 50/50, deux actifs anticorrélés), non-régression temporelle (aucun résultat ne change si on ajoute des données postérieures à `as_of`), et interface testée avec le banc d'essai de Streamlit.

## Ce qui n'est pas couvert

- **Enrichissement** (`alphaforge/enrichment/`, agent 2.1) : l'agent est appelé par l'orchestrateur quand le profil ou la demande crée un besoin (interrupteur « Enrichissement » dans l'interface ; une panne de l'agent n'arrête pas les quatre autres). Seul `EdgarProvider` (métadonnées des dépôts américains, identification SEC nom + courriel demandée dans l'onglet Sources, gardée en session seulement) est branché, avec un cache disque dans `alphaforge/app/cache/` (ignoré par git). Aucun fournisseur de cotes ESG, de fondamentaux, de nouvelles ou de composition de FNB n'est branché : ces besoins apparaissent comme des lacunes nommées. Les préférences ESG de Mateo ne sont donc pas encore vérifiables par des données ; la gouvernance le signale et nomme le substitut déclaré (le FNB filtré ESGV). Une controverse ne bloque que si elle vient d'une source de niveau A ou B ; un signal de niveau C (nouvelles) n'est qu'un avertissement. Les dépôts canadiens (SEDAR+) n'ont pas d'API publique documentée : ils restent une lacune.
- **Hébergement AWS** : non fait. L'application est lancée localement. Un client Bedrock optionnel existe pour l'interprétation des demandes (`LLMParser`), avec repli sur les règles ; il demande `boto3` et un accès au modèle.
- **Transparence des FNB** : sans la composition des fonds, un FNB est traité comme une position unique.
- **Pas de projection** : tout est descriptif ou rétrospectif.

## Structure du dépôt

```
alphaforge/
  quant/            analyste : datastore, outils, registre, fenêtres, métriques
  orchestrator/     profils, interprétation de la demande, plan, registre (ledger)
  governance/       contraintes dérivées du profil, contrôles, verdict
  explainability/   rédaction, vérificateur de chiffres, limites, étiquettes
  enrichment/       agent 2.1 : besoins, collecte, filtre as_of, faits sourcés, fiches source, écriture au registre
  app/              pipeline des agents, vues, application Streamlit
alphaforge_starter/ données de départ et script de régénération
tests/              un dossier par agent, plus l'interface
Agents.md           architecture des agents
```
