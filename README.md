# Tennis Trading Bot — Detección de "Bache de Favorito"

Bot de Live Trading para tenis que monitoriza partidos en vivo y envía
alertas a Telegram cuando un favorito pre-partido claro (cuota entre
1.12 y 1.35) sufre un break en el primer set. Procesa siempre primero
los torneos grandes (Grand Slam, Masters 1000...) y luego los pequeños
(Challenger, ITF), y persiste cada alerta en MongoDB para auditoría.

## Estructura del proyecto

```
tennis_trading_bot/
├── main.py                    # Punto de entrada: bucle de polling y orquestación
├── config.py                  # Configuración centralizada (credenciales, umbrales)
├── models.py                  # Dataclasses: MatchState, AlertEvent
├── data_provider.py           # Ingesta: Mock (dev) + RapidAPITennisProvider (real, genérico)
├── flashscore_scraper.py      # Alternativa OPCIONAL/NO recomendada (scraping Flashscore)
├── tournament_priority.py     # Normalización de categorías y ordenación grandes→pequeños
├── trading_engine.py          # Motor de reglas (núcleo de inteligencia)
├── notifier.py                # Cliente Telegram vía httpx puro
├── logger.py                  # Persistencia de alertas en MongoDB (pymongo)
├── test_trading_engine.py     # Suite de pruebas funcionales del motor
├── requirements.txt
└── logs/                      # (ya no se usa para datos; se deja por compatibilidad)
```

## Arquitectura

```
data_provider.py  →  tournament_priority.py  →  trading_engine.py  →  notifier.py
   (RapidAPI/Mock)      (ordena grandes→pequeños)                  ↘
                                                                       logger.py (MongoDB)
```

- **data_provider.py**: produce objetos `MatchState` ya ordenados de
  torneo grande a pequeño. El resto del sistema no sabe ni le importa
  si los datos vienen de RapidAPI, de un mock o de scraping.
- **tournament_priority.py**: aísla la lógica de "qué es un torneo
  grande" (jerarquía configurable en `config.py`) para que tanto el
  proveedor de datos como, en el futuro, otras partes del sistema
  puedan reutilizarla sin duplicar código.
- **trading_engine.py**: puro, sin efectos secundarios externos.
  Decide si un `MatchState` genera un `AlertEvent`.
- **notifier.py** y **logger.py**: consumidores independientes del
  mismo `AlertEvent`. Un fallo en uno (Telegram caído, Mongo caído)
  nunca bloquea al otro.

## Instalación

```bash
pip install -r requirements.txt
```

Necesitas un servidor MongoDB accesible (local, Docker, o un cluster
gratuito de MongoDB Atlas). Para levantar uno local rápido con Docker:

```bash
docker run -d --name tennis-mongo -p 27017:27017 mongo:7
```

## Configuración

Edita `config.py` o exporta variables de entorno antes de ejecutar:

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC-tu-token-real"
export TELEGRAM_CHAT_ID="-1001234567890"

export MONGO_URI="mongodb://localhost:27017"
export MONGO_DB_NAME="tennis_trading_bot"

# Cuando tengas cuenta en RapidAPI:
export RAPIDAPI_KEY="tu-key"
export RAPIDAPI_HOST="tennis-live-data.p.rapidapi.com"   # el host del producto que elijas
export RAPIDAPI_BASE_URL="https://tennis-live-data.p.rapidapi.com"
export RAPIDAPI_LIVE_ENDPOINT="/tennis/v2/live-events"    # el endpoint real del producto elegido
```

## Ejecución

```bash
python3 main.py
```

Por defecto usa `MockBetsAPIProvider`, que simula un feed en vivo en
memoria con partidos de **distintas categorías de torneo** (Grand
Slam, Masters 1000, ATP 250, Challenger) para que puedas comprobar a
simple vista que la priorización funciona, sin gastar ningún crédito
de API todavía.

### Pasar a RapidAPI (datos reales)

1. Busca en el marketplace de RapidAPI un producto de tenis en vivo
   (filtra por "tennis live scores" / "tennis live data") y revisa qué
   tier gratuito de créditos ofrece cada uno.
2. Suscríbete y copia tu `X-RapidAPI-Key` y el host del producto.
3. Rellena las variables `RAPIDAPI_*` de `config.py` (o expórtalas
   como entorno).
4. En `main.py`, dentro de `_build_default_bot()`, descomenta el
   bloque de `RapidAPITennisProvider` y comenta la línea de
   `MockBetsAPIProvider`.
5. **Importante**: la primera vez que ejecutes contra el producto
   real, revisa el JSON real que te devuelve (puedes loguearlo
   temporalmente) y ajusta `RapidAPITennisProvider._normalize_raw_match()`
   y `_extract_events_list()` en `data_provider.py` a esa estructura
   exacta — los nombres de campo varían bastante entre productos.

### Sobre Flashscore (scraping)

**No es la vía recomendada.** Flashscore prohíbe el scraping en sus
Términos de Servicio y tiene protecciones anti-bot activas. Se incluye
`flashscore_scraper.py` solo como referencia para quien quiera asumir
ese riesgo si se agotan los créditos gratuitos de RapidAPI; el método
`get_live_matches()` lanza deliberadamente `NotImplementedError` con
una guía orientativa, en vez de scraping funcional listo para usar,
para forzar una decisión consciente antes de activarlo.

## Reglas del motor (`trading_engine.py`)

1. **Favoritismo real**: cuota pre-partido del favorito estrictamente
   entre 1.12 y 1.35.
2. **Bache en el 1er set**: el favorito va perdiendo en juegos
   (`current_set == 1`), con déficit de 1 break (simple) o 2+ breaks
   (doble break; en WTA se reconocen explícitamente los patrones 1-4 y 2-5).
3. **Condicional de superficie**:
   - *Tierra Batida*: permisivo, alerta ya con un solo break abajo en
     fase temprana (1-2, 1-3).
   - *Hierba*: restrictivo — solo alerta si el favorito está **restando**
     en ese momento, y nunca en un juego crítico de final de set
     (suma de juegos ≥ 7, cerca de tie-break).
   - *Dura*: postura intermedia, exige un break claro en fase temprana/media.
4. **Anti-duplicados**: una vez disparada una alerta para un `game_id`
   en un set concreto, no se vuelve a notificar dentro de ese mismo set.

## Priorización de torneos (`tournament_priority.py`)

Cada partido lleva una `tournament_category` que se normaliza contra
la jerarquía definida en `config.TOURNAMENT_CATEGORY_PRIORITY`:

```
Grand Slam > Masters 1000 / WTA 1000 > ATP 500 / WTA 500
           > ATP 250 / WTA 250 > Challenger > ITF
```

`data_provider.get_live_matches()` devuelve siempre la lista **ya
ordenada** según esta jerarquía, de forma que `main.py` evalúa y
notifica primero los partidos de los torneos más importantes en cada
ciclo de polling. Esto no filtra ni descarta ningún torneo pequeño:
solo decide el orden de procesamiento dentro del mismo ciclo.

Si tu proveedor de datos usa nombres de categoría distintos a los de
la lista (p. ej. "ATP500" en vez de "ATP 500"), añade el alias
correspondiente en `config.TOURNAMENT_CATEGORY_ALIASES` — es el único
sitio que hay que tocar.

## Tests

```bash
python3 test_trading_engine.py
```

Valida 11 escenarios explícitos del enunciado original (tierra 1-3,
WTA 1-4/2-5, hierba restrictiva sirviendo vs restando, anti-duplicados,
fuera de rango de cuota, set distinto de 1, etc).

## Persistencia (MongoDB)

Cada alerta se guarda como un documento en la colección configurada
(`MONGO_ALERTS_COLLECTION`, por defecto `trading_alerts`), con índices
sobre `game_id`, `timestamp_utc` y `tournament_category` para acelerar
las consultas de auditoría más habituales, por ejemplo:

```javascript
// Todas las alertas de Grand Slam de la última semana
db.trading_alerts.find({
  tournament_category: "Grand Slam",
  timestamp_utc: { $gte: new Date(Date.now() - 7*24*60*60*1000) }
})

// Rendimiento agregado por categoría de torneo
db.trading_alerts.aggregate([
  { $group: { _id: "$tournament_category", total: { $sum: 1 } } }
])
```

Si MongoDB no está disponible al arrancar (o se cae durante la
ejecución), el bot sigue funcionando con normalidad: las alertas
siguen enviándose a Telegram, y `logger.py` reintenta la conexión de
forma perezosa en cada alerta hasta que Mongo vuelva.

## Extensión futura: Betfair Exchange

Los puntos de extensión para automatizar la orden de compra (en lugar
de solo notificar) siguen marcados con comentarios `# FUTURO:` en
`notifier.py` y `main.py`. La integración requeriría un nuevo módulo
`betfair_client.py` que hable con la Betting API de Betfair Exchange
(`placeOrders`), usando `BETFAIR_APP_KEY` y `BETFAIR_SESSION_TOKEN`
(ya esbozados, comentados, en `config.py`).
