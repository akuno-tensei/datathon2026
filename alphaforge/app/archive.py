"""Archive des analyses dans Amazon S3 : registre complet (JSONL) et résumé (JSON), pour la traçabilité et le rejeu.

Activée seulement si ALPHAFORGE_S3_BUCKET est défini. Aucun identifiant AWS dans le code : boto3 lit le rôle de la machine.
Une panne de S3 n'arrête jamais l'analyse : elle devient une note visible dans l'interface.
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def ledger_jsonl(ledger) -> str:
    return "".join(json.dumps(e.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n" for e in ledger.entries())


class S3Archive:
    def __init__(self, bucket: str, region_name: str = "us-east-1", prefix: str = "runs", client: Optional[Any] = None):
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client("s3", region_name=region_name,
                                  config=Config(connect_timeout=3, read_timeout=8, retries={"max_attempts": 2, "mode": "standard"}))
        self.bucket, self.prefix, self._s3 = bucket, prefix.strip("/"), client

    def put(self, summary: Dict[str, Any], ledger) -> Dict[str, str]:
        """Écrit `ledger.jsonl` et `summary.json` sous runs/AAAA-MM-JJ/<horodatage>-<empreinte>/. Renvoie les clés écrites."""
        body = ledger_jsonl(ledger)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]  # même registre = même empreinte : rejeu vérifiable
        now = datetime.now(timezone.utc)
        base = f"{self.prefix}/{now:%Y-%m-%d}/{now:%H%M%S}-{digest}"
        summary = dict(summary, ledger_sha256_12=digest, archived_at_utc=now.isoformat(timespec="seconds"))
        keys = {"ledger": f"{base}/ledger.jsonl", "summary": f"{base}/summary.json"}
        self._s3.put_object(Bucket=self.bucket, Key=keys["ledger"], Body=body.encode("utf-8"),
                            ContentType="application/x-ndjson", ServerSideEncryption="AES256")
        self._s3.put_object(Bucket=self.bucket, Key=keys["summary"],
                            Body=json.dumps(summary, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
                            ContentType="application/json", ServerSideEncryption="AES256")
        return keys


def build_archive() -> Optional[S3Archive]:
    bucket = os.environ.get("ALPHAFORGE_S3_BUCKET", "").strip()
    if not bucket:
        return None
    try:
        return S3Archive(bucket, region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1")
    except Exception:  # boto3 absent : pas d'archive, l'analyse continue
        return None


def summarize(analysis) -> Dict[str, Any]:
    """Ce qui permet de retrouver et de rejouer l'analyse, sans données personnelles (les profils sont fictifs)."""
    req = analysis.result.request
    v = analysis.verdict or {}
    return {
        "request_text": analysis.request_text,
        "profile_id": analysis.profile.profile_id,
        "as_of": str(analysis.as_of),
        "base_currency": analysis.base_currency,
        "language": analysis.profile.language,
        "parser": req.parser if req else None,
        "intent": req.intent if req else None,
        "tickers": req.tickers if req else [],
        "status": analysis.result.status,
        "verdict_status": v.get("status"),
        "assets": [{"ticker": a["ticker"], "status": a["status"]} for a in v.get("assets", [])],
        "enrichment_enabled": analysis.enrichment_enabled,
        "n_entries": len(analysis.result.ledger),
        "n_evidence": len(analysis.result.ledger.evidence()),
        "answer": analysis.result.answer,
    }


def archive_notes(archive: Optional[S3Archive], analysis, lang: str) -> List[str]:
    if archive is None:
        return []
    try:
        keys = archive.put(summarize(analysis), analysis.result.ledger)
    except Exception as e:  # réseau, droits, quota : dit, jamais masqué
        msg = f"{type(e).__name__}: {str(e)[:120]}"
        return [f"Archive S3 indisponible ({msg}) : l'analyse n'est pas archivée." if lang == "fr"
                else f"S3 archive unavailable ({msg}): the analysis is not archived."]
    where = f"s3://{archive.bucket}/{keys['ledger'].rsplit('/', 1)[0]}/"
    return [f"Registre archivé dans Amazon S3 : {where}" if lang == "fr" else f"Ledger archived in Amazon S3: {where}"]
