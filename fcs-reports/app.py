"""Servicio HTTP minimo: solo lista y sirve los reportes ya generados.

Toda la logica de negocio vive en src/generate.py (proceso batch). Aqui no se
consulta la API de Falcon ni se genera nada: si el HTML no existe en disco,
este servicio no lo puede producir.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env", override=False)

# El servicio HTTP solo necesita saber donde estan los archivos: a proposito no
# carga las credenciales de Falcon, para que pueda arrancar sin ellas.
_configured = os.getenv("OUTPUT_DIR", "output/reports").strip() or "output/reports"
REPORTS_DIR: Path = Path(_configured)
if not REPORTS_DIR.is_absolute():
    REPORTS_DIR = PROJECT_ROOT / REPORTS_DIR

# Se rechaza cualquier nombre que no sea un .html plano: sin separadores de
# ruta, sin "..", sin caracteres raros.
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,120})\.html$")

app = FastAPI(
    title="FCS Reports",
    description="Entrega los reportes HTML de Falcon Cloud Security generados por lote.",
    version="0.1.0",
)


def _resolve(filename: str) -> Path:
    """Valida el nombre y confirma que el archivo queda dentro de REPORTS_DIR."""
    if not SAFE_FILENAME.match(filename):
        raise HTTPException(status_code=400, detail="Nombre de archivo invalido.")

    base = REPORTS_DIR.resolve()
    target = (base / filename).resolve()
    if target.parent != base or not target.is_file():
        raise HTTPException(status_code=404, detail="Reporte no encontrado.")
    return target


@app.get("/reports", summary="Lista los reportes generados")
def list_reports() -> Dict[str, Any]:
    if not REPORTS_DIR.is_dir():
        return {"count": 0, "reports": []}

    reports: List[Dict[str, Any]] = []
    for path in REPORTS_DIR.glob("*.html"):
        stat = path.stat()
        # Convencion de nombre: {cliente-slug}_{YYYY-MM-DD}.html
        stem_parts = path.stem.rsplit("_", 1)
        client = stem_parts[0] if len(stem_parts) == 2 else path.stem
        report_date = stem_parts[1] if len(stem_parts) == 2 else None
        reports.append(
            {
                "filename": path.name,
                "client": client,
                "report_date": report_date,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
                .isoformat(timespec="seconds"),
                "url": f"/reports/{path.name}",
            }
        )

    reports.sort(key=lambda item: item["modified_at"], reverse=True)
    return {"count": len(reports), "reports": reports}


@app.get(
    "/reports/{filename}",
    summary="Sirve un reporte HTML ya generado",
    response_class=FileResponse,
)
def get_report(filename: str) -> FileResponse:
    return FileResponse(
        _resolve(filename),
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )
