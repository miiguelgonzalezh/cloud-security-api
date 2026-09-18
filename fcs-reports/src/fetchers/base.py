"""Infraestructura comun a todos los fetchers.

Tres garantias que cada fetcher hereda al usar estos helpers:

1. Paginacion completa. Nunca se asume que la primera pagina trae todo: se
   sigue meta.pagination.total (modo offset) o meta.pagination.after (modo
   cursor) hasta agotar los resultados.
2. Reintentos con backoff exponencial ante 429 y 5xx (y errores de red),
   respetando Retry-After / X-RateLimit-RetryAfter cuando la API los envia.
3. Aislamiento de fallas: safe_fetch() atrapa cualquier excepcion, la registra
   y devuelve un FetchResult marcado como fallido, de modo que un dashboard
   caido no tumba el reporte completo.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

LOG = logging.getLogger(__name__)

# Estados que justifican reintentar la misma llamada.
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})

# Cortafuegos: si un endpoint nunca dice "ya no hay mas", se corta aqui.
MAX_PAGES = 1000


class FetchError(RuntimeError):
    """Falla definitiva de un fetcher tras agotar los reintentos."""

    def __init__(
        self,
        message: str,
        *,
        operation: Optional[str] = None,
        status_code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.status_code = status_code


@dataclass
class FetchResult:
    """Salida uniforme de todo fetcher; los processors solo ven esto."""

    name: str
    label: str
    ok: bool = True
    records: List[dict] = field(default_factory=list)
    pages: int = 0
    total_reported: Optional[int] = None
    truncated: bool = False
    elapsed_seconds: float = 0.0
    error: Optional[str] = None

    @property
    def count(self) -> int:
        return len(self.records)

    def summary(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "ok": self.ok,
            "records": self.count,
            "pages": self.pages,
            "total_reported": self.total_reported,
            "truncated": self.truncated,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "error": self.error,
        }


# --------------------------------------------------------------------------- #
# Lectura defensiva de la respuesta de FalconPy
# --------------------------------------------------------------------------- #
def response_status(response: Any) -> int:
    if isinstance(response, dict):
        return int(response.get("status_code") or 0)
    return int(getattr(response, "status_code", 0) or 0)


def response_body(response: Any) -> dict:
    if isinstance(response, dict):
        body = response.get("body")
        return body if isinstance(body, dict) else {}
    body = getattr(response, "body", None)
    return body if isinstance(body, dict) else {}


def response_headers(response: Any) -> dict:
    if isinstance(response, dict):
        headers = response.get("headers")
        return headers if isinstance(headers, dict) else {}
    headers = getattr(response, "headers", None)
    return headers if isinstance(headers, dict) else {}


def _header(headers: dict, name: str) -> Optional[str]:
    """Busca un header sin importar mayusculas/minusculas."""
    target = name.lower()
    for key, value in headers.items():
        if str(key).lower() == target:
            return str(value)
    return None


def error_text(body: dict) -> str:
    """Convierte body.errors[] en un mensaje legible."""
    errors = body.get("errors") or []
    parts = []
    for item in errors:
        if isinstance(item, dict):
            code = item.get("code")
            message = item.get("message") or item.get("detail") or ""
            parts.append(f"[{code}] {message}".strip())
        else:
            parts.append(str(item))
    return "; ".join(p for p in parts if p) or "sin detalle en body.errors"


def _pagination(body: dict) -> dict:
    meta = body.get("meta")
    if not isinstance(meta, dict):
        return {}
    pagination = meta.get("pagination")
    return pagination if isinstance(pagination, dict) else {}


def _resources(body: dict) -> List[dict]:
    resources = body.get("resources")
    if isinstance(resources, list):
        return resources
    # Algunos endpoints devuelven un objeto unico en vez de una lista.
    if isinstance(resources, dict):
        return [resources]
    return []


# --------------------------------------------------------------------------- #
# Reintentos
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RetryPolicy:
    """Parametros de backoff exponencial con jitter."""

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0

    def delay_for(self, attempt: int) -> float:
        """attempt es 1-based: 1s, 2s, 4s... acotado por max_delay."""
        raw = self.base_delay * (2 ** (attempt - 1))
        capped = min(raw, self.max_delay)
        return capped + random.uniform(0.0, capped * 0.25)


def _retry_after_seconds(headers: dict) -> Optional[float]:
    """Interpreta Retry-After (segundos) o X-RateLimit-RetryAfter (epoch)."""
    raw = _header(headers, "Retry-After")
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass

    raw = _header(headers, "X-RateLimit-RetryAfter")
    if raw:
        try:
            # Falcon lo envia como timestamp epoch en segundos.
            return max(0.0, float(raw) - time.time())
        except ValueError:
            pass
    return None


def call_operation(
    operation: Callable[..., Any],
    *,
    op_name: str,
    retry: RetryPolicy,
    **kwargs: Any,
) -> dict:
    """Ejecuta una operacion de FalconPy y devuelve su body, con reintentos.

    Reintenta ante 429/5xx y ante errores de transporte. Cualquier otro estado
    (400, 401, 403, 404...) se considera definitivo y se propaga como FetchError:
    reintentar un filtro FQL invalido o un scope faltante solo pierde tiempo.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            response = operation(**kwargs)
        except Exception as exc:  # noqa: BLE001 - requests/urllib3 lanzan varios tipos
            if attempt <= retry.max_retries:
                wait = retry.delay_for(attempt)
                LOG.warning(
                    "%s: error de transporte (%s). Reintento %d/%d en %.1fs",
                    op_name, exc, attempt, retry.max_retries, wait,
                )
                time.sleep(wait)
                continue
            raise FetchError(
                f"{op_name}: error de transporte tras {retry.max_retries} reintentos: {exc}",
                operation=op_name,
            ) from exc

        status = response_status(response)
        body = response_body(response)

        if 200 <= status < 300:
            return body

        if status in RETRYABLE_STATUS and attempt <= retry.max_retries:
            wait = retry.delay_for(attempt)
            suggested = _retry_after_seconds(response_headers(response))
            if suggested is not None:
                wait = min(max(wait, suggested), retry.max_delay)
            LOG.warning(
                "%s: HTTP %d. Reintento %d/%d en %.1fs",
                op_name, status, attempt, retry.max_retries, wait,
            )
            time.sleep(wait)
            continue

        raise FetchError(
            f"{op_name}: HTTP {status} - {error_text(body)}",
            operation=op_name,
            status_code=status,
        )


# --------------------------------------------------------------------------- #
# Paginacion
# --------------------------------------------------------------------------- #
def paginate_offset(
    operation: Callable[..., Any],
    *,
    op_name: str,
    retry: RetryPolicy,
    page_size: int,
    max_records: int = 0,
    **params: Any,
) -> Tuple[List[dict], int, Optional[int], bool]:
    """Recorre un endpoint paginado por offset/limit hasta agotarlo.

    Devuelve (records, paginas_leidas, total_reportado_por_la_api, truncado).
    """
    records: List[dict] = []
    offset = 0
    pages = 0
    total: Optional[int] = None
    truncated = False

    while True:
        body = call_operation(
            operation,
            op_name=op_name,
            retry=retry,
            limit=page_size,
            offset=offset,
            **params,
        )
        pages += 1
        page = _resources(body)
        pagination = _pagination(body)
        reported = pagination.get("total")
        if isinstance(reported, int):
            total = reported

        records.extend(page)
        LOG.debug(
            "%s: pagina %d -> %d registros (acumulado %d, total=%s)",
            op_name, pages, len(page), len(records), total,
        )

        if not page:
            break
        if total is not None and len(records) >= total:
            break
        if total is None and len(page) < page_size:
            # Sin total declarado: una pagina incompleta es la ultima.
            break
        if max_records and len(records) >= max_records:
            records = records[:max_records]
            truncated = True
            LOG.warning(
                "%s: truncado en %d registros por MAX_RECORDS_PER_FETCHER",
                op_name, max_records,
            )
            break
        if pages >= MAX_PAGES:
            truncated = True
            LOG.warning(
                "%s: limite de %d paginas alcanzado, se corta la lectura",
                op_name, MAX_PAGES,
            )
            break

        offset += len(page)

    return records, pages, total, truncated


def paginate_after(
    operation: Callable[..., Any],
    *,
    op_name: str,
    retry: RetryPolicy,
    page_size: int,
    max_records: int = 0,
    **params: Any,
) -> Tuple[List[dict], int, Optional[int], bool]:
    """Igual que paginate_offset pero para endpoints con cursor (after/limit)."""
    records: List[dict] = []
    pages = 0
    total: Optional[int] = None
    truncated = False
    after: Optional[str] = None
    seen_cursors = set()

    while True:
        call_params = dict(params)
        if after:
            call_params["after"] = after

        body = call_operation(
            operation,
            op_name=op_name,
            retry=retry,
            limit=page_size,
            **call_params,
        )
        pages += 1
        page = _resources(body)
        pagination = _pagination(body)
        reported = pagination.get("total")
        if isinstance(reported, int):
            total = reported

        records.extend(page)
        LOG.debug(
            "%s: pagina %d -> %d registros (acumulado %d, total=%s)",
            op_name, pages, len(page), len(records), total,
        )

        next_after = pagination.get("after") or pagination.get("next")
        if not page or not next_after or next_after in seen_cursors:
            break
        seen_cursors.add(next_after)
        after = str(next_after)

        if total is not None and len(records) >= total:
            break
        if max_records and len(records) >= max_records:
            records = records[:max_records]
            truncated = True
            LOG.warning(
                "%s: truncado en %d registros por MAX_RECORDS_PER_FETCHER",
                op_name, max_records,
            )
            break
        if pages >= MAX_PAGES:
            truncated = True
            LOG.warning(
                "%s: limite de %d paginas alcanzado, se corta la lectura",
                op_name, MAX_PAGES,
            )
            break

    return records, pages, total, truncated


# --------------------------------------------------------------------------- #
# Aislamiento de fallas
# --------------------------------------------------------------------------- #
def safe_fetch(
    fetch_fn: Callable[..., FetchResult],
    *args: Any,
    name: str,
    label: str,
    **kwargs: Any,
) -> FetchResult:
    """Ejecuta un fetcher sin dejar que su falla propague al reporte."""
    started = time.monotonic()
    try:
        result = fetch_fn(*args, **kwargs)
        result.elapsed_seconds = time.monotonic() - started
        LOG.info(
            "%s: %d registros en %d pagina(s) (%.1fs)",
            name, result.count, result.pages, result.elapsed_seconds,
        )
        return result
    except Exception as exc:  # noqa: BLE001 - el aislamiento es justamente el objetivo
        elapsed = time.monotonic() - started
        LOG.error(
            "%s: fallo definitivo tras %.1fs -> %s",
            name, elapsed, exc,
            exc_info=LOG.isEnabledFor(logging.DEBUG),
        )
        return FetchResult(
            name=name,
            label=label,
            ok=False,
            elapsed_seconds=elapsed,
            error=str(exc),
        )
