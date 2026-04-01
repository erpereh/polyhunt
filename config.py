"""
Configuración de PolyHunt — carga variables de entorno y las valida.

Solo se requieren las credenciales de Supabase en .env o Railway.
Las API keys de los proveedores LLM (Cerebras, Gemini, Groq) se
almacenan en Supabase y se gestionan desde el dashboard.
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")

# Dónde conseguir cada clave
_SOURCES = {
    "SUPABASE_URL": "Supabase Dashboard → Project Settings → API → Project URL",
    "SUPABASE_KEY": "Supabase Dashboard → Project Settings → API → anon public key",
}

_missing = [k for k in _SOURCES if not os.getenv(k)]
if _missing:
    for var in _missing:
        print(f"[ERROR] Falta variable de entorno: {var}")
        print(f"        Consíguela en: {_SOURCES[var]}")
        print(f"        Añádela al archivo .env en la raíz del proyecto\n")
    sys.exit(1)
