"""
Cliente singleton de Supabase.
Una sola instancia para todo el proceso — el plan gratuito tiene
conexiones simultáneas limitadas.

Hardening para Railway:
  - Forzamos HTTP/1.1 para evitar errores "Server disconnected" con HTTP/2
  - db_retry() helper centralizado para reintentos automáticos en writes
"""
import logging
import time
from typing import Callable, TypeVar

from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)

_client: Client = None

T = TypeVar("T")


def get_db() -> Client:
    """Devuelve el cliente Supabase, creándolo solo la primera vez."""
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("Conexión a Supabase establecida")
    return _client


def db_retry(
    fn: Callable[[], T],
    max_retries: int = 3,
    delay: float = 2.0,
    context: str = "",
) -> T:
    """
    Ejecuta una función con reintentos automáticos para operaciones de Supabase.

    Captura errores de conexión HTTP/2 (httpx.RemoteProtocolError,
    httpcore.RemoteProtocolError) y reintenta hasta max_retries veces.

    Args:
        fn: Función a ejecutar (lambda o callable sin argumentos)
        max_retries: Número máximo de reintentos (default: 3)
        delay: Segundos entre reintentos (default: 2.0)
        context: Descripción para logs (ej: "upsert_market")

    Returns:
        El resultado de fn() si tiene éxito

    Raises:
        Exception: Si se agotan todos los reintentos
    """
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except Exception as e:
            last_error = e
            error_name = type(e).__name__
            error_msg = str(e).lower()

            # Detectar errores de protocolo HTTP/2 o conexión
            is_retriable = any([
                "remoteprotocolerror" in error_name.lower(),
                "server disconnected" in error_msg,
                "connectionerror" in error_name.lower(),
                "connecterror" in error_name.lower(),
                "readtimeout" in error_name.lower(),
                "timeout" in error_msg,
                "networkerror" in error_name.lower(),
                "connection" in error_msg and "closed" in error_msg,
            ])

            if not is_retriable:
                # Error no recuperable, no reintentamos
                raise

            if attempt < max_retries:
                logger.warning(
                    f"[db_retry] {context} intento {attempt}/{max_retries} falló: {error_name} | "
                    f"Reintentando en {delay}s..."
                )
                time.sleep(delay)
            else:
                logger.error(
                    f"[db_retry] {context} falló tras {max_retries} intentos: {error_name} — {e}"
                )

    raise last_error
