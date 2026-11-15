# Déploiement AWS et banc d'essai (`deploy/`, `Dockerfile`, `.dockerignore`)

Ce document décrit comment l'interface d'AlphaForge AI est empaquetée, construite et hébergée sur AWS, et comment le modèle Bedrock a été choisi. Il s'adresse à une personne qui rejoint l'équipe ou à un jury technique. Le rôle de chaque service AWS dans le produit, la liste des droits et la marche à suivre après l'événement sont dans le [README racine](../README.md), sections « AWS » et « Après l'événement » : ils ne sont pas répétés ici. L'interface elle-même est décrite dans [`alphaforge/app/README.md`](../alphaforge/app/README.md).

Aucun coût n'est indiqué ici : aucun chiffre de prix n'est établi par le dépôt (le README racine donne un ordre de grandeur « non vérifié sur la grille tarifaire »).

## 1. Fichiers

| Fichier | Lignes | Rôle |
|---|---|---|
| `Dockerfile` | 27 | Image de l'interface. |
| `.dockerignore` | 10 | Fichiers exclus du contexte de construction. |
| `deploy/build-stack.yaml` | 119 | Pile de construction : dépôt ECR, projet CodeBuild. |
| `deploy/app-stack.yaml` | 267 | Pile applicative : CloudFront, équilibreur, ECS Fargate, rôles. |
| `deploy/deploy.py` | 138 | Script de déploiement de bout en bout et de suppression. |
| `deploy/bench_llm.py` | 88 | Banc d'essai de l'interprétation des demandes. |
| `deploy/bench_results.json` | 206 | Résultats du banc d'essai (relevé du 2026-11-15T05:11:08Z). |
| `deploy/requirements.lock.txt` | 7 | Versions exactes installées dans l'image. |
| `.github/workflows/tests.yml` | 40 | Intégration continue. |

## 2. Architecture déployée

```
visiteur --HTTPS--> CloudFront --HTTP, en-tête X-Origin-Verify--> ALB (port 80) --> tâche ECS Fargate (port 8501)
                                                                                      |-- Amazon Bedrock : bedrock:InvokeModel
                                                                                      |-- Amazon S3 : s3:PutObject sous runs/
```

- Le visiteur arrive en HTTPS sur la distribution CloudFront (`ViewerProtocolPolicy: redirect-to-https`). CloudFront parle en HTTP à l'équilibreur (`OriginProtocolPolicy: http-only`, port 80), car il n'y a ni nom de domaine ni certificat (commentaire du `Listener`).
- L'équilibreur d'application n'accepte que le trafic des serveurs d'origine de CloudFront (liste de préfixes gérée) et que les requêtes qui portent l'en-tête `X-Origin-Verify`. Les autres reçoivent 403.
- Une seule tâche Fargate (`DesiredCount: 1`) exécute l'image. Elle appelle Bedrock pour interpréter les demandes et écrit les registres dans S3.
- L'image est construite par CodeBuild à partir d'une archive du code déposée dans S3, puis poussée dans ECR. Aucun Docker local n'est nécessaire (`deploy.py`).
- Le bucket S3 des archives n'est créé par aucun des deux modèles : il doit exister (paramètre `ArtifactBucket`, « Bucket S3 existant »). Ses propriétés (privé, chiffré, HTTPS seulement, expiration à 60 jours) sont affirmées par le README racine et ne se vérifient pas dans ce dépôt.

## 3. L'image (`Dockerfile`, `.dockerignore`)

| Élément | Contenu |
|---|---|
| Base | `public.ecr.aws/docker/library/python:3.9-slim`, la même que le conteneur de la CI. Aucune empreinte (digest) n'est fixée. |
| Variables | `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1`, `PIP_NO_CACHE_DIR=1`, `ALPHAFORGE_HOT_RELOAD=0` (pas de rechargement à chaud en production), `STREAMLIT_BROWSER_GATHER_USAGE_STATS=false`. |
| Mises à jour de sécurité | `apt-get update && apt-get -y upgrade && apt-get -y autoremove`, puis suppression de `/var/lib/apt/lists/*`. Le commentaire du fichier explique que l'image de base Python 3.9 n'est plus reconstruite et que le scan ECR y relevait openssl, glibc et perl. |
| Dépendances | `deploy/requirements.lock.txt` est copié en `/app/requirements.txt`, puis `pip install -r`. |
| Code | `COPY alphaforge` et `COPY alphaforge_starter` (le code et les données, dont l'instantané SEC). Ni `tests/` ni `deploy/` (sauf le fichier de versions) n'entrent dans l'image. |
| Utilisateur | `chmod -R a+rX` sur le code et les données, puis `useradd --uid 10001 --create-home app` et `USER app` : le conteneur ne tourne pas en superutilisateur. |
| Dossiers d'écriture | `/app/alphaforge/app/profiles/user` (profils créés depuis l'interface) et `/app/alphaforge/app/cache` (cache d'enrichissement) sont créés et appartiennent à `app`. Le reste du code n'est pas modifiable par cet utilisateur. |
| Port | `EXPOSE 8501`. |
| Contrôle de santé | `HEALTHCHECK --interval=30s --timeout=5s --start-period=40s`, qui lit `http://127.0.0.1:8501/_stcore/health` avec `urllib` de Python. |
| Commande | `streamlit run alphaforge/app/streamlit_app.py --server.port=8501 --server.address=0.0.0.0 --server.headless=true`. La protection XSRF de Streamlit reste à sa valeur par défaut (`--server.enableXsrfProtection false` n'est utilisé que pour le développement derrière le proxy de l'éditeur). |

Le modèle de la tâche ne définit pas de `HealthCheck` de conteneur. L'état de santé que suit le service est celui du groupe cible de l'équilibreur (section 5). Je n'ai pas vérifié si ECS tient compte du `HEALTHCHECK` de l'image.

`.dockerignore` exclut : `.git`, `**/__pycache__`, `**/*.pyc`, `.pytest_cache`, `tests`, `deploy/*.zip`, `alphaforge/app/cache`, `alphaforge/app/profiles/user`, `*.pdf` et `*.pptx`. Les deux dossiers de l'application sont donc recréés vides par le `Dockerfile`, jamais copiés depuis un poste de travail.

## 4. Pile de construction (`build-stack.yaml`)

Nom de pile utilisé par `deploy.py` : `alphaforge-build`.

| Ressource | Rôle et réglages |
|---|---|
| `Repository` (`AWS::ECR::Repository`) | `ImageTagMutability: IMMUTABLE` (une balise désigne toujours la même image), `ScanOnPush: true`, chiffrement `AES256`, `EmptyOnDelete: true` (la suppression de la pile vide le dépôt), règle de cycle de vie qui expire les images au-delà de 5 (`imageCountMoreThan`, `countNumber: 5`). |
| `BuildLogs` (`AWS::Logs::LogGroup`) | `/aws/codebuild/${AWS::StackName}`, rétention `RetentionInDays: 14`. |
| `BuildRole` (`AWS::IAM::Role`) | Approuvé par `codebuild.amazonaws.com` avec la condition `aws:SourceAccount`. Droits : `logs:CreateLogStream` et `logs:PutLogEvents` sur le groupe de journaux ; `s3:GetObject` et `s3:GetObjectVersion` sur `${ArtifactBucket}/build/*` ; `ecr:GetAuthorizationToken` (ressource `*`, seule exception, car cette action n'accepte pas de ressource) ; les actions de dépôt d'image (`BatchCheckLayerAvailability`, `InitiateLayerUpload`, `UploadLayerPart`, `CompleteLayerUpload`, `PutImage`, `BatchGetImage`, `DescribeImages`) sur ce seul dépôt. |
| `BuildProject` (`AWS::CodeBuild::Project`) | Source de type S3 `${ArtifactBucket}/${SourceKey}` (`SourceKey` vaut `build/source.zip` par défaut), `TimeoutInMinutes: 20`, `LINUX_CONTAINER`, `BUILD_GENERAL1_SMALL`, image `aws/codebuild/standard:7.0`, `PrivilegedMode: true` (nécessaire pour exécuter Docker). Le `buildspec` fait : connexion à ECR (`aws ecr get-login-password`), `docker build -t "$REPO_URI:$IMAGE_TAG" .`, `docker push`. `IMAGE_TAG` vaut `replace-at-start-build` dans le modèle et est remplacé à chaque lancement par `deploy.py`. |

Sorties : `RepositoryUri` et `BuildProjectName`. Paramètres : `ArtifactBucket` et `SourceKey`.

## 5. Pile applicative (`app-stack.yaml`)

Nom de pile utilisé par `deploy.py` : `alphaforge-app`.

### Paramètres

| Paramètre | Défaut | Rôle |
|---|---|---|
| `ImageUri` | aucun | Image construite (dépôt ECR et balise). |
| `ArtifactBucket` | aucun | Bucket S3 existant des archives. |
| `ModelId` | `global.anthropic.claude-haiku-4-5-20251001-v1:0` | Profil d'inférence Bedrock, transmis à l'application (`ALPHAFORGE_LLM_MODEL`). |
| `FoundationModel` | `anthropic.claude-haiku-4-5-20251001-v1:0` | Modèle sous-jacent, pour le droit IAM. |
| `VpcId`, `SubnetIds` | aucun | Réseau. `SubnetIds` : « au moins deux sous-réseaux publics de zones différentes ». |
| `SecUserAgent` | vide | Identification exigée par la SEC (nom et courriel de l'équipe). Vide : dépôts et fondamentaux ne sont pas branchés en direct, et l'interface le dit. Ce n'est pas un secret, mais une donnée de contact : elle n'entre jamais dans le dépôt git. |
| `CloudFrontPrefixList` | `pl-3b927c52` | Liste de préfixes gérée `com.amazonaws.global.cloudfront.origin-facing`, valable pour `us-east-1` d'après la description du paramètre. |

### Ressources

| Ressource | Rôle et réglages |
|---|---|
| `AlbSecurityGroup` | Entrée TCP 80 depuis la liste de préfixes CloudFront seulement. Note du modèle : ne jamais ouvrir à `0.0.0.0/0`. |
| `TaskSecurityGroup` | Entrée TCP 8501 depuis `AlbSecurityGroup` seulement. |
| `LoadBalancer` | Application, `internet-facing`, sur `SubnetIds`. `idle_timeout.timeout_seconds` = 3600 (connexion WebSocket de Streamlit) et `routing.http.drop_invalid_header_fields.enabled` = true. |
| `TargetGroup` | Type `ip`, HTTP, port 8501. Contrôle de santé `/_stcore/health`, intervalle 15 s, 2 succès pour être sain. `deregistration_delay.timeout_seconds` = 10. |
| `Listener` | HTTP, port 80. Action par défaut : réponse fixe 403 « Forbidden ». |
| `ListenerRule` | Priorité 1. Si l'en-tête `X-Origin-Verify` vaut la valeur attendue, transfert au groupe cible. |
| `Cluster` | `containerInsights` activé. |
| `AppLogs` | Groupe `/ecs/${AWS::StackName}`, rétention 14 jours. |
| `ExecutionRole` | Approuvé par `ecs-tasks.amazonaws.com` (condition `aws:SourceAccount`). Politique gérée `AmazonECSTaskExecutionRolePolicy` : tirer l'image et écrire les journaux. |
| `TaskRole` | Droits de l'application (voir plus bas). |
| `TaskDefinition` | Fargate, `awsvpc`, `Cpu: "1024"`, `Memory: "2048"`, Linux `X86_64`, un conteneur `app` (port 8501), journaux `awslogs` avec le préfixe `app`. |
| `Service` | Fargate, `DesiredCount: 1`, `HealthCheckGracePeriodSeconds: 90`, `MinimumHealthyPercent: 100`, `MaximumPercent: 200`, `DeploymentCircuitBreaker` avec `Enable: true` et `Rollback: true`. IP publique activée : les sous-réseaux sont publics et il n'y a pas de passerelle NAT, la tâche atteint ECR, Bedrock et S3 par Internet. Dépend de `ListenerRule`. |
| `Distribution` | CloudFront, HTTP/2, `PriceClass_100`, une origine (l'équilibreur) avec `OriginReadTimeout: 60` et `OriginKeepaliveTimeout: 60`. Politique de cache gérée `Managed-CachingDisabled` (`4135ea2d-…`) : l'interface est dynamique. Politique de requête d'origine `Managed-AllViewer` (`216adef6-…`) : tous les en-têtes, dont `Host` et `Origin`, atteignent Streamlit, ce qui sert à la vérification d'origine du WebSocket (commentaire du modèle). Toutes les méthodes HTTP sont autorisées. |

Sorties : `AppUrl` (adresse HTTPS de la distribution) et `LogGroup`.

### En-tête d'origine

La valeur de `X-Origin-Verify` est le troisième segment de l'identifiant de la pile (`!Select [2, !Split ["/", !Ref AWS::StackId]]`, c'est-à-dire l'UUID). CloudFront l'ajoute à chaque requête vers l'origine (`OriginCustomHeaders`) et la règle de l'équilibreur l'exige. Sans cet en-tête, la réponse est un 403, même depuis une autre distribution CloudFront.

### Droits minimaux du rôle de tâche

Le rôle de tâche (`TaskRole`, politique `app`) contient deux instructions, sans action ni ressource générique :

| `Sid` | Action | Ressources |
|---|---|---|
| `BedrockOneModel` | `bedrock:InvokeModel` | `inference-profile/${ModelId}` dans la région et le compte de la pile, `foundation-model/${FoundationModel}` dans toute région (`bedrock:*::`) et sans région (`bedrock:::`). |
| `ArchiveRunsOnly` | `s3:PutObject` | `arn:…:s3:::${ArtifactBucket}/runs/*` |

Aucune lecture S3 et aucun autre service. Le rôle d'exécution et le rôle de construction sont distincts du rôle de tâche.

### Variables d'environnement de la tâche

| Variable | Valeur |
|---|---|
| `AWS_REGION` | Région de la pile (`!Ref AWS::Region`). |
| `ALPHAFORGE_LLM_MODEL` | Paramètre `ModelId`. |
| `ALPHAFORGE_S3_BUCKET` | Paramètre `ArtifactBucket` : active l'archive. |
| `ALPHAFORGE_SEC_USER_AGENT` | Paramètre `SecUserAgent`. |

Ce sont des variables en clair : l'identification SEC est donc lisible dans la définition de la tâche et dans les paramètres de la pile. Aucun identifiant AWS n'est défini : `boto3` utilise le rôle de la tâche. La signification de chaque variable côté code est décrite dans [`alphaforge/app/README.md`](../alphaforge/app/README.md), section « Variables d'environnement ».

## 6. Le script `deploy.py`

```bash
python3 deploy/deploy.py --bucket <bucket-des-archives>                 # région us-east-1 par défaut (--region)
ALPHAFORGE_SEC_USER_AGENT="Equipe exemple contact@exemple.org" python3 deploy/deploy.py --bucket <bucket-des-archives>
python3 deploy/deploy.py --bucket <bucket-des-archives> --delete        # supprime les deux piles, pas le bucket
```

Ce script appelle AWS : à n'exécuter que sur demande explicite, sur le compte prévu (règle de `.kiro/steering/deploy-aws.md`). Il n'a pas été lancé pour écrire ce document.

### Étapes

1. **Archive du code** (`source_zip`). La liste des fichiers vient de `git ls-files -co --exclude-standard` : fichiers suivis, plus fichiers non suivis et non ignorés (donc des modifications non validées entrent dans l'archive). Sont écartés : les fichiers qui n'existent plus, `*.pdf`, `*.pptx` et tout ce qui commence par `tests/`.
2. **Dépôt dans S3** : `build/source.zip` dans le bucket, avec `ServerSideEncryption="AES256"`. La taille en Kio et l'empreinte sont affichées.
3. **Pile `alphaforge-build`** (`deploy_stack`, paramètre `ArtifactBucket`).
4. **Image** (`build_image`). Si la balise existe déjà dans ECR (`describe_images`), la construction est sautée. Sinon `start_build` lance CodeBuild avec `IMAGE_TAG`, et le script interroge l'état toutes les 15 secondes. Un état différent de `SUCCEEDED` arrête le script avec le lien des journaux.
5. **Réseau** (`default_network`) : le VPC par défaut et jusqu'à 3 sous-réseaux avec adresse IP publique à l'ouverture, triés par zone de disponibilité. Si le VPC par défaut n'a qu'un sous-réseau public, la description du paramètre `SubnetIds` (au moins deux zones) n'est pas respectée et le script ne le vérifie pas.
6. **Pile `alphaforge-app`** avec `ImageUri`, `ArtifactBucket`, `VpcId`, `SubnetIds` et, si la variable `ALPHAFORGE_SEC_USER_AGENT` est non vide, `SecUserAgent`. L'adresse `AppUrl` s'affiche à la fin.

### Archive reproductible et permissions

Dans l'archive, chaque fichier est écrit avec une date fixe (`2026-01-01 00:00:00`), une compression `ZIP_DEFLATED`, des fichiers triés par nom et des permissions `0o644` (`external_attr = 0o644 << 16`). Sans permissions explicites, les fichiers se retrouvaient en mode 000 dans le conteneur (voir la section 11). L'empreinte (12 premiers caractères du SHA-256 de l'archive) sert de balise d'image : un même contenu donne la même balise, et `build_image` ne reconstruit pas une balise déjà présente. La balise dépend aussi des octets compressés, donc de la version de `zlib` de la machine qui construit l'archive ; que deux machines produisent la même balise pour le même code n'est pas vérifié.

### Conservation de `SecUserAgent`

`deploy_stack(..., keep=("SecUserAgent",))` : si `SecUserAgent` n'est pas fourni et que la pile existe déjà avec ce paramètre, il est passé en `UsePreviousValue`. Sinon, la valeur par défaut du modèle (vide) s'applique. Une mise à jour sans la variable n'efface donc pas l'identification déjà déployée.

### Gestion des piles

`deploy_stack` décrit la pile ; si elle existe, il appelle `update_stack`, sinon `create_stack` avec `OnFailure="DELETE"`. Les capacités sont `CAPABILITY_IAM` (les rôles n'ont pas de nom explicite), et l'étiquette `project=alphaforge` est posée. L'attente est de 160 tentatives espacées de 15 secondes, soit 2 400 secondes (40 minutes) au plus. Le message « No updates are to be performed » est traité comme « déjà à jour ».

### Suppression

`--delete` supprime `alphaforge-app`, attend la fin, puis supprime `alphaforge-build` avec la même attente. Le bucket des archives n'est pas touché : `build/source.zip` et les archives `runs/` y restent. `--bucket` reste un argument obligatoire même pour la suppression.

## 7. Banc d'essai (`bench_llm.py`, `bench_results.json`)

Le banc mesure l'interprétation des demandes (intention, actifs, signaux de périmètre), pas la qualité des analyses.

- **12 demandes** (`CASES`), en français et en anglais : noms d'entreprises, descriptions comme « l'or », demandes de prédiction, d'achat ou de conseil.
- **Score d'un passage** : 3 points par demande (intention juste, actifs justes, signaux de périmètre attendus présents), soit 36.
- **Passages** : 3 par modèle par défaut (`--repeats 3`), 1 pour les règles (déterministes). Un appel de préchauffage non compté précède les passages d'un modèle Bedrock.
- **Interpréteurs par défaut** : `rules`, `global.anthropic.claude-haiku-4-5-20251001-v1:0` et `us.amazon.nova-2-lite-v1:0`. Option `--models` pour en choisir d'autres, `--region` (défaut `us-east-1`), `--out` pour le fichier de sortie.
- **Replis** : un appel Bedrock en échec qui retombe sur les règles est compté à part (`fallbacks`) et ne compte pas comme une erreur du modèle.
- **Latence** : médiane et maximum du temps d'un appel, mesurés depuis la machine qui lance le banc (le réseau y compte).

Le fichier `bench_results.json` contient `run_at_utc`, `n_requests` et, par interpréteur, `scores`, `max`, `fallbacks`, `latency_median_s` et le détail de chaque passage (points par critère, latences, demandes manquées dans `misses`). Valeurs du relevé du 2026-11-15T05:11:08Z :

| Interpréteur | Score | Replis | Latence médiane |
|---|---|---|---|
| `rules` | 30/36 (1 passage) | 0 | 0,0 s (arrondie) |
| `global.anthropic.claude-haiku-4-5-20251001-v1:0` | 108/108 (3 passages) | 0 | 0,891 s |
| `us.amazon.nova-2-lite-v1:0` | 105/108 (3 passages) | 0 | 0,519 s |

Nova 2 Lite manque, à chaque passage, le signal `asks_transaction` de « Tout le monde parle de Tesla, je devrais en acheter ? » (le fichier ne liste que `asks_personal_advice` pour cette demande). Le README racine juge un signal de périmètre manqué comme l'erreur la plus coûteuse ici, d'où le choix de Claude Haiku 4.5 (`DEFAULT_LLM_MODEL` dans `alphaforge/app/pipeline.py`, `ModelId` dans `app-stack.yaml`).

Ces nombres sont recopiés du fichier de résultats. Ils ne sont pas testés dans ce document : le fichier de résultats fait foi.

### Test qui relie le README aux résultats

`tests/orchestrator/test_orch_bench.py` contient deux tests :

1. `test_the_rules_score_is_recomputed_not_remembered` rejoue les règles sur les 12 demandes (sans réseau) et vérifie que le score égale celui du fichier publié.
2. `test_the_readme_quotes_the_results_file` vérifie que le **README racine** contient, pour chaque interpréteur, la chaîne `total/maximum` calculée depuis le fichier (par exemple `105/108`).

Seuls les scores des modèles Bedrock ne se rejouent pas sans AWS : ils se vérifient en relançant le banc avec les modèles, ce qui appelle Bedrock et n'a pas été fait ici.

### Relancer sans réseau

```bash
python3 deploy/bench_llm.py --models rules --repeats 1 --out /tmp/bench.json
```

Sortie observée : `scores [30] / 36 · repli 0`. Sans `--out`, le fichier `deploy/bench_results.json` est écrasé, et un relevé sans les deux modèles Bedrock remplacerait celui du dépôt.

## 8. Versions figées (`requirements.lock.txt`)

L'image de production installe ces versions exactes, et la CI aussi :

```
pandas==2.3.3
numpy==2.0.2
pydantic==2.13.5
pyarrow==21.0.0
streamlit==1.50.0
boto3==1.42.97
```

L'en-tête du fichier précise qu'il s'agit des versions de la suite complète verte sous Python 3.9. Seules les dépendances directes sont figées : les dépendances de ces paquets (par exemple celles de Streamlit) sont résolues au moment de la construction. Dans l'environnement où ce document a été rédigé, `pydantic` était en version 2.12.5 (les autres paquets, dont `streamlit`, `pandas`, `numpy` et `boto3`, correspondaient au fichier) : une exécution locale peut donc différer de l'image.

## 9. Intégration continue (`.github/workflows/tests.yml`)

Déclenchement : chaque envoi (`push`), chaque demande de fusion et le lancement manuel. Droits : `contents: read`. Une exécution en cours pour la même référence est annulée quand une nouvelle démarre (`concurrency`).

Un seul travail `pytest`, limité à 20 minutes, dans le conteneur `public.ecr.aws/docker/library/python:3.9-slim` (même base que le `Dockerfile`, car Python 3.9 n'existe plus sur les machines Ubuntu récentes de GitHub, d'après le commentaire du fichier). Variables : `ALPHAFORGE_LLM="0"`, `ALPHAFORGE_S3_BUCKET=""`, `ALPHAFORGE_HOT_RELOAD="0"`, `PYTHONDONTWRITEBYTECODE="1"`. Aucun secret n'est fourni et aucun accès AWS n'est possible.

Étapes :

1. `actions/checkout@v5`.
2. Mise à jour de `pip`, puis `pip install -r deploy/requirements.lock.txt pytest==8.4.2`.
3. `python -m pytest -q` : toute la suite.
4. `pip install cfn-lint` (sans version fixée), puis `cfn-lint deploy/build-stack.yaml deploy/app-stack.yaml`.

La CI ne construit pas l'image Docker et n'exécute pas `deploy.py`.

## 10. Commandes

Sans appel AWS :

```bash
python3 -m pytest -q                                              # toute la suite
cfn-lint deploy/build-stack.yaml deploy/app-stack.yaml            # modèles CloudFormation (sans sortie : aucun problème)
python3 deploy/bench_llm.py --models rules --repeats 1 --out /tmp/bench.json
```

Avec appel AWS (sur demande explicite, compte de l'événement) : les trois commandes de `deploy.py` de la section 6.

### Exemples Python vérifiés

Ces exemples s'exécutent depuis la racine du dépôt : `cd /workshop/datathon2026 && ALPHAFORGE_LLM=0 python3 -W ignore -c "<code>"`. Ils ne font aucun appel réseau ni AWS.

**Lire les résultats publiés du banc d'essai**

```python
import json
from pathlib import Path
res = json.loads(Path("deploy/bench_results.json").read_text(encoding="utf-8"))
print(res["run_at_utc"], res["n_requests"])
for r in res["results"]:
    print(f"{r['interpreter']}: {sum(r['scores'])}/{r['max'] * len(r['scores'])}, replis {r['fallbacks']}, médiane {r['latency_median_s']} s")
```

Sortie observée :

```
2026-11-15T05:11:08Z 12
rules: 30/36, replis 0, médiane 0.0 s
global.anthropic.claude-haiku-4-5-20251001-v1:0: 108/108, replis 0, médiane 0.891 s
us.amazon.nova-2-lite-v1:0: 105/108, replis 0, médiane 0.519 s
```

**Rejouer le score des règles**

```python
import importlib.util
from pathlib import Path
from alphaforge.orchestrator import RuleBasedParser
from alphaforge.quant import DataStore

spec = importlib.util.spec_from_file_location("bench_llm", Path("deploy/bench_llm.py"))
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)
assets = DataStore.from_directory().assets.to_dict("records")
r = bench.run_pass(RuleBasedParser(), assets)
print(r["score"], r["max"], r["intent"], r["assets"], r["scope"], r["fallbacks"])
print([m["request"][:40] for m in r["misses"]])
```

Sortie observée (30 points : 10 d'intention, 9 d'actifs, 11 de périmètre) :

```
30 36 10 9 11 0
['Compare la Banque Royale et TD', "Lequel est le moins risqué entre l'or et", 'Pourquoi Shopify est-il risqué pour moi ', 'Tout le monde parle de Tesla, je devrais', 'Je veux comparer Enbridge, Fortis et le ']
```

**Vérifier l'archive du code sans la déposer**

Cet exemple importe `deploy.py` et appelle seulement `source_zip`, qui lit des fichiers locaux avec `git ls-files` (commande de lecture) et n'appelle pas AWS.

```python
import hashlib
import importlib.util
import io
import zipfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("deploy_script", Path("deploy/deploy.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
body = mod.source_zip()  # lit seulement les fichiers locaux (git ls-files), aucun appel AWS
z = zipfile.ZipFile(io.BytesIO(body))
infos = z.infolist()
print({oct(i.external_attr >> 16) for i in infos}, {i.date_time for i in infos})
print(any(i.filename.startswith("tests/") or i.filename.endswith((".pdf", ".pptx")) for i in infos))
print("Dockerfile" in z.namelist(), "deploy/requirements.lock.txt" in z.namelist())
print(hashlib.sha256(body).hexdigest() == hashlib.sha256(mod.source_zip()).hexdigest())
```

Sortie observée :

```
{'0o644'} {(2026, 1, 1, 0, 0, 0)}
False
True True
True
```

## 11. Dépannage connu

**Fichiers illisibles dans le conteneur (`Permission denied`).** Incident noté dans `.kiro/steering/deploy-aws.md` : une archive zip sans permissions a donné des fichiers illisibles dans l'image. Deux protections existent : `source_zip` écrit `0o644` pour chaque fichier, et le `Dockerfile` exécute `chmod -R a+rX` sur le code et les données. Si le symptôme revient, vérifier d'abord les modes dans l'archive avec l'exemple de la section 10.

**Scan ECR.** Le dépôt est configuré avec `ScanOnPush: true` : chaque image poussée est analysée. Après chaque nouvelle image, lire le résultat du scan. L'image de base Python 3.9 n'est plus reconstruite, d'où `apt-get upgrade` dans le `Dockerfile`. La note de `.kiro/steering/deploy-aws.md` indique que 11 failles critiques sont retombées à 0 après cette mise à jour ; ce chiffre vient de cette note et n'a pas été revérifié ici. Commande de lecture du résultat, non exécutée pour ce document : `aws ecr describe-image-scan-findings --repository-name <dépôt> --image-id imageTag=<balise>`. L'`apt-get upgrade` rend le contenu de l'image dépendant de la date de construction, mais comme les balises sont immuables, une balise donnée n'est construite qu'une fois.

**La construction échoue.** `deploy.py` s'arrête avec l'état CodeBuild et le lien des journaux. Le groupe de journaux est `/aws/codebuild/alphaforge-build`, conservé 14 jours.

**Le service ne se stabilise pas.** Le disjoncteur de déploiement (`DeploymentCircuitBreaker`, avec retour arrière) annule le déploiement. Les journaux de la tâche sont dans `/ecs/alphaforge-app` (sortie `LogGroup`), et l'état de santé vient du groupe cible (`/_stcore/health`, délai de grâce de 90 secondes).

**403 « Forbidden » en appelant l'équilibreur directement.** Comportement voulu : sans l'en-tête `X-Origin-Verify`, l'équilibreur répond 403. Utiliser l'adresse `AppUrl` de CloudFront.

**Noms de fichiers avec des espaces.** `source_zip` lit `git ls-files -z` (noms séparés par un octet nul) : un nom qui contient une espace est conservé intact. Les deux PDF et le PowerPoint, dont les noms en contiennent, sont exclus de l'archive de toute façon.

## 12. Limites

- **Une seule tâche, sans mise à l'échelle** (`DesiredCount: 1`, aucune règle d'ajustement). Les profils créés dans l'interface sont écrits dans le conteneur et disparaissent quand la tâche est remplacée.
- **Aucune authentification des visiteurs.** Toute personne qui connaît l'adresse CloudFront accède à l'application. Aucun pare-feu applicatif (WAF) ni domaine propre.
- **HTTP entre CloudFront et l'équilibreur.** L'en-tête `X-Origin-Verify` voyage donc sans chiffrement sur ce trajet. Sa valeur est dérivée de l'identifiant de la pile, que peuvent lire les personnes qui ont accès à la pile.
- **Identification SEC en clair** dans la définition de la tâche et les paramètres de la pile.
- **Bucket des archives hors modèles.** Ses réglages ne sont pas vérifiables dans le dépôt ; `deploy.py` ne supprime ni `build/source.zip` ni les archives `runs/`.
- **Identifiant de liste de préfixes propre à une région.** `CloudFrontPrefixList` a la valeur de `us-east-1` par défaut ; `--region` d'une autre région demande de la changer, et `deploy.py` ne la transmet pas.
- **Trois copies du modèle Bedrock** : `DEFAULT_LLM_MODEL` (`alphaforge/app/pipeline.py`), `ModelId` (`app-stack.yaml`) et `DEFAULT_MODELS` (`bench_llm.py`). Changer de modèle demande de les modifier ensemble, après un relevé du banc d'essai.
- **Versions** : seules les dépendances directes sont figées, `cfn-lint` n'est pas épinglé en CI, et la base de l'image n'a pas d'empreinte.
- **Rien n'a été déployé ni appelé sur AWS** pour écrire ce document. Le comportement réel de la pile (création, délais, santé de la tâche, scan ECR) n'a donc pas été observé ici. Seuls `cfn-lint` (sans erreur) et les exemples ci-dessus ont été exécutés.
