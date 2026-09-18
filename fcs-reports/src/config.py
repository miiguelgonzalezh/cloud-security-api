"""Configuracion del generador, leida exclusivamente de variables de entorno.

Una sola imagen Docker sirve a todos los clientes: lo unico que cambia entre
contenedores es el .env que se monta/inyecta.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]

VALID_CLOUDS = ("us-1", "us-2", "us-3", "eu-1", "us-gov-1", "us-gov-2")
VALID_FREQUENCIES = ("daily", "weekly", "monthly")

DEFAULT_CHARTJS_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"


class ConfigError(RuntimeError):
    """La configuracion es invalida o esta incompleta."""


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    """Devuelve la variable de entorno, tratando cadenas vacias como ausentes."""
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip()
    return raw if raw else default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} debe ser un entero, se recibio {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} debe ser un numero, se recibio {raw!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "y", "on", "si")


def slugify(value: str) -> str:
    """Normaliza el nombre del cliente para usarlo en nombres de archivo."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "cliente"


def normalize_cloud(value: str) -> str:
    """Acepta us1 / US-1 / us_1 y devuelve la forma canonica us-1.

    FalconPy, ante un nombre desconocido, asume que le pasaron una URL y
    construye https://<lo-que-sea>. Validar aqui evita fallas silenciosas.
    """
    candidate = value.strip().lower().replace("_", "-")
    if candidate not in VALID_CLOUDS:
        # us1 -> us-1, usgov1 -> us-gov-1
        compact = candidate.replace("-", "")
        lookup = {c.replace("-", ""): c for c in VALID_CLOUDS}
        if compact in lookup:
            return lookup[compact]
        raise ConfigError(
            f"FALCON_CLOUD invalido: {value!r}. Valores permitidos: {', '.join(VALID_CLOUDS)}"
        )
    return candidate


@dataclass(frozen=True)
class Settings:
    """Configuracion inmutable para una ejecucion completa."""

    client_id: str
    client_secret: str
    cloud: str
    client_name: str
    report_frequency: str

    output_dir: Path
    templates_dir: Path
    static_dir: Path
    raw_dir: Path

    retention: int
    log_level: str

    request_timeout: float
    max_retries: int
    backoff_base: float
    backoff_cap: float
    ssl_verify: bool
    user_agent: str

    cloud_risks_filter: str
    cloud_risks_sort: str
    max_records_per_fetcher: int

    chartjs_url: str

    @property
    def client_slug(self) -> str:
        return slugify(self.client_name)

    @property
    def timeout(self) -> Tuple[float, float]:
        """(connect, read) para requests, como lo espera FalconPy."""
        return (min(10.0, self.request_timeout), self.request_timeout)

    def describe(self) -> dict:
        """Resumen seguro para logs: nunca incluye el secret."""
        return {
            "client_name": self.client_name,
            "client_slug": self.client_slug,
            "cloud": self.cloud,
            "client_id": f"{self.client_id[:6]}..." if self.client_id else "",
            "report_frequency": self.report_frequency,
            "output_dir": str(self.output_dir),
            "retention": self.retention,
        }


def load_settings(env_file: Optional[Path] = None) -> Settings:
    """Carga y valida la configuracion. Lanza ConfigError si falta algo critico."""
    load_dotenv(env_file or (PROJECT_ROOT / ".env"), override=False)

    client_id = _env("FALCON_CLIENT_ID")
    client_secret = _env("FALCON_CLIENT_SECRET")
    missing = [
        name
        for name, value in (
            ("FALCON_CLIENT_ID", client_id),
            ("FALCON_CLIENT_SECRET", client_secret),
        )
        if not value
    ]
    if missing:
        raise ConfigError(f"Faltan variables de entorno obligatorias: {', '.join(missing)}")

    frequency = (_env("REPORT_FREQUENCY", "weekly") or "weekly").lower()
    if frequency not in VALID_FREQUENCIES:
        raise ConfigError(
            f"REPORT_FREQUENCY invalido: {frequency!r}. Permitidos: {', '.join(VALID_FREQUENCIES)}"
        )

    output_dir = Path(_env("OUTPUT_DIR", "output/reports") or "output/reports")
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir

    return Settings(
        client_id=client_id,
        client_secret=client_secret,
        cloud=normalize_cloud(_env("FALCON_CLOUD", "us-1") or "us-1"),
        client_name=_env("CLIENT_NAME", "Cliente") or "Cliente",
        report_frequency=frequency,
        output_dir=output_dir,
        templates_dir=PROJECT_ROOT / "templates",
        static_dir=PROJECT_ROOT / "static",
        raw_dir=output_dir.parent / "raw",
        retention=max(0, _env_int("REPORT_RETENTION", 12)),
        log_level=(_env("LOG_LEVEL", "INFO") or "INFO").upper(),
        request_timeout=_env_float("REQUEST_TIMEOUT", 60.0),
        max_retries=max(0, _env_int("MAX_RETRIES", 3)),
        backoff_base=_env_float("BACKOFF_BASE", 1.0),
        backoff_cap=_env_float("BACKOFF_CAP", 30.0),
        ssl_verify=_env_bool("SSL_VERIFY", True),
        user_agent=_env("USER_AGENT", "fcs-reports/0.1.0") or "fcs-reports/0.1.0",
        cloud_risks_filter=_env("CLOUD_RISKS_FILTER", "severity:['High','Critical']") or "",
        cloud_risks_sort=_env("CLOUD_RISKS_SORT", "") or "",
        max_records_per_fetcher=max(0, _env_int("MAX_RECORDS_PER_FETCHER", 0)),
        chartjs_url=_env("CHARTJS_URL", DEFAULT_CHARTJS_URL) or DEFAULT_CHARTJS_URL,
    )
