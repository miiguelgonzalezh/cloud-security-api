"""Orquestador batch del reporte.

Flujo: autentica una vez -> corre los fetchers -> procesa -> renderiza ->
guarda en output/reports/{cliente}_{fecha}.html -> aplica retencion.

Un fetcher que falla se registra y el reporte se genera igual, con el
dashboard correspondiente marcado como no disponible.

Uso:
    python -m src.generate
    python -m src.generate --only cloud_risks --dump-raw --log-level DEBUG
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple

from .auth import AuthenticationError, FalconSession
from .config import ConfigError, Settings, load_settings
from .fetchers import base as fetch_base
from .fetchers import cloud_risks as cloud_risks_fetcher
from .processors import cloud_risks as cloud_risks_processor
from .report_builder import ReportBuilder

LOG = logging.getLogger("fcs-reports")


class Dashboard(NamedTuple):
    """Registro de un dashboard: fetcher + processor + metadatos de la seccion."""

    name: str
    label: str
    description: str
    fetch: Callable[[FalconSession, Settings], fetch_base.FetchResult]
    process: Callable[[fetch_base.FetchResult], Dict[str, Any]]


# Los otros seis dashboards se agregan aqui, en este orden, conforme se
# implementen: cloud_assets, cloud_detections, drift, k8s_clusters,
# cloud_compliance, k8s_compliance.
DASHBOARDS: List[Dashboard] = [
    Dashboard(
        name=cloud_risks_fetcher.NAME,
        label=cloud_risks_fetcher.LABEL,
        description=(
            "Misconfiguraciones (IOM) abiertas, agrupadas por la regla de politica "
            "que las detecta."
        ),
        fetch=cloud_risks_fetcher.fetch,
        process=cloud_risks_processor.process,
    ),
]


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    # El logger de urllib3 es ruidoso en DEBUG y puede filtrar URLs con tokens.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="generate",
        description="Genera el reporte HTML de Falcon Cloud Security para un cliente.",
    )
    parser.add_argument(
        "--only",
        action="append",
        metavar="DASHBOARD",
        help="Ejecuta solo estos dashboards (repetible). Por defecto: todos.",
    )
    parser.add_argument(
        "--dump-raw",
        action="store_true",
        help="Guarda el JSON crudo de cada fetcher en output/raw/ (util para "
             "descubrir los nombres reales de los campos).",
    )
    parser.add_argument(
        "--no-retention",
        action="store_true",
        help="No elimina reportes historicos al terminar.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Consulta y procesa, pero no escribe el HTML.",
    )
    parser.add_argument("--log-level", default=None, help="DEBUG | INFO | WARNING | ERROR")
    return parser.parse_args(argv)


def dump_raw(settings: Settings, result: fetch_base.FetchResult, stamp: str) -> None:
    target_dir = settings.raw_dir / settings.client_slug
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{result.name}_{stamp}.json"
    payload = {"summary": result.summary(), "resources": result.records}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    LOG.info("%s: JSON crudo en %s", result.name, path)


def run(argv: List[str]) -> int:
    args = parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        LOG.error("Configuracion invalida: %s", exc)
        return 2

    configure_logging(args.log_level or settings.log_level)
    LOG.info("Iniciando generacion: %s", settings.describe())

    selected = DASHBOARDS
    if args.only:
        wanted = set(args.only)
        selected = [d for d in DASHBOARDS if d.name in wanted]
        unknown = wanted - {d.name for d in DASHBOARDS}
        if unknown:
            LOG.error("Dashboard(s) desconocido(s): %s", ", ".join(sorted(unknown)))
            return 2
    if not selected:
        LOG.error("No hay dashboards por ejecutar.")
        return 2

    generated_at = datetime.now(timezone.utc)
    stamp = generated_at.strftime("%Y%m%dT%H%M%SZ")

    sections: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []

    session = FalconSession(settings)
    try:
        session.connect()
    except AuthenticationError as exc:
        LOG.error("%s", exc)
        return 3

    try:
        for dashboard in selected:
            result = fetch_base.safe_fetch(
                dashboard.fetch,
                session,
                settings,
                name=dashboard.name,
                label=dashboard.label,
            )
            if args.dump_raw and result.records:
                dump_raw(settings, result, stamp)

            try:
                data = dashboard.process(result)
            except Exception as exc:  # noqa: BLE001 - un processor roto tampoco tumba el reporte
                LOG.error("%s: el processor fallo -> %s", dashboard.name, exc, exc_info=True)
                data = {
                    "available": False,
                    "error": f"Error procesando los datos: {exc}",
                    "stats": [],
                    "cards": [],
                    "totals": {},
                    "notes": [],
                }

            sections.append(
                {
                    "id": dashboard.name,
                    "title": dashboard.label,
                    "description": dashboard.description,
                    "data": data,
                }
            )
            diagnostics.append(result.summary())
    finally:
        session.close()

    ok_count = sum(1 for d in diagnostics if d["ok"])
    LOG.info("Fetchers completados: %d/%d", ok_count, len(diagnostics))

    builder = ReportBuilder(settings)
    context = builder.build_context(sections, diagnostics, generated_at)
    html = builder.render(context)

    if args.dry_run:
        LOG.info("--dry-run: no se escribio el archivo (%d KB renderizados)", len(html) // 1024)
        return 0 if ok_count else 1

    path = builder.save(html, generated_at)
    if not args.no_retention:
        builder.enforce_retention()

    print(path)
    # Exit 1 si ningun dashboard trajo datos: util para que el cron alerte.
    return 0 if ok_count else 1


def main() -> None:
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
