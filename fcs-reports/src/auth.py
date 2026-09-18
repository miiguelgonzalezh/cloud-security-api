"""Autenticacion unica contra la API de Falcon.

Se crea UN solo objeto OAuth2 por ejecucion de generate.py. Todas las Service
Classes de FalconPy se construyen con auth_object=<ese objeto>, por lo que
comparten el mismo bearer token y su renovacion automatica: no hay un login
por llamada ni por fetcher.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Type, TypeVar

from falconpy import OAuth2

from .config import Settings

LOG = logging.getLogger(__name__)

T = TypeVar("T")


class AuthenticationError(RuntimeError):
    """No fue posible obtener un token valido."""


class FalconSession:
    """Envoltorio sobre OAuth2 que entrega Service Classes ya autenticadas."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._auth: Optional[OAuth2] = None
        self._services: Dict[str, object] = {}

    # ------------------------------------------------------------------ login
    def connect(self) -> OAuth2:
        """Autentica una sola vez; llamadas posteriores reusan el token."""
        if self._auth is not None:
            return self._auth

        LOG.info("Autenticando contra Falcon (cloud=%s)", self._settings.cloud)
        auth = OAuth2(
            client_id=self._settings.client_id,
            client_secret=self._settings.client_secret,
            base_url=self._settings.cloud,
            user_agent=self._settings.user_agent,
            ssl_verify=self._settings.ssl_verify,
            timeout=self._settings.timeout,
            # Renueva el token 5 min antes de expirar, en vez de los 2 min por
            # defecto: un reporte completo puede tardar varios minutos.
            renew_window=300,
        )
        auth.login()

        if not auth.token_valid:
            reason = auth.token_fail_reason or "sin detalle"
            raise AuthenticationError(
                f"Login fallido (status={auth.token_status}): {reason}. "
                "Revisa FALCON_CLIENT_ID / FALCON_CLIENT_SECRET / FALCON_CLOUD "
                "y que la API client tenga los scopes de lectura necesarios."
            )

        LOG.info("Autenticacion correcta (status=%s)", auth.token_status)
        self._auth = auth
        return auth

    @property
    def auth_object(self) -> OAuth2:
        return self.connect()

    # --------------------------------------------------------------- services
    def service(self, service_cls: Type[T]) -> T:
        """Devuelve (y cachea) una Service Class que comparte el token vigente."""
        key = service_cls.__name__
        cached = self._services.get(key)
        if cached is None:
            LOG.debug("Instanciando Service Class %s", key)
            cached = service_cls(auth_object=self.auth_object)
            self._services[key] = cached
        return cached  # type: ignore[return-value]

    # ----------------------------------------------------------------- cierre
    def close(self) -> None:
        """Revoca el token al terminar. Nunca interrumpe la ejecucion."""
        if self._auth is None:
            return
        try:
            self._auth.logout()
            LOG.debug("Token revocado")
        except Exception as exc:  # noqa: BLE001 - el logout es best-effort
            LOG.warning("No se pudo revocar el token: %s", exc)
        finally:
            self._auth = None
            self._services.clear()

    def __enter__(self) -> "FalconSession":
        self.connect()
        return self

    def __exit__(self, *_exc_info) -> None:
        self.close()
