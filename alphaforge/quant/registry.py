"""Registre des outils : enregistrement, schémas JSON générés, exécution validée, construction des preuves."""
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Type

from pydantic import BaseModel, ValidationError

from .datastore import DataStore
from .models import Evidence, InputRef, Notice, ResolvedWindow, ToolInputError


@dataclass
class ToolSpec:
    name: str
    version: str
    description: str
    params_model: Type[BaseModel]
    func: Callable[[DataStore, Any], Evidence]


TOOLS: Dict[str, ToolSpec] = {}

# Erreurs typées du contrat. `refusal_reason` (minuscules) reste exposé tel quel pour les lecteurs existants.
ERROR_CODES = {
    "asset_not_found": "NOT_IN_UNIVERSE",
    "insufficient_data": "INSUFFICIENT_DATA",
}


def error_code_for(reason: str) -> str:
    return ERROR_CODES.get(reason, reason.upper())


def tool(name: str, version: str, description: str, params_model: Type[BaseModel]):
    def deco(func):
        TOOLS[name] = ToolSpec(name, version, description, params_model, func)
        return func

    return deco


def tool_schemas() -> List[dict]:
    """Schémas JSON des outils, générés depuis les modèles pydantic (aucune duplication manuelle)."""
    return [
        {"name": t.name, "version": t.version, "description": t.description, "input_schema": t.params_model.model_json_schema()}
        for t in TOOLS.values()
    ]


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def make_evidence_id(tool_name: str, version: str, resolved_params: dict, fingerprint: str) -> str:
    payload = canonical_json(
        {"tool": tool_name, "version": version, "params": resolved_params, "data": fingerprint}
    )
    return "ev_" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def build_evidence(
    store: DataStore,
    tool_name: str,
    resolved_params: dict,
    as_of: date,
    *,
    window: Optional[ResolvedWindow] = None,
    value: Optional[dict] = None,
    sources: Optional[List[str]] = None,
    notices: Optional[List[Notice]] = None,
    assumptions: Optional[List[str]] = None,
    limits: Optional[List[str]] = None,
    depends_on: Optional[List[str]] = None,
    refusal_reason: Optional[str] = None,
    inputs: Optional[List[InputRef]] = None,
    n_obs: Optional[int] = None,
) -> Evidence:
    spec = TOOLS[tool_name]
    notices = notices or []
    if refusal_reason:
        status = "refused"
    elif any(n.severity == "warning" for n in notices):
        status = "warning"
    else:
        status = "ok"
    return Evidence(
        evidence_id=make_evidence_id(tool_name, spec.version, resolved_params, store.fingerprint),
        tool=tool_name,
        tool_version=spec.version,
        data_fingerprint=store.fingerprint,
        as_of=as_of,
        params=resolved_params,
        window=window,
        status=status,
        refusal_reason=refusal_reason,
        error_code=error_code_for(refusal_reason) if refusal_reason else None,
        n_obs=n_obs,
        inputs=inputs or [],
        value=value,
        sources=sources or [],
        notices=notices,
        assumptions=assumptions or [],
        limits=limits or [],
        depends_on=depends_on or [],
    )


def refused(store: DataStore, tool_name: str, params: dict, as_of: date, err: ToolInputError, **kw) -> Evidence:
    kw.setdefault("notices", [])
    kw["notices"] = list(kw["notices"]) + [Notice(code=err.reason.upper(), message=err.message)]
    return build_evidence(store, tool_name, params, as_of, refusal_reason=err.reason, **kw)


def run_tool(store: DataStore, name: str, arguments: dict) -> Evidence:
    """Point d'entrée pour l'orchestrateur ou une Lambda : valide les arguments contre le schéma, puis exécute."""
    if name not in TOOLS:
        raise KeyError(f"Outil inconnu: {name}. Disponibles: {sorted(TOOLS)}")
    spec = TOOLS[name]
    try:
        params = spec.params_model.model_validate(arguments)
    except ValidationError as e:
        raise ValueError(f"Arguments invalides pour {name}: {e}") from e
    return spec.func(store, params)
