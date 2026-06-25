"""
config.py
---------
Configuración centralizada del bot. Aquí se definen credenciales,
umbrales de negocio y parámetros del motor de reglas.

Las credenciales (TOKEN, CHAT_ID, API_KEY, MONGO_URI) se cargan desde
variables de entorno. Para desarrollo local, se leen automáticamente
desde un archivo `.env` en la raíz del proyecto (ver `.env.example`
como plantilla). En producción (Docker, un servidor, CI/CD), basta con
exportar esas mismas variables de entorno directamente; el `.env` es
solo una comodidad para no tener que exportarlas a mano cada vez.

IMPORTANTE: el archivo `.env` con tus claves reales NUNCA debe subirse
a git (ya está excluido en `.gitignore`). Solo `.env.example` (sin
secretos) se versiona.
"""

import os

from dotenv import load_dotenv

# Carga las variables definidas en .env al entorno del proceso, si el
# archivo existe. Si no existe (p. ej. en un servidor donde ya se
# exportaron las variables manualmente), no falla: simplemente no
# sobreescribe nada y os.getenv() seguirá leyendo del entorno real.
load_dotenv()

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "TU_TOKEN_AQUI")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "TU_CHAT_ID_AQUI")

# --- Proveedor de datos: RapidAPI ---
# Producto confirmado y en uso: "Tennis API - ATP WTA ITF"
# (host tennis-api-atp-wta-itf.p.rapidapi.com). Los valores por
# defecto de HOST/BASE_URL/ENDPOINT ya corresponden a este producto;
# solo necesitas rellenar tu RAPIDAPI_KEY real (en .env, NUNCA aquí).
# Si en el futuro cambias a otro producto de RapidAPI, sobreescribe
# estas 4 variables vía entorno y revisa
# RapidAPITennisProvider._normalize_raw_match() en data_provider.py,
# ya que la estructura JSON puede ser distinta entre productos.
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "TU_RAPIDAPI_KEY_AQUI")
RAPIDAPI_HOST = os.getenv("RAPIDAPI_HOST", "tennis-api-atp-wta-itf.p.rapidapi.com")
RAPIDAPI_BASE_URL = os.getenv("RAPIDAPI_BASE_URL", "https://tennis-api-atp-wta-itf.p.rapidapi.com")
# Endpoint de listado de partidos en vivo (confirmado contra respuesta real)
RAPIDAPI_LIVE_ENDPOINT = os.getenv("RAPIDAPI_LIVE_ENDPOINT", "/tennis/v2/extend/api/events/live")
# Si False, RapidAPITennisProvider no consulta cuotas por partido (más
# rápido / menos créditos gastados), pero entonces TODOS los partidos
# se descartarán en _safe_parse_match() por falta de cuota pre-partido,
# ya que el motor de reglas la necesita obligatoriamente (Filtro 1).
RAPIDAPI_FETCH_ODDS = os.getenv("RAPIDAPI_FETCH_ODDS", "true").lower() == "true"

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
# Colección donde se guardan los fixtures (partidos programados) del
# día, refrescados periódicamente, para poder cruzarlos con los
# partidos que van apareciendo en events/live y así capturar su cuota
# de apertura real (ver opening_odds_tracker.py).
MONGO_FIXTURES_COLLECTION = os.getenv("MONGO_FIXTURES_COLLECTION", "fixtures")
# Colección donde se guarda, una sola vez por partido, la cuota
# capturada en el momento en que se detectó por primera vez en vivo
# (la mejor aproximación disponible a la cuota pre-partido real).
MONGO_OPENING_ODDS_COLLECTION = os.getenv("MONGO_OPENING_ODDS_COLLECTION", "opening_odds")
# Colección donde se cachea la categoría/superficie real de cada
# torneo (resuelta vía /tennis/v2/{type}/tournament/info/{id}), para
# no volver a pedir ese endpoint en cada ciclo de polling: la
# categoría y superficie de un torneo no cambian durante su disputa.
MONGO_TOURNAMENT_INFO_COLLECTION = os.getenv("MONGO_TOURNAMENT_INFO_COLLECTION", "tournament_info")
# Timeout corto para no bloquear el bucle de polling si Mongo no responde
MONGO_SERVER_SELECTION_TIMEOUT_MS = 5000

# --- Fixtures: refresco periódico ---
# Cada cuántos segundos se vuelve a consultar /fixtures/{fecha} para
# refrescar la lista de partidos programados de hoy (y de mañana, por
# si el bot sigue corriendo cerca de medianoche). No hace falta que
# sea tan frecuente como el polling de partidos en vivo.
FIXTURES_REFRESH_INTERVAL_SECONDS = 1800  # 30 minutos
# Tipos de circuito para los que se consultan fixtures (la ruta real
# es /tennis/v2/{type}/fixtures/{fecha})
FIXTURES_TOUR_TYPES = ["atp", "wta"]

# --- Modo de prueba manual con seguimiento (bajo consumo de requests) ---
# Pensado para planes con límite diario bajo de RapidAPI (p.ej. Free,
# 50 requests/día). En vez de vigilar TODOS los partidos en vivo de
# forma continua, el bot detecta candidatos (bache + cuota favorita)
# y los presenta en Telegram; solo empieza a vigilar de cerca el
# partido que el usuario elige respondiendo ".{game_id}" en el chat.
# Útil cuando ya se ha validado la metodología y se quiere automatizar
# también el seguimiento del break posterior. Ver MODO MÁS SIMPLE
# (ALERT_ONLY_MODE_ENABLED) más abajo para validar la metodología
# primero, sin automatizar nada del seguimiento.
MANUAL_MODE_ENABLED = os.getenv("MANUAL_MODE_ENABLED", "false").lower() == "true"
# Cada cuántos segundos se vuelve a consultar el marcador de un
# partido YA elegido por el usuario (en seguimiento activo). Un valor
# más alto consume menos requests pero detecta los breaks con más
# retraso. 300s (5 min) es razonable: la duración media de un juego de
# servicio en tenis profesional es de unos 4 minutos.
MANUAL_MODE_WATCH_INTERVAL_SECONDS = int(os.getenv("MANUAL_MODE_WATCH_INTERVAL_SECONDS", "300"))
# Cada cuántos segundos se vuelve a consultar events/live al completo
# para detectar NUEVOS candidatos (partidos que empiezan a cumplir el
# bache). Puede ser más espaciado que el polling automático normal,
# ya que solo se usa para ofrecer candidatos, no para seguimiento fino.
MANUAL_MODE_SCAN_INTERVAL_SECONDS = int(os.getenv("MANUAL_MODE_SCAN_INTERVAL_SECONDS", "300"))
# Cuánto tiempo (segundos) sigue siendo válido un código de selección
# ".{game_id}" tras ofrecerse como candidato, antes de caducar.
MANUAL_MODE_CANDIDATE_TTL_SECONDS = int(os.getenv("MANUAL_MODE_CANDIDATE_TTL_SECONDS", "3600"))

# --- Modo "solo alerta" (el más simple, para validar la metodología) ---
# Sin selección ni seguimiento automático: el bot escanea events/live
# dentro de una ventana horaria acotada (días y horas configurables) y
# manda un único aviso por partido que cumpla el bache+cuota. El propio
# usuario decide manualmente cuándo entrar y cuándo registrar el pick
# si rompen el saque del no favorito — nada de eso lo automatiza el bot
# en este modo. Pensado para una primera fase de validación con el plan
# Free de RapidAPI (50 requests/día), antes de automatizar el resto.
ALERT_ONLY_MODE_ENABLED = os.getenv("ALERT_ONLY_MODE_ENABLED", "false").lower() == "true"
# Zona horaria del usuario para interpretar la ventana de trading.
# Se usa zoneinfo (estándar de Python), que maneja automáticamente el
# cambio de horario de verano/invierno sin cálculos manuales.
ALERT_ONLY_MODE_TIMEZONE = os.getenv("ALERT_ONLY_MODE_TIMEZONE", "Europe/Madrid")
# Hora de inicio/fin de la ventana de trading, en formato "HH:MM", en
# la zona horaria anterior. Solo se escanea events/live dentro de esta
# ventana; fuera de ella, el bot no hace ninguna llamada a RapidAPI.
ALERT_ONLY_MODE_START_TIME = os.getenv("ALERT_ONLY_MODE_START_TIME", "09:00")
ALERT_ONLY_MODE_END_TIME = os.getenv("ALERT_ONLY_MODE_END_TIME", "16:00")
# Días de la semana activos (0=lunes ... 6=domingo, como datetime.weekday()).
# Por defecto lunes a viernes.
ALERT_ONLY_MODE_ACTIVE_WEEKDAYS = [
    int(d) for d in os.getenv("ALERT_ONLY_MODE_ACTIVE_WEEKDAYS", "0,1,2,3,4").split(",")
]
# Intervalo de escaneo dentro de la ventana activa. 540s (9 min) está
# calculado para consumir ~46-47 de las 50 requests/día disponibles en
# una ventana de 7h (9:00-16:00), dejando un margen de seguridad de
# ~10% para imprevistos (pruebas manuales, ciclo extra al arrancar).
ALERT_ONLY_MODE_SCAN_INTERVAL_SECONDS = int(os.getenv("ALERT_ONLY_MODE_SCAN_INTERVAL_SECONDS", "540"))
# Cada cuántos segundos se revisa el reloj para saber si toca dormir
# (fuera de ventana) o escanear (dentro de ventana). No consume
# requests de RapidAPI, solo decide cuándo es momento de hacerlo.
ALERT_ONLY_MODE_CLOCK_CHECK_INTERVAL_SECONDS = 30

# --- Fixtures: refresco "bajo demanda" para partidos huérfanos ---
# Cuando un partido nuevo en events/live no cruza con ningún fixture
# ya guardado (típicamente porque empezó después del último refresco
# periódico), en vez de esperar al siguiente refresco programado
# (hasta 30 min), se dispara un refresco dirigido inmediato SOLO del
# tour_type de ese partido. Si tras ese refresco inmediato sigue sin
# encontrarse, se aplica este cooldown antes de reintentarlo de nuevo,
# para no martillear la API en cada ciclo de 15s si el partido
# realmente no tiene fixture (qualy de última hora, dato inconsistente
# del proveedor, etc).
ORPHAN_LOOKUP_COOLDOWN_SECONDS = 120  # 2 minutos

# --- Circuitos soportados ---
CIRCUIT_ATP = "ATP"
CIRCUIT_WTA = "WTA"

# --- Superficies ---
SURFACE_CLAY = "Tierra Batida"
SURFACE_GRASS = "Hierba"
SURFACE_HARD = "Dura"

# Mapeo de nombres de superficie tal como los devuelve la API
# (en inglés, vistos en tournament/info -> court.name: "Hard", "Clay",
# "Grass", "Carpet"...) hacia las constantes en español que usa
# trading_engine.py. Sin esta traducción, el Filtro 3 (condicional de
# superficie) nunca reconocería tierra batida ni hierba reales y todo
# caería por defecto en la rama de "Dura", rompiendo silenciosamente
# la regla de superficie ya validada.
SURFACE_NAME_TRANSLATIONS = {
    "hard": SURFACE_HARD,
    "clay": SURFACE_CLAY,
    "grass": SURFACE_GRASS,
    "carpet": SURFACE_HARD,  # moqueta indoor: se trata como pista dura a efectos del motor de reglas
    "indoor hard": SURFACE_HARD,
    "indoor clay": SURFACE_CLAY,
}

# --- Jerarquía de categorías de torneo (de mayor a menor prioridad) ---
# Se usa para ORDENAR el procesamiento de partidos en cada ciclo de
# polling: los torneos grandes se evalúan y notifican antes que los
# pequeños. No filtra ni excluye ningún torneo, solo decide el orden.
#
# Estos 5 niveles son los que el propio proveedor de datos distingue
# de forma fiable (ver RANK_ID_TO_CATEGORY más abajo): no separa ATP/WTA
# 500 de 250 (ambos caen en su nivel "Main tour"), así que se agrupan
# en una sola categoría "Tour" en vez de inventar una distinción que
# la fuente de datos no ofrece.
TOURNAMENT_CATEGORY_PRIORITY = [
    "Grand Slam",
    "Masters 1000",
    "Tour",        # ATP/WTA 500 + ATP/WTA 250 (el proveedor no los distingue)
    "Challenger",
    "ITF",
]

# Categoría asignada cuando la API no informa la categoría o no
# coincide con ninguna de la lista anterior. Se coloca al final
# de la prioridad (mínima prioridad) para no interferir con torneos
# grandes correctamente identificados.
TOURNAMENT_CATEGORY_UNKNOWN = "Desconocido"

# Mapeo OFICIAL confirmado contra el endpoint
# /tennis/v2/ms-api/calendar/atp/filters (campo "levels") de
# "Tennis API - ATP WTA ITF": traduce el "rankId" devuelto por
# /tennis/v2/{type}/tournament/info/{tournamentId} a la categoría
# canónica usada en TOURNAMENT_CATEGORY_PRIORITY. Esta es la fuente
# PRIMARIA y fiable de categoría (no una heurística por nombre).
#
# rankId oficiales NO relacionados con torneos individuales de
# singles/dobles regulares (Davis/Fed Cup, Juniors, Olympics, etc.) se
# omiten a propósito: si aparecen, caerán en TOURNAMENT_CATEGORY_UNKNOWN.
RANK_ID_TO_CATEGORY = {
    0: "ITF",          # "Futures/Satellites/ITF tournaments $10K"
    1: "Challenger",   # "Challengers/ITF tournaments > $10K"
    2: "Tour",         # "Main tour" (agrupa ATP/WTA 500 y 250)
    3: "Masters 1000",  # "Masters series"
    4: "Grand Slam",   # "Grand Slam"
    7: "Tour",         # "Tour finals" (ATP/WTA Finals) -> se trata como big-tour
}

# Mapeo de alias / variantes de texto que distintas APIs usan para
# referirse a la misma categoría, normalizados a las claves de
# TOURNAMENT_CATEGORY_PRIORITY. Se usa como FUENTE SECUNDARIA (texto
# del nombre del torneo) cuando no se dispone del rankId oficial
# (p.ej. el endpoint events/live no trae tournamentId, solo el nombre
# de la liga). Ampliar este diccionario es la forma recomendada de
# adaptar el bot a nomenclatura adicional que vayas observando.
TOURNAMENT_CATEGORY_ALIASES = {
    "grandslam": "Grand Slam",
    "grand slam": "Grand Slam",
    "slam": "Grand Slam",
    "masters1000": "Masters 1000",
    "masters 1000": "Masters 1000",
    "atp masters 1000": "Masters 1000",
    "masters series": "Masters 1000",
    "wta1000": "Masters 1000",
    "wta 1000": "Masters 1000",
    "main tour": "Tour",
    "tour finals": "Tour",
    "atp500": "Tour",
    "atp 500": "Tour",
    "wta500": "Tour",
    "wta 500": "Tour",
    "atp250": "Tour",
    "atp 250": "Tour",
    "wta250": "Tour",
    "wta 250": "Tour",
    "challenger": "Challenger",
    "atp challenger": "Challenger",
    "challengers/itf tournaments > $10k": "Challenger",
    "itf": "ITF",
    "itf world tennis tour": "ITF",
    "futures/satellites/itf tournaments $10k": "ITF",
}
