"""
Cliente singleton de Supabase.
Una sola instancia para todo el proceso — el plan gratuito tiene
conexiones simultáneas limitadas.
"""
import logging
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)

_client: Client = None


def get_db() -> Client:
    """Devuelve el cliente Supabase, creándolo solo la primera vez."""
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("Conexión a Supabase establecida")
    return _client
