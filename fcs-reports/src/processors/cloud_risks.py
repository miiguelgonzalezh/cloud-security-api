"""Procesa Cloud Risks (IOMs por regla) a la forma minima de las graficas.

El endpoint devuelve una fila por regla evaluada. Los nombres exactos de los
campos han variado entre versiones del API, asi que la lectura es tolerante:
_dig() prueba varias rutas por campo y cae a 0 / "" si ninguna existe. Cuando
ningun candidato aparece se agrega una nota visible en el reporte en vez de
fallar en silencio (usa `generate.py --dump-raw` para ver la forma real).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from ..fetchers.base import FetchResult

LOG = logging.getLogger(__name__)

TOP_RULES = 10
TOP_PROVIDERS = 3  # mas alla de 3 se pliega en "Otros" (regla de la paleta)

# Orden de severidad de mayor a menor y su color semantico (paleta de estado).
SEVERITY_ORDER = ("critical", "high", "medium", "informational")
SEVERITY_LABELS = {
    "critical": "Critica",
    "high": "Alta",
    "medium": "Media",
    "informational": "Informativa",
    "unknown": "Sin clasificar",
}
SEVERITY_COLORS = {
    "critical": "sev-critical",
    "high": "sev-high",
    "medium": "sev-medium",
    "informational": "sev-info",
    "unknown": "neutral",
}
SEVERITY_ALIASES = {
    "info": "informational",
    "informative": "informational",
    "low": "informational",
    "moderate": "medium",
    "med": "medium",
    "severe": "critical",
}

PROVIDER_LABELS = {
    "aws": "AWS",
    "azure": "Azure",
    "gcp": "GCP",
    "google": "GCP",
    "oci": "OCI",
    "alibaba": "Alibaba Cloud",
}

# Rutas candidatas por campo, en orden de preferencia. Las primeras de cada
# tupla son las verificadas contra un tenant real; el resto son respaldos por
# si el endpoint cambia de forma. Estructura observada del recurso:
#   {account_id, assessed_assets, cid, cloud_provider, compliance[],
#    misconfigurations, region, rule{rule_id, rule_name}, severity, tags{}}
FIELD_PATHS = {
    "rule_id": ("rule.rule_id", "rule_id", "id"),
    "rule_name": ("rule.rule_name", "rule_name", "name", "policy_name", "title"),
    "severity": ("severity", "rule.severity", "severity_name"),
    "misconfigurations": (
        "misconfigurations",
        "misconfiguration_count",
        "open_ioms",
        "iom_count",
        "failed_assets",
        "count",
    ),
    "assessed_assets": ("assessed_assets", "assessed_asset_count", "total_assets", "resource_count"),
    "cloud_provider": ("cloud_provider", "cloud", "provider"),
    "region": ("region", "cloud_region"),
    "account_id": ("account_id", "account", "cloud_account_id"),
}


def _dig(record: dict, path: str) -> Any:
    """Lee una ruta con puntos dentro de un dict anidado."""
    current: Any = record
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def _first(record: dict, field: str) -> Any:
    for path in FIELD_PATHS[field]:
        value = _dig(record, path)
        if value not in (None, ""):
            return value
    return None


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except (ValueError, AttributeError):
            return 0
    if isinstance(value, list):
        return len(value)
    return 0


def _normalize_severity(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    key = value.strip().lower()
    key = SEVERITY_ALIASES.get(key, key)
    return key if key in SEVERITY_ORDER else "unknown"


def _normalize_provider(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "Sin dato"
    key = value.strip().lower()
    return PROVIDER_LABELS.get(key, value.strip().upper() if len(key) <= 4 else value.strip())


def _percent(part: int, whole: int) -> float:
    return round(part * 100.0 / whole, 1) if whole else 0.0


def _shorten(text: str, limit: int = 52) -> str:
    text = text or "(sin nombre)"
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _empty(error: Optional[str], notes: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "available": False,
        "error": error,
        "stats": [],
        "cards": [],
        "totals": {},
        "notes": notes or [],
    }


def process(result: FetchResult) -> Dict[str, Any]:
    """Transforma el FetchResult de cloud_risks en datos listos para graficar."""
    if not result.ok:
        return _empty(result.error or "El fetcher fallo sin detalle.")
    if not result.records:
        return _empty(None, ["La consulta no devolvio reglas con el filtro aplicado."])

    notes: List[str] = []
    if result.truncated:
        notes.append(
            f"Lectura truncada en {result.count} registros; el total reportado por la API "
            f"es {result.total_reported}."
        )

    rows: List[Dict[str, Any]] = []
    missing_misconfig = 0
    for record in result.records:
        if not isinstance(record, dict):
            continue
        raw_count = _first(record, "misconfigurations")
        if raw_count is None:
            missing_misconfig += 1
        rows.append(
            {
                "rule_id": str(_first(record, "rule_id") or ""),
                "rule_name": str(_first(record, "rule_name") or "(sin nombre)"),
                "severity": _normalize_severity(_first(record, "severity")),
                # Sin campo de conteo, cada regla cuenta como 1 hallazgo.
                "misconfigurations": _as_int(raw_count) if raw_count is not None else 1,
                "assessed_assets": _as_int(_first(record, "assessed_assets")),
                "provider": _normalize_provider(_first(record, "cloud_provider")),
                "region": str(_first(record, "region") or "—"),
                "account": str(_first(record, "account_id") or "—"),
            }
        )

    if missing_misconfig:
        notes.append(
            f"{missing_misconfig} de {len(rows)} reglas no traen conteo de misconfiguraciones; "
            "se contabilizo 1 por regla. Revisa los campos reales con --dump-raw."
        )

    total_rules = len(rows)
    total_findings = sum(r["misconfigurations"] for r in rows)
    total_assets = sum(r["assessed_assets"] for r in rows)

    # ------------------------------------------------------------- severidad
    severity_counts: Dict[str, int] = {}
    severity_rules: Dict[str, int] = {}
    for row in rows:
        severity_counts[row["severity"]] = severity_counts.get(row["severity"], 0) + row["misconfigurations"]
        severity_rules[row["severity"]] = severity_rules.get(row["severity"], 0) + 1

    # Solo niveles con hallazgos: un bucket en cero no se ve en la grafica pero
    # si ocupa renglon en la leyenda y fila en la tabla.
    severity_keys = [k for k in SEVERITY_ORDER if severity_counts.get(k)]
    if severity_counts.get("unknown"):
        severity_keys.append("unknown")
    if not severity_keys:
        # Todo en cero: mejor mostrar los buckets vacios que una grafica muda.
        severity_keys = [k for k in SEVERITY_ORDER if k in severity_counts]
        if "unknown" in severity_counts:
            severity_keys.append("unknown")

    severity_card = {
        "id": "cloud-risks-severity",
        "title": "Hallazgos por severidad",
        "subtitle": "Misconfiguraciones abiertas agrupadas por severidad de la regla.",
        "type": "bar-h",
        "value_label": "Hallazgos",
        "labels": [SEVERITY_LABELS[k] for k in severity_keys],
        "values": [severity_counts[k] for k in severity_keys],
        "colors": [SEVERITY_COLORS[k] for k in severity_keys],
        "columns": [
            {"key": "severidad", "label": "Severidad"},
            {"key": "hallazgos", "label": "Hallazgos", "numeric": True},
            {"key": "reglas", "label": "Reglas", "numeric": True},
            {"key": "porcentaje", "label": "% del total", "numeric": True, "suffix": "%"},
        ],
    }
    severity_card["rows"] = [
        {
            "severidad": SEVERITY_LABELS[k],
            "hallazgos": severity_counts[k],
            "reglas": severity_rules[k],
            "porcentaje": _percent(severity_counts[k], total_findings),
        }
        for k in severity_keys
    ]

    # ------------------------------------------------------------ top reglas
    top_rows = sorted(
        rows,
        key=lambda r: (r["misconfigurations"], r["assessed_assets"]),
        reverse=True,
    )[:TOP_RULES]

    top_card = {
        "id": "cloud-risks-top-rules",
        "title": f"Top {len(top_rows)} reglas por hallazgos",
        "subtitle": "Reglas de politica con mayor cantidad de recursos en incumplimiento.",
        "type": "bar-h",
        "value_label": "Hallazgos",
        "labels": [_shorten(r["rule_name"]) for r in top_rows],
        "values": [r["misconfigurations"] for r in top_rows],
        # Serie unica: un solo color para todas las barras (la longitud ya
        # codifica la magnitud; teñirlas por tamaño seria doble codificacion).
        "colors": ["series-1"] * len(top_rows),
        "tooltips": [r["rule_name"] for r in top_rows],
        "columns": [
            {"key": "regla", "label": "Regla"},
            {"key": "severidad", "label": "Severidad"},
            {"key": "hallazgos", "label": "Hallazgos", "numeric": True},
            {"key": "activos_evaluados", "label": "Activos evaluados", "numeric": True},
            {"key": "proveedor", "label": "Proveedor"},
            {"key": "region", "label": "Region"},
            {"key": "cuenta", "label": "Cuenta"},
        ],
    }
    top_card["rows"] = [
        {
            "regla": r["rule_name"],
            "severidad": SEVERITY_LABELS[r["severity"]],
            "hallazgos": r["misconfigurations"],
            "activos_evaluados": r["assessed_assets"],
            "proveedor": r["provider"],
            "region": r["region"],
            "cuenta": r["account"],
        }
        for r in top_rows
    ]

    # -------------------------------------------------------------- proveedor
    provider_counts: Dict[str, int] = {}
    for row in rows:
        provider_counts[row["provider"]] = provider_counts.get(row["provider"], 0) + row["misconfigurations"]

    # Igual que en severidad: fuera los proveedores sin hallazgos.
    ranked = sorted(
        ((name, value) for name, value in provider_counts.items() if value > 0),
        key=lambda kv: kv[1],
        reverse=True,
    )
    if not ranked:
        ranked = sorted(provider_counts.items(), key=lambda kv: kv[1], reverse=True)
    head = ranked[:TOP_PROVIDERS]
    tail_total = sum(value for _, value in ranked[TOP_PROVIDERS:])

    provider_labels = [name for name, _ in head]
    provider_values = [value for _, value in head]
    provider_colors = [f"series-{i + 1}" for i in range(len(head))]
    if tail_total:
        provider_labels.append("Otros")
        provider_values.append(tail_total)
        provider_colors.append("neutral")

    provider_card = {
        "id": "cloud-risks-providers",
        "title": "Distribucion por proveedor",
        "subtitle": "Participacion de cada nube en el total de hallazgos abiertos.",
        "type": "doughnut",
        "value_label": "Hallazgos",
        "labels": provider_labels,
        "values": provider_values,
        "colors": provider_colors,
        "percents": [_percent(v, total_findings) for v in provider_values],
        "columns": [
            {"key": "proveedor", "label": "Proveedor"},
            {"key": "hallazgos", "label": "Hallazgos", "numeric": True},
            {"key": "porcentaje", "label": "% del total", "numeric": True, "suffix": "%"},
        ],
    }
    provider_card["rows"] = [
        {
            "proveedor": label,
            "hallazgos": value,
            "porcentaje": _percent(value, total_findings),
        }
        for label, value in zip(provider_labels, provider_values)
    ]

    critical = severity_counts.get("critical", 0)
    high = severity_counts.get("high", 0)

    stats = [
        {
            "label": "Hallazgos abiertos",
            "value": total_findings,
            "caption": (
                f"en {total_rules} reglas evaluadas"
                + (f" · {provider_labels[0]}" if len(provider_labels) == 1 else "")
            ),
        },
        {
            "label": "Severidad critica",
            "value": critical,
            "caption": f"{_percent(critical, total_findings)}% del total",
            "tone": "sev-critical",
        },
        {
            "label": "Severidad alta",
            "value": high,
            "caption": f"{_percent(high, total_findings)}% del total",
            "tone": "sev-high",
        },
        {
            "label": "Activos evaluados",
            "value": total_assets,
            "caption": "suma por regla (un activo puede repetirse)",
        },
    ]

    return {
        "available": True,
        "error": None,
        "stats": stats,
        # Orden intencional: las vistas de distribucion (media columna) quedan
        # pareadas en la primera fila y el detalle Top N ocupa el ancho completo
        # debajo. Con un solo proveedor la dona seria una rebanada al 100%: no
        # dice nada que la tarjeta de totales no diga ya, asi que se omite (y
        # reaparece sola en cuanto haya una segunda nube).
        "cards": (
            [severity_card, provider_card, top_card]
            if len(provider_labels) > 1
            else [severity_card, top_card]
        ),
        "totals": {
            "rules": total_rules,
            "findings": total_findings,
            "assets": total_assets,
            "critical": critical,
            "high": high,
            "critical_pct": _percent(critical, total_findings),
            "high_pct": _percent(high, total_findings),
        },
        "notes": notes,
    }
