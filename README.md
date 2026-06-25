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
├── data_provider.py           # Ingesta: Mock (dev) + RapidAPITennisProvider (real, confirmado)
├── opening_odds_tracker.py    # Resuelve la cuota de apertura real cruzando fixtures + live
├── player_name_matching.py    # Normalización y comparación de nombres de jugador
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

El proyecto usa un archivo `.env` para las credenciales (se carga
automáticamente al arrancar gracias a `python-dotenv`). Copia la
plantilla y rellena tus valores reales:

```bash
cp .env.example .env
```

Y edita `.env` con tu editor:

```bash
TELEGRAM_BOT_TOKEN=123456789:AAExampleTokenReplaceThis
TELEGRAM_CHAT_ID=123456789

RAPIDAPI_KEY=tu_rapidapi_key_aqui
RAPIDAPI_HOST=tennis-live-data.p.rapidapi.com
RAPIDAPI_BASE_URL=https://tennis-live-data.p.rapidapi.com
RAPIDAPI_LIVE_ENDPOINT=/tennis/v2/live-events

MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=tennis_trading_bot
MONGO_ALERTS_COLLECTION=trading_alerts
```

**El archivo `.env` nunca se sube a git** (está en `.gitignore`); solo
`.env.example` (sin secretos) se versiona como plantilla para otros
desarrolladores o para tu futuro yo en otra máquina.

Si prefieres no usar `.env` (por ejemplo, en un servidor donde ya
gestionas las variables de entorno de otra forma — Docker, systemd,
CI/CD), simplemente exporta las mismas variables antes de ejecutar:

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC-tu-token-real"
export MONGO_URI="mongodb://localhost:27017"
# etc.
```

`config.py` las leerá igual; `.env` es solo una comodidad para desarrollo local.

## Ejecución

```bash
python3 main.py
```

Por defecto usa `MockBetsAPIProvider`, que simula un feed en vivo en
memoria con partidos de **distintas categorías de torneo** (Grand
Slam, Masters 1000, ATP 250, Challenger) para que puedas comprobar a
simple vista que la priorización funciona, sin gastar ningún crédito
de API todavía.

### Datos en vivo: RapidAPI (ya conectado)

El bot usa por defecto el producto **"Tennis API - ATP WTA ITF"** de
RapidAPI (`tennis-api-atp-wta-itf.p.rapidapi.com`), confirmado contra
respuestas reales:

- `GET /tennis/v2/extend/api/events/live` — lista de partidos en vivo
  ahora (jugadores, marcador, set actual, indicador de saque).
- `GET /tennis/v2/extend/api/odds/summary/movements/last-10/{event_id}`
  — historial de movimientos de cuota por partido (se usa para
  `odds_prematch_*` y `live_odds_*`).

Solo necesitas rellenar `RAPIDAPI_KEY` en tu `.env`; el resto de
variables (`RAPIDAPI_HOST`, `RAPIDAPI_BASE_URL`, `RAPIDAPI_LIVE_ENDPOINT`)
ya tienen el valor correcto por defecto en `config.py`. Si `RAPIDAPI_KEY`
no está rellenada (o sigue en el valor de relleno), el bot cae
automáticamente a `MockBetsAPIProvider` y avisa por log — así nunca
intenta llamar a RapidAPI con una key inválida.

**Limitaciones conocidas de este endpoint** (documentadas también en
el docstring de `RapidAPITennisProvider` en `data_provider.py`):

- No incluye **superficie** ni si la pista es indoor → se rellena como
  vacío/`False`. Esto afecta al Filtro 3 (condicional de superficie):
  con superficie vacía, ese partido cae en la rama "otras superficies"
  del motor de reglas, no en las ramas específicas de tierra/hierba.
- La **categoría de torneo** no viene como campo explícito; se infiere
  del nombre del torneo (`league`) detectando prefijos típicos de ITF
  (`M15`, `W35`, etc. → `"ITF"`). Si ves nombres de Challenger o tours
  principales en tus datos reales, amplía
  `RapidAPITennisProvider._ITF_LEAGUE_PREFIXES` en `data_provider.py`.
- El significado exacto del campo `indicator` (quién sirve) **no está
  documentado oficialmente** por el proveedor; se dedujo comparando
  dos llamadas al mismo partido en instantes distintos y confirmando
  contra el texto legible de `event/timeline` (que sí narra quién
  rompió el saque de quién). Si en producción ves que el saque
  aparece invertido, el único cambio necesario está en
  `RapidAPITennisProvider._parse_indicator()`.

### El problema de la cuota pre-partido (y cómo se resuelve)

El endpoint de cuotas (`odds/summary/movements/last-10/{id}`) **ya
está confirmado** contra respuestas reales: devuelve, agrupado por
casa de apuestas y mercado, los **últimos 10 movimientos** de cuota
del mercado "Full Time Result" (ganador del partido), ordenados del
más reciente al más antiguo.

El problema (confirmado empíricamente, ver historial de desarrollo):
para un partido que ya lleva tiempo jugándose, **ni el movimiento más
antiguo de esos 10 es una cuota pre-partido fiable** — ya refleja
cómo se ha movido el mercado durante el propio partido, no la cuota
de apertura real. Probarlo con un partido que llevaba un set completo
jugado mostró una ventana de apenas ~7 minutos de movimientos, muy
lejos del inicio real del encuentro.

**Solución implementada: `opening_odds_tracker.py`**

1. Cada `FIXTURES_REFRESH_INTERVAL_SECONDS` (30 min por defecto), el
   bot descarga los fixtures del día (`/tennis/v2/{tour}/fixtures/{fecha}`,
   que sí incluye la hora programada) y los guarda en MongoDB
   (colección `fixtures`).
2. En cada ciclo de polling, cuando aparece un `game_id` que nunca se
   había visto antes en `events/live`, el bot busca entre los
   fixtures guardados uno cuyos jugadores coincidan (cruce por
   nombre vía `player_name_matching.py`, ya que el `id` de fixtures
   y el de `events/live` **no son el mismo identificador** — son
   numeraciones independientes del proveedor).
3. Si encuentra coincidencia, captura la cuota **en vivo actual** en
   ese instante y la guarda de forma permanente en MongoDB (colección
   `opening_odds`) como la cuota de apertura de ese partido. Es una
   aproximación (la cuota cuando el bot detectó el partido, no la
   cuota exacta al segundo de su inicio), pero es lo mejor disponible
   con este proveedor de datos.
4. En todos los ciclos posteriores, ese `game_id` ya tiene su cuota de
   apertura guardada y se reutiliza sin volver a calcularla — así
   `odds_prematch_*` permanece estable mientras `live_odds_*` sigue
   actualizándose con cada ciclo.

**Limitación aceptada conscientemente**: si el bot arranca cuando un
partido ya lleva tiempo jugándose, nunca podrá recuperar su cuota de
apertura real (se perdió esa oportunidad). Ese partido se descarta
limpiamente en `_safe_parse_match()` por falta de cuota pre-partido,
igual que cualquier otro dato incompleto — no genera alertas con
datos inventados.

El cruce de nombres (`player_name_matching.py`) tolera variaciones
razonables (tildes, mayúsculas, nombre/apellido invertidos, "C.
Alcaraz" vs "Carlos Alcaraz", incluyendo parejas de dobles separadas
por "/") pero **no** usa similitud difusa (Levenshtein, etc.) a
propósito: un emparejamiento "parecido pero incorrecto" introduciría
la cuota de un partido equivocado, lo cual es peor que simplemente no
encontrar coincidencia ese ciclo y reintentarlo en el siguiente.

Atención particular al apellido compartido: dos nombres completos que
solo comparten apellido (p.ej. "Pedro Martinez" vs "Carlos Martinez",
algo nada raro en tenis) **nunca** se consideran la misma persona solo
por eso. La coincidencia por apellido únicamente se permite cuando al
menos uno de los dos nombres está en forma abreviada ("C. Martinez"),
y si ambos lados están abreviados, se exige además que las iniciales
coincidan entre sí (evita que "C. Martinez" y "P. Martinez" — dos
jugadores distintos — se confundan por el apellido compartido).


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

Cada partido lleva una `tournament_category` resuelta de dos formas
posibles, en este orden de preferencia:

1. **Fuente primaria (fiable): `rankId` oficial.** Cuando hay un
   `opening_odds_tracker` inyectado (caso normal con
   `RapidAPITennisProvider`), se resuelve el `tournament_id` del
   partido cruzando con los fixtures guardados, y se consulta (con
   caché en MongoDB, colección `tournament_info`)
   `/tennis/v2/{type}/tournament/info/{tournament_id}`, que devuelve
   un campo `rankId` oficial. Este `rankId` se traduce a categoría
   canónica vía `config.RANK_ID_TO_CATEGORY`, confirmado contra el
   propio endpoint de niveles del proveedor
   (`/tennis/v2/ms-api/calendar/atp/filters`):

   | `rankId` | Nivel oficial | Categoría canónica |
   |---|---|---|
   | 0 | Futures/Satellites/ITF tournaments $10K | `ITF` |
   | 1 | Challengers/ITF tournaments > $10K | `Challenger` |
   | 2 | Main tour | `Tour` (agrupa ATP/WTA 500 y 250) |
   | 3 | Masters series | `Masters 1000` |
   | 4 | Grand Slam | `Grand Slam` |
   | 7 | Tour finals | `Tour` |

   Nota: el proveedor **no distingue ATP/WTA 500 de 250** (ambos caen
   en "Main tour"), así que se agrupan deliberadamente en una sola
   categoría `Tour` en vez de inventar una distinción que la fuente de
   datos no ofrece.

   Esta misma llamada también resuelve la **superficie real** del
   torneo (`court.name` en la respuesta, traducido de inglés a
   español vía `config.SURFACE_NAME_TRANSLATIONS`: "Hard"→"Dura",
   "Clay"→"Tierra Batida", "Grass"→"Hierba"), reemplazando así la
   limitación original de `events/live` (que no trae superficie).

1.5. **Resolución "bajo demanda" para partidos huérfanos.** Si el
   cruce inicial con fixtures falla (típicamente porque el partido
   empezó después del último refresco periódico de 30 min), en vez de
   caer directamente al fallback por nombre, `RapidAPITennisProvider`
   dispara un **refresco dirigido e inmediato** de fixtures — solo
   para el `tour_type` (atp/wta) de ese partido concreto, no ambos —
   y reintenta el cruce en el mismo ciclo. Esto resuelve la categoría
   correcta sin esperar al próximo refresco programado.

   Para no martillear la API si un partido realmente no tiene fixture
   todavía (qualy de última hora, dato inconsistente del proveedor),
   este refresco dirigido respeta un cooldown por partido
   (`config.ORPHAN_LOOKUP_COOLDOWN_SECONDS`, 2 minutos por defecto,
   gestionado en memoria por `OpeningOddsTracker`): si el primer
   intento bajo demanda no encuentra el fixture, no se repite en cada
   ciclo de 15s, solo tras pasar el cooldown.

2. **Fuente secundaria (heurística, último recurso): nombre del
   torneo.** Si tampoco el refresco dirigido logra resolver el
   `tournament_id` (o no hay tracker inyectado), se cae a
   `normalize_tournament_category()`, que infiere por el nombre del
   torneo (`league` en `events/live`) detectando prefijos típicos de
   ITF (`M15`, `W35`...). Solo distingue ITF; cualquier otra categoría
   queda como `Desconocido` en este modo.

La jerarquía final de prioridad, de mayor a menor:

```
Grand Slam > Masters 1000 > Tour (ATP/WTA 500+250) > Challenger > ITF
```

`data_provider.get_live_matches()` devuelve siempre la lista **ya
ordenada** según esta jerarquía, de forma que `main.py` evalúa y
notifica primero los partidos de los torneos más importantes en cada
ciclo de polling. Esto no filtra ni descarta ningún torneo pequeño:
solo decide el orden de procesamiento dentro del mismo ciclo.

Si tu proveedor de datos usa nombres de categoría distintos a los de
la lista (p. ej. otro texto de `tier`), añade el alias correspondiente
en `config.TOURNAMENT_CATEGORY_ALIASES`, o si cambias de producto de
RapidAPI con su propio sistema de rangos, ajusta
`config.RANK_ID_TO_CATEGORY`.

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

## Despliegue en Railway

Este bot es un **proceso de fondo (worker)**, no un servicio web: no
escucha en ningún puerto HTTP, solo corre el bucle de polling
indefinidamente. Railway necesita saberlo explícitamente.

### 1. El `Procfile`

Ya incluido en la raíz del proyecto:

```
worker: python3 main.py
```

Esto le dice a Railway (vía Railpack/Nixpacks) que ejecute el bot como
proceso de fondo. **Sin este archivo, Railway no sabe cómo arrancar
el proyecto** y el despliegue se queda construido pero nunca arranca.

### 2. Sube el proyecto a GitHub

Railway despliega desde un repositorio de GitHub, no subiendo el zip
directamente. Crea un repo (puede ser privado) y sube el contenido de
esta carpeta — **asegúrate de que `.gitignore` ya excluye tu `.env`
real** antes de hacer el primer commit, para no subir tus credenciales.

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/tu-usuario/tu-repo.git
git push -u origin main
```

### 3. Crea el proyecto en Railway y conecta el repo

En el dashboard de Railway: "New Project" → "Deploy from GitHub repo"
→ selecciona tu repositorio. Railway detectará Python automáticamente
vía `requirements.txt` y usará el `Procfile` para el comando de arranque.

### 4. Añade MongoDB como servicio del mismo proyecto

Dentro del mismo proyecto de Railway (no en uno aparte): "+ New" →
"Database" → "Add MongoDB". Railway despliega un contenedor MongoDB
oficial y le asigna automáticamente variables como `MONGO_URL`,
`MONGOHOST`, `MONGOPORT`, etc.

**Importante**: esto es un **segundo servicio facturado por separado**
del bot dentro del mismo proyecto — no es gratis indefinidamente. El
plan gratuito de Railway da $5 de crédito los primeros 30 días; pasado
ese periodo, cada servicio activo consume crédito/facturación según uso.

### 5. Conecta el bot a MongoDB con una "reference variable"

En el servicio del bot (no en el de MongoDB), añade esta variable de
entorno, usando la sintaxis de referencia de Railway para apuntar al
otro servicio (sustituye `MongoDB` por el nombre exacto que le haya
puesto Railway a tu servicio de base de datos, visible en el dashboard):

```
MONGO_URI=${{MongoDB.MONGO_URL}}
```

Railway resuelve esa referencia automáticamente al valor real de
conexión interna entre servicios del mismo proyecto — no necesitas
copiar ningún string a mano, y si Railway rota las credenciales de
Mongo, tu bot sigue funcionando sin cambios.

**Trampa común reportada por otros usuarios de Railway** (confirmada
en su Help Station): si la variable se queda vacía o la conexión da
`ENOTFOUND`/timeout, casi siempre es porque el servicio del bot y el
de MongoDB **no están en el mismo "environment"** del proyecto, o el
nombre de servicio en la referencia (`MongoDB` en el ejemplo) no
coincide exactamente con el nombre real que Railway le asignó. Revisa
el nombre exacto en el dashboard si esto pasa.

### 6. Añade el resto de variables de entorno

En el servicio del bot, añade (como variables normales, no referencias)
el resto de las que ya tienes en tu `.env` local:

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
RAPIDAPI_KEY=...
RAPIDAPI_HOST=tennis-api-atp-wta-itf.p.rapidapi.com
RAPIDAPI_BASE_URL=https://tennis-api-atp-wta-itf.p.rapidapi.com
RAPIDAPI_LIVE_ENDPOINT=/tennis/v2/extend/api/events/live
RAPIDAPI_FETCH_ODDS=true
```

(El resto de variables del `.env.example` tienen valores por defecto
razonables en `config.py`; solo necesitas sobreescribir las que
quieras cambiar respecto al default.)

### 7. Despliega y verifica los logs

Railway desplegará automáticamente tras detectar el push. En la
pestaña "Deployments" del servicio del bot, abre los logs y confirma
que aparecen líneas como:

```
INFO | OpeningOddsTracker conectado a MongoDB (db=tennis_trading_bot).
INFO | Usando RapidAPITennisProvider (host=tennis-api-atp-wta-itf.p.rapidapi.com) como fuente de datos.
INFO | Bot de trading de tenis iniciado. Intervalo de polling: 15s
```

Si en su lugar ves `RAPIDAPI_KEY no configurada... usando MockBetsAPIProvider`,
revisa que la variable `RAPIDAPI_KEY` esté bien escrita en el dashboard
de Railway (sin comillas, sin espacios).

### Notas de coste y operación

- El bot corre 24/7 mientras el servicio esté activo: dos servicios
  (bot + MongoDB) consumiendo crédito de forma continua. Vigila el
  uso desde el dashboard de Railway si quieres evitar sorpresas en la
  factura.
- Railway reinicia el contenedor automáticamente en cada nuevo deploy
  o caída; `main.py` ya maneja `SIGTERM` de forma ordenada (cierra las
  conexiones a MongoDB antes de salir), así que un reinicio no corrompe
  datos ni deja conexiones colgadas.
- Si más adelante quieres reducir el riesgo de "loguear desde el primer
  segundo en vivo" en cada redeploy, recuerda que el caché de fixtures
  y de cuotas de apertura vive en MongoDB, no en memoria del proceso:
  un reinicio del bot **no** pierde las cuotas de apertura ya
  capturadas para partidos en curso.

