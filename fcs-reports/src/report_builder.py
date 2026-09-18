"""Renderiza el reporte HTML y administra los archivos generados.

El HTML resultante es autocontenido: el CSS se incrusta en el propio archivo,
de modo que un reporte se puede archivar, adjuntar por correo o abrir sin el
servicio FastAPI. La unica dependencia externa es Chart.js por CDN.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from .config import Settings

LOG = logging.getLogger(__name__)

TEMPLATE_NAME = "report.html"

FREQUENCY_LABELS = {
    "daily": "Diario",
    "weekly": "Semanal",
    "monthly": "Mensual",
}
FREQUENCY_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}


class ReportBuilder:
    """Arma el contexto, renderiza la plantilla y escribe el archivo."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._env = Environment(
            loader=FileSystemLoader(str(settings.templates_dir)),
            autoescape=select_autoescape(["html", "xml"]),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._env.filters["miles"] = _thousands

    # ---------------------------------------------------------------- contexto
    def build_context(
        self,
        sections: List[Dict[str, Any]],
        diagnostics: List[Dict[str, Any]],
        generated_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        settings = self._settings
        now = generated_at or datetime.now(timezone.utc)
        days = FREQUENCY_DAYS.get(settings.report_frequency, 7)
        period_start = now - timedelta(days=days)

        failures = [d for d in diagnostics if not d.get("ok")]

        return {
            "client": {"name": settings.client_name, "slug": settings.client_slug},
            "report": {
                "title": f"Falcon Cloud Security - Reporte {FREQUENCY_LABELS.get(settings.report_frequency, '')}".strip(),
                "frequency": settings.report_frequency,
                "frequency_label": FREQUENCY_LABELS.get(settings.report_frequency, settings.report_frequency),
                "generated_at": now.isoformat(timespec="seconds"),
                "generated_at_human": now.strftime("%d/%m/%Y %H:%M UTC"),
                "period_label": (
                    f"{period_start.strftime('%d/%m/%Y')} - {now.strftime('%d/%m/%Y')}"
                ),
                "cloud": settings.cloud,
            },
            "sections": sections,
            "diagnostics": diagnostics,
            "failures": failures,
            "chartjs_url": settings.chartjs_url,
            "stylesheet": self._read_stylesheet(),
        }

    def _read_stylesheet(self) -> str:
        css_path = self._settings.static_dir / "style.css"
        try:
            return css_path.read_text(encoding="utf-8")
        except OSError as exc:
            LOG.warning("No se pudo leer %s: %s. El reporte saldra sin estilos.", css_path, exc)
            return ""

    # --------------------------------------------------------------- renderizado
    def render(self, context: Dict[str, Any]) -> str:
        template = self._env.get_template(TEMPLATE_NAME)
        return template.render(**context)

    # ------------------------------------------------------------------ archivo
    def output_path(self, generated_at: Optional[datetime] = None) -> Path:
        now = generated_at or datetime.now(timezone.utc)
        name = f"{self._settings.client_slug}_{now.strftime('%Y-%m-%d')}.html"
        return self._settings.output_dir / name

    def save(self, html: str, generated_at: Optional[datetime] = None) -> Path:
        path = self.output_path(generated_at)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        LOG.info("Reporte escrito en %s (%.1f KB)", path, path.stat().st_size / 1024)
        return path

    # ----------------------------------------------------------------- retencion
    def enforce_retention(self) -> List[Path]:
        """Conserva los N reportes mas recientes de ESTE cliente y borra el resto.

        La retencion es por cliente: cada contenedor limpia unicamente sus
        propios archivos, aunque compartan volumen de salida.
        """
        keep = self._settings.retention
        if keep <= 0:
            return []

        pattern = f"{self._settings.client_slug}_*.html"
        existing = sorted(
            self._settings.output_dir.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        removed: List[Path] = []
        for stale in existing[keep:]:
            try:
                stale.unlink()
                removed.append(stale)
                LOG.info("Retencion: eliminado %s", stale.name)
            except OSError as exc:
                LOG.warning("Retencion: no se pudo eliminar %s: %s", stale.name, exc)
        return removed


def _thousands(value: Any) -> str:
    """Formatea numeros con separador de miles es-MX (coma).

    Debe coincidir con el Intl.NumberFormat("es-MX") que usan las graficas,
    para que el mismo numero no se vea distinto en la tarjeta y en el eje.
    Los valores no numericos pasan intactos.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.1f}"
    return f"{int(value):,}"
