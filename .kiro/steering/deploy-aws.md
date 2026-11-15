---
inclusion: fileMatch
fileMatchPattern: ["deploy/**", "Dockerfile", ".dockerignore", "alphaforge/app/archive.py", "alphaforge/orchestrator/request.py"]
---
# Règles AWS et déploiement

S'applique aux modèles CloudFormation, au Dockerfile et au code qui appelle AWS. Ne s'applique pas aux tests, qui n'appellent
jamais AWS.

- Le compte AWS est celui de l'événement : il reste actif pendant l'évaluation puis est supprimé. Créer, modifier ou supprimer
  une ressource payante seulement sur demande explicite.
- Moindre privilège : le rôle de la tâche ne fait que `bedrock:InvokeModel` sur un seul modèle et `s3:PutObject` sous `runs/`.
  Aucune action ni ressource générique, aucune entrée `0.0.0.0/0` (l'équilibreur n'accepte que CloudFront).
- Aucun identifiant ni secret dans le code, les modèles ou les journaux : boto3 utilise le rôle de la machine ou de la tâche.
- Tout appel AWS a un délai court et un nombre de tentatives borné ; une panne devient un repli affiché (règles, note
  « archive indisponible »), jamais un arrêt de l'analyse.
- Valider avec `cfn-lint deploy/build-stack.yaml deploy/app-stack.yaml` avant tout déploiement (aussi lancé par la CI).
- Déployer avec `python3 deploy/deploy.py --bucket <bucket>` ; l'archive du code doit garder des fichiers lisibles.
  Incident : une archive zip sans permissions a donné « Permission denied » dans le conteneur.
- Après chaque nouvelle image, lire le scan ECR : l'image de base Python 3.9 n'est plus reconstruite, d'où la mise à jour des
  paquets Debian dans le Dockerfile (11 failles critiques ramenées à 0).
- Changer de modèle Bedrock seulement après `python3 deploy/bench_llm.py` ; le README cite `deploy/bench_results.json`.
