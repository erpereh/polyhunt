"""
Configuración de PolyHunt — carga variables de entorno y las valida.
Si falta alguna clave, indica dónde conseguirla y termina el proceso.
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

# Dónde conseguir cada clave
_SOURCES = {
    "SUPABASE_URL":   "Supabase Dashboard → Project Settings → API → Project URL",
    "SUPABASE_KEY":   "Supabase Dashboard → Project Settings → API → anon public key",
    "GROQ_API_KEY":   "console.groq.com/keys",
    "GEMINI_API_KEY": "aistudio.google.com/app/apikey",
}

_missing = [k for k in _SOURCES if not os.getenv(k)]
if _missing:
    for var in _missing:
        print(f"[ERROR] Falta variable de entorno: {var}")
        print(f"        Consíguela en: {_SOURCES[var]}")
        print(f"        Añádela al archivo .env en la raíz del proyecto\n")
    sys.exit(1)
