"""Déploie AlphaForge AI sur AWS, de bout en bout, sans Docker local.

    python3 deploy/deploy.py --bucket <bucket-des-archives>          # construit l'image puis déploie l'app
    python3 deploy/deploy.py --bucket <bucket> --delete              # supprime les deux piles (le bucket reste)
    ALPHAFORGE_SEC_USER_AGENT="Equipe X x@exemple.org" python3 deploy/deploy.py --bucket <bucket>   # branche SEC EDGAR

Identification SEC : lue dans ALPHAFORGE_SEC_USER_AGENT (jamais dans un fichier du dépôt). Absente lors d'une mise à jour :
la valeur déjà déployée est conservée.

Étapes : archive du code (fichiers suivis par git et non ignorés) -> S3 build/ -> pile `alphaforge-build` (ECR, CodeBuild)
-> construction de l'image (balise = empreinte de l'archive) -> pile `alphaforge-app` (ECS Fargate, ALB, CloudFront).
Identifiants : ceux de l'environnement (rôle de la machine) ; aucun secret dans le code ni dans les modèles.
"""
import argparse
import os
import hashlib
import io
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
BUILD_STACK, APP_STACK = "alphaforge-build", "alphaforge-app"


def source_zip() -> bytes:
    files = [f for f in subprocess.run(["git", "ls-files", "-z", "-co", "--exclude-standard"], cwd=ROOT, check=True,
                                       capture_output=True, text=True).stdout.split("\0") if f]  # -z : noms avec espaces intacts
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(files):
            p = ROOT / f
            if p.is_file() and not f.endswith((".pdf", ".pptx")) and not f.startswith("tests/"):
                info = zipfile.ZipInfo(f, date_time=(2026, 1, 1, 0, 0, 0))  # date fixe : archive reproductible
                info.external_attr = 0o644 << 16  # lisible dans l'image (sans cela, mode 000 : fichiers illisibles)
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, p.read_bytes())
    return buf.getvalue()


def deploy_stack(cfn, name: str, template: str, params: dict, keep: tuple = ()) -> dict:
    """`keep` : paramètres à conserver tels quels lors d'une mise à jour (UsePreviousValue) s'ils ne sont pas fournis."""
    try:
        current = {x["ParameterKey"] for x in cfn.describe_stacks(StackName=name)["Stacks"][0].get("Parameters", [])}
    except ClientError:
        current = set()
    plist = [{"ParameterKey": k, "ParameterValue": v} for k, v in params.items()]
    plist += [{"ParameterKey": k, "UsePreviousValue": True} for k in keep if k not in params and k in current]  # sinon : défaut du modèle
    kw = dict(StackName=name, TemplateBody=(ROOT / "deploy" / template).read_text(encoding="utf-8"),
              Parameters=plist,
              Capabilities=["CAPABILITY_IAM"], Tags=[{"Key": "project", "Value": "alphaforge"}])
    try:
        cfn.describe_stacks(StackName=name)
        exists = True
    except ClientError:
        exists = False
    try:
        if exists:
            cfn.update_stack(**kw)
            waiter = cfn.get_waiter("stack_update_complete")
        else:
            cfn.create_stack(OnFailure="DELETE", **kw)
            waiter = cfn.get_waiter("stack_create_complete")
        print(f"… pile {name} : {'mise à jour' if exists else 'création'} (plusieurs minutes pour CloudFront)")
        waiter.wait(StackName=name, WaiterConfig={"Delay": 15, "MaxAttempts": 160})
    except ClientError as e:
        if "No updates are to be performed" not in str(e):
            raise
        print(f"= pile {name} : déjà à jour")
    out = cfn.describe_stacks(StackName=name)["Stacks"][0].get("Outputs", [])
    return {o["OutputKey"]: o["OutputValue"] for o in out}


def build_image(cb, ecr, project: str, repo_uri: str, tag: str) -> str:
    repo = repo_uri.split("/", 1)[1]
    try:
        ecr.describe_images(repositoryName=repo, imageIds=[{"imageTag": tag}])
        print(f"= image {tag} déjà construite")
        return f"{repo_uri}:{tag}"
    except ClientError:
        pass
    build_id = cb.start_build(projectName=project, environmentVariablesOverride=[
        {"name": "IMAGE_TAG", "value": tag, "type": "PLAINTEXT"}])["build"]["id"]
    print(f"… construction CodeBuild {build_id}")
    while True:
        b = cb.batch_get_builds(ids=[build_id])["builds"][0]
        if b["buildStatus"] != "IN_PROGRESS":
            break
        time.sleep(15)
    if b["buildStatus"] != "SUCCEEDED":
        sys.exit(f"échec de la construction : {b['buildStatus']} (journaux : {b.get('logs', {}).get('deepLink')})")
    return f"{repo_uri}:{tag}"


def default_network(ec2):
    vpc = ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc]}, {"Name": "map-public-ip-on-launch", "Values": ["true"]}])
    ids = [s["SubnetId"] for s in sorted(subnets["Subnets"], key=lambda s: s["AvailabilityZone"])][:3]
    return vpc, ids


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--delete", action="store_true")
    a = ap.parse_args()
    s = boto3.session.Session(region_name=a.region)
    cfn = s.client("cloudformation")
    if a.delete:
        for name in (APP_STACK, BUILD_STACK):
            cfn.delete_stack(StackName=name)
            cfn.get_waiter("stack_delete_complete").wait(StackName=name, WaiterConfig={"Delay": 15, "MaxAttempts": 160})
            print(f"✓ pile {name} supprimée")
        return
    body = source_zip()
    tag = hashlib.sha256(body).hexdigest()[:12]
    s.client("s3").put_object(Bucket=a.bucket, Key="build/source.zip", Body=body, ServerSideEncryption="AES256")
    print(f"✓ code envoyé dans s3://{a.bucket}/build/source.zip ({len(body) // 1024} Kio, empreinte {tag})")
    b = deploy_stack(cfn, BUILD_STACK, "build-stack.yaml", {"ArtifactBucket": a.bucket})
    image = build_image(s.client("codebuild"), s.client("ecr"), b["BuildProjectName"], b["RepositoryUri"], tag)
    print(f"✓ image {image}")
    vpc, subnets = default_network(s.client("ec2"))
    params = {"ImageUri": image, "ArtifactBucket": a.bucket, "VpcId": vpc, "SubnetIds": ",".join(subnets)}
    ua = os.environ.get("ALPHAFORGE_SEC_USER_AGENT", "").strip()
    if ua:
        params["SecUserAgent"] = ua
    out = deploy_stack(cfn, APP_STACK, "app-stack.yaml", params, keep=("SecUserAgent",))
    print(f"✓ application : {out['AppUrl']}")


if __name__ == "__main__":
    main()
