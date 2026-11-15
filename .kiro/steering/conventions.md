# Conventions du dépôt AlphaForge AI

Socle court, lu à chaque requête. Les règles propres à un dossier sont dans `app-interface.md` et `deploy-aws.md`,
chargées seulement quand un fichier de ce dossier est en jeu. Architecture des agents : `Agents.md`.

## Travail en équipe
- Ne jamais commiter ni pousser sans demande explicite. Avant un commit : `git fetch`, `git status`, `git add` de fichiers
  nommés (jamais `git add -A`), recherche de secrets dans le diff.
- Ne jamais écraser ni réécrire un fichier qu'un autre contributeur est en train de modifier (fichier non suivi ou modifié
  récemment par quelqu'un d'autre). Incident : `explainability/models.py` d'un autre contributeur a été écrasé puis restauré.
  En cas de doute, demander ; pour ajouter une fonction, l'écrire dans son propre module.
- Avant de changer une signature, un code de raison ou un format de preuve : chercher qui en dépend (`grep` dans `alphaforge/`
  et `tests/`). Si un autre agent en dépend, ne rien changer sans accord.

## Exactitude
- Aucun chiffre écrit de mémoire : un nombre cité (tests, scores, comptes) vient d'un script ou d'un fichier de résultats.
  Incidents : README à « 551 tests » quand il y en avait 556, score Bedrock « 35/36 » périmé ; les scores viennent de
  `deploy/bench_results.json` et un test vérifie que le README les cite exactement.
- Ne jamais inventer une donnée, un seuil ou une source. Une donnée absente devient une lacune nommée.
- Ne jamais prétendre avoir vérifié ce qui ne l'a pas été : dire ce qui a été lancé et ce qui ne l'a pas été.
- Simulation uniquement : aucune transaction, aucun conseil personnalisé, aucune prédiction. Les mots « garanti », « achetez »,
  « vendez », « meilleur choix » sont refusés par les tests de l'interface.

## Tests
- Chaque changement de comportement vient avec ses tests. Lancer `python3 -m pytest -q` (toute la suite) avant de dire « fait ».
  Ne pas passer deux dossiers de tests dans une même commande : deux `conftest.py` homonymes provoquent une erreur de collecte.
- Les tests n'appellent jamais AWS ni le réseau : `conftest.py` met `ALPHAFORGE_LLM=0` et vide `ALPHAFORGE_S3_BUCKET` ;
  Bedrock, S3 et EDGAR sont remplacés par des doublures.
- La CI (`.github/workflows/tests.yml`) rejoue la suite à chaque envoi, dans l'image Python 3.9 de production.

## Python 3.9
- Pas de `X | None` dans les modèles pydantic (utiliser `Optional[X]`), pas d'antislash dans une expression d'f-string.
- Versions exactes de production : `deploy/requirements.lock.txt`.

## Langue
- Code commenté et messages en français ; l'interface existe en français et en anglais (parité des clés vérifiée par un test).
