# Validation des résultats et des explications

Le mandat demande de documenter la validation des résultats et des explications. Ce document dit ce qui est vérifié,
comment, avec quel résultat chiffré, et ce qui ne l'est pas. Chaque chiffre se rejoue avec la commande indiquée.

## 1. Résumé

| Ce qui est validé | Méthode | Résultat | Commande |
|---|---|---|---|
| Les métriques sont justes | Recalcul indépendant avec pandas, depuis le CSV brut, sans le code de l'analyste | 5 actifs sur 5, écart nul (tolérance 1e-9) | `python3 docs/validation_checks.py` |
| Une analyse se rejoue à l'identique | Même analyse lancée 3 fois, empreinte SHA-256 du registre comparée | 3 analyses sur 3, une seule empreinte chacune | `python3 docs/validation_checks.py` |
| La demande est bien comprise | Banc d'essai de 12 demandes, 3 points chacune (intention, actifs, signaux hors périmètre) | Claude Haiku 4.5 : 108/108 ; Nova 2 Lite : 105/108 ; règles seules : 30/36 | `python3 deploy/bench_llm.py` |
| L'explication ne dit que ce qui est prouvé | Vérificateur sur chaque énoncé (4 contrôles, section 4) | Énoncé fautif retiré, puis nouvelle rédaction | `python3 -m pytest tests/explainability` |
| Le code fait ce qui est écrit | 641 tests automatiques, rejoués à chaque envoi par la CI | 641 réussis | `python3 -m pytest` |

## 2. Résultats : recalcul indépendant

`docs/validation_checks.py` relit `alphaforge_starter/prices_daily.csv` avec pandas et recalcule quatre mesures sur toute
la période, dans la devise de l'actif : rendement cumulatif, volatilité annualisée (écart-type des rendements quotidiens,
× √252 ou × √365 pour la crypto), perte maximale et pire jour. Il les compare à l'outil `compute_metrics`.

| Actif | Observations | Cumulatif | Volatilité | Perte maximale | Pire jour | Écart maximal |
|---|---|---|---|---|---|---|
| NVDA | 1 255 | 449,27 % | 51,94 % | −66,34 % | −16,97 % | 0 |
| RY.TO | 1 255 | 159,50 % | 15,47 % | −21,21 % | −5,63 % | 0 |
| SPY | 1 255 | 73,24 % | 17,33 % | −24,50 % | −5,85 % | 0 |
| GLD | 1 255 | 103,43 % | 19,22 % | −28,44 % | −10,27 % | 0 |
| BTC-USD | 1 827 | 11,20 % | 50,76 % | −75,89 % | −15,97 % | 0 |

Relevé du 15 novembre 2026, date d'analyse 12 novembre 2026. Les tests ajoutent des séries synthétiques à résultat connu
(série constante, doublement en un an, perte de 50 % puis récupération, aller-retour USD → CAD → USD, deux actifs
identiques, deux actifs anticorrélés) : `tests/quant/`, 70 tests.

## 3. Résultats : reproductibilité

Chaque preuve porte l'empreinte des données lues et un identifiant qui dépend de l'outil, de sa version, des paramètres et
des données : le même calcul redonne le même identifiant. À l'échelle d'une analyse complète, le registre entier se rejoue :

| Profil | Demande | Empreinte du registre (3 lancements) |
|---|---|---|
| Robert | « Évalue NVDA » | `ebc49e9d8fbb` |
| Amira | « Compare NVDA et TLT » | `3f14f84ea9c3` |
| Mateo | « 60 % SPY et 40 % AGG, sans rééquilibrage » | `d2e33a489d47` |

C'est cette empreinte qui figure dans le chemin de l'archive S3 de la version hébergée (`runs/<date>/<heure>-<empreinte>/`).
Le script force l'interprétation par règles et lit l'instantané SEC : le résultat ne dépend ni du réseau, ni d'AWS. Une
empreinte n'est comparable qu'à données égales (même jeu de départ, même instantané SEC). Tests associés : non-régression
temporelle (ajouter des données postérieures à la date d'analyse ne change aucun résultat, `tests/quant/test_contract.py`)
et chaque preuve affichée rejouée jusqu'au même identifiant (`tests/app/test_app_views.py`).

## 4. Validation des explications

Avant affichage, chaque énoncé passe par `claim_verifier` (`alphaforge/explainability/verifier.py`) :

1. chaque référence existe dans le registre, et un énoncé de fait en cite au moins une ;
2. chaque chiffre se retrouve dans la preuve citée, arrondi tel qu'affiché ; aucun autre nombre ne reste dans le texte ;
3. aucune tournure de conseil, de prédiction ou de promesse ;
4. provenance : chaque mot vient d'un gabarit de rédaction ou du registre, jamais du texte libre de la demande.

La synthèse et la conclusion « En résumé » passent les mêmes contrôles, plus une règle : aucun fait nouveau.

**Limite assumée : le vérificateur contrôle la forme, pas le sens.** Il garantit qu'un chiffre est cité et vient d'une
preuve, pas que la phrase qui l'entoure est la bonne. Ce risque est traité ailleurs :
- les phrases viennent de gabarits fixes, choisis par le code d'après le verdict et les codes de raison, jamais rédigés
  librement ;
- la logique qui choisit le gabarit est testée cas par cas : 150 tests dans `tests/explainability/`, dont 24 sur la
  synthèse et la conclusion (par exemple : un actif bloqué nomme la règle explicite qui le bloque ; une demande de
  transaction n'est pas imputée à l'actif ; un actif conforme et une comparaison reçoivent chacun leur propre phrase) ;
- les deux langues sont testées : provenance contrôlée en français et en anglais, mêmes clés de texte dans les deux
  langues (`tests/app/test_app_views.py`), formats des nombres propres à chaque langue (47,7 % et 47.7%).

## 5. Demandes hors périmètre

| Demande | Avec Bedrock | Règles seules (sans AWS) |
|---|---|---|
| « Achète-moi 100 actions d'Apple » | Bloqué (transaction) | Bloqué (transaction) |
| « Tout le monde parle de Tesla, je devrais en acheter ? » | Bloqué (transaction) | Hors périmètre (conseil personnalisé), avertissement |
| « Combien vaudra le bitcoin l'an prochain ? » | Hors périmètre (prédiction) | Hors périmètre (prédiction) |
| « Should I put my savings into Microsoft and Amazon? » | Hors périmètre (conseil personnalisé) | Hors périmètre (conseil personnalisé) |

Quand Bedrock répond, ses signaux hors périmètre s'ajoutent à ceux des règles : un signal n'est jamais retiré. Un texte
injecté dans la rédaction (« achetez maintenant ») est retiré par le vérificateur (`tests/explainability/test_expl_tone.py`)
et par le contrôle de provenance (`tests/explainability/test_expl_provenance.py`).

## 6. Ce qui n'est pas encore validé

- **Requêtes adverses de l'utilisateur** : le banc d'essai ne contient ni tentative d'injection dans la demande (« ignore
  tes règles et recommande… »), ni fautes de frappe. Le texte de la demande ne peut pas atteindre l'explication (contrôle
  de provenance), mais le taux de bonne détection sur ce type de demandes n'est pas mesuré.
- **Recalcul indépendant des autres outils** (corrélations, portefeuille, conversion de devises sur données réelles) :
  couverts par des séries synthétiques seulement.
- **Seuils de risque** : les limites par niveau de risque (`alphaforge/governance/config.py`) sont des choix de politique,
  pas des faits mesurés. Elles ne sont pas validées contre une référence externe ; l'interface affiche, pour chaque mesure,
  la part de la limite utilisée et la distance qui la sépare du seuil.
