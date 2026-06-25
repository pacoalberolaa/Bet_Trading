"""
config.py
---------
Configuración centralizada del bot. Aquí se definen credenciales,
umbrales de negocio y parámetros del motor de reglas.

En producción, las credenciales (TOKEN, CHAT_ID, API_KEY, MONGO_URI)
deberían cargarse desde variables de entorno (os.environ) o un .env,
nunca hardcodeadas. Se deja como variables simples con valores por
defecto para facilitar el setup inicial.
"""

import os

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "TU_TOKEN_AQUI")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "TU_CHAT_ID_AQUI")

# --- Proveedor de datos: RapidAPI (genérico) ---
# Diseñado contra el patrón estándar de cualquier producto de tenis en
# RapidAPI: headers X-RapidAPI-Key / X-RapidAPI-Host + una base_url
# propia de cada producto. Cuando tengas cuenta, solo hace falta
# rellenar estas variables (o exportarlas como entorno) y, si el
# producto elegido tiene una estructura de JSON distinta, ajustar el
# mapeo en RapidAPITennisProvider._normalize_raw_match().
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "TU_RAPIDAPI_KEY_AQUI")
RAPIDAPI_HOST = os.getenv("RAPIDAPI_HOST", "TU_RAPIDAPI_HOST_AQUI")  # ej: "tennis-live-data.p.rapidapi.com"
RAPIDAPI_BASE_URL = os.getenv("RAPIDAPI_BASE_URL", "https://TU_RAPIDAPI_HOST_AQUI")
# Endpoint relativo de "partidos en vivo" del producto elegido (varía entre productos)
RAPIDAPI_LIVE_ENDPOINT = os.getenv("RAPIDAPI_LIVE_ENDPOINT", "/tennis/v2/live-events")

# --- Proveedor de datos alternativo: scraping de Flashscore (OPCIONAL, NO recomendado) ---
# Flashscore prohíbe explícitamente el scraping automatizado en sus
# Términos de Servicio y usa protecciones anti-bot activas (fingerprinting,
# bloqueo de IP, cambios frecuentes de estructura HTML). Se incluye un
# proveedor experimental en flashscore_scraper.py SOLO como referencia /
# último recurso si se agotan los créditos gratuitos de RapidAPI, pero
# no es la vía recomendada para producción. Úsalo bajo tu propio riesgo
# y revisa los TOS antes de activarlo.
FLASHSCORE_SCRAPING_ENABLED = os.getenv("FLASHSCORE_SCRAPING_ENABLED", "false").lower() == "true"

# --- Futuro: Betfair Exchange (automatización de la orden) ---
# BETFAIR_APP_KEY = os.getenv("BETFAIR_APP_KEY")
# BETFAIR_SESSION_TOKEN = os.getenv("BETFAIR_SESSION_TOKEN")
# BETFAIR_BASE_URL = "https://api.betfair.com/exchange/betting/rest/v1.0"

# --- Parámetros del motor de reglas (Filtro 1: Favoritismo real) ---
FAVORITE_ODDS_MIN = 1.12
FAVORITE_ODDS_MAX = 1.35

# --- Polling ---
POLL_INTERVAL_SECONDS = 15

# --- Persistencia: MongoDB ---
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "tennis_trading_bot")
MONGO_ALERTS_COLLECTION = os.getenv("MONGO_ALERTS_COLLECTION", "trading_alerts")
# Timeout corto para no bloquear el bucle de polling si Mongo no responde
MONGO_SERVER_SELECTION_TIMEOUT_MS = 5000

# --- Circuitos soportados ---
CIRCUIT_ATP = "ATP"
CIRCUIT_WTA = "WTA"

# --- Superficies ---
SURFACE_CLAY = "Tierra Batida"
SURFACE_GRASS = "Hierba"
SURFACE_HARD = "Dura"

# --- Jerarquía de categorías de torneo (de mayor a menor prioridad) ---
# Se usa para ORDENAR el procesamiento de partidos en cada ciclo de
# polling: los torneos grandes se evalúan y notifican antes que los
# pequeños. No filtra ni excluye ningún torneo, solo decide el orden.
TOURNAMENT_CATEGORY_PRIORITY = [
    "Grand Slam",
    "Masters 1000",
    "WTA 1000",
    "ATP 500",
    "WTA 500",
    "ATP 250",
    "WTA 250",
    "Challenger",
    "ITF",
]

# Categoría asignada cuando la API no informa la categoría o no
# coincide con ninguna de la lista anterior. Se coloca al final
# de la prioridad (mínima prioridad) para no interferir con torneos
# grandes correctamente identificados.
TOURNAMENT_CATEGORY_UNKNOWN = "Desconocido"

# Mapeo de alias / variantes de texto que distintas APIs usan para
# referirse a la misma categoría, normalizados a las claves de
# TOURNAMENT_CATEGORY_PRIORITY. Ampliar este diccionario es la forma
# recomendada de adaptar el bot a la nomenclatura exacta que use tu
# proveedor de datos real.
TOURNAMENT_CATEGORY_ALIASES = {
    "grandslam": "Grand Slam",
    "grand slam": "Grand Slam",
    "slam": "Grand Slam",
    "masters1000": "Masters 1000",
    "masters 1000": "Masters 1000",
    "atp masters 1000": "Masters 1000",
    "wta1000": "WTA 1000",
    "wta 1000": "WTA 1000",
    "atp500": "ATP 500",
    "atp 500": "ATP 500",
    "wta500": "WTA 500",
    "wta 500": "WTA 500",
    "atp250": "ATP 250",
    "atp 250": "ATP 250",
    "wta250": "WTA 250",
    "wta 250": "WTA 250",
    "challenger": "Challenger",
    "atp challenger": "Challenger",
    "itf": "ITF",
    "itf world tennis tour": "ITF",
}
