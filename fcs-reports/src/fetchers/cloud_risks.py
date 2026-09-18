"""Cloud Risks: IOMs (misconfiguraciones) agrupados por regla.

Endpoint  : GET /cloud-security-evaluations/combined/ioms-by-rule/v1
FalconPy  : CloudSecurityDetections.get_combined_iom_by_rule
            (alias de operacion: cspm_evaluations_combined_iom_by_rule)
Paginacion: offset / limit, maximo 1000 por pagina (default de la API: 500).
Scope     : Cloud security detections - READ
"""

from __future__ import annotations

import logging

from falconpy import CloudSecurityDetections

from ..auth import FalconSession
from ..config import Settings
from .base import FetchResult, RetryPolicy, paginate_offset

LOG = logging.getLogger(__name__)

NAME = "cloud_risks"
LABEL = "Cloud Risks"
OPERATION = "cspm_evaluations_combined_iom_by_rule"

# Tope documentado por el endpoint: valores mayores se recortan a 1000.
MAX_PAGE_SIZE = 1000


def fetch(session: FalconSession, settings: Settings) -> FetchResult:
    """Trae todas las reglas con IOMs abiertos que cumplen el filtro FQL."""
    service = session.service(CloudSecurityDetections)
    retry = RetryPolicy(
        max_retries=settings.max_retries,
        base_delay=settings.backoff_base,
        max_delay=settings.backoff_cap,
    )

    params = {}
    if settings.cloud_risks_filter:
        params["filter"] = settings.cloud_risks_filter
    if settings.cloud_risks_sort:
        params["sort"] = settings.cloud_risks_sort

    LOG.info("%s: consultando %s filter=%r", NAME, OPERATION, params.get("filter", ""))

    records, pages, total, truncated = paginate_offset(
        service.get_combined_iom_by_rule,
        op_name=OPERATION,
        retry=retry,
        page_size=MAX_PAGE_SIZE,
        max_records=settings.max_records_per_fetcher,
        **params,
    )

    return FetchResult(
        name=NAME,
        label=LABEL,
        ok=True,
        records=records,
        pages=pages,
        total_reported=total,
        truncated=truncated,
    )
