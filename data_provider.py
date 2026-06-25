"""
data_provider.py
-----------------
Capa de ingesta de datos. Responsable de obtener el estado de los
partidos en vivo y transformarlo en objetos `MatchState` tipados,
ya ordenados de torneos grandes a pequeños.

Diseño:
- `BaseDataProvider` define el contrato (interfaz) que cualquier
  proveedor debe cumplir: get_live_matches() -> List[MatchState].
- `MockBetsAPIProvider` es una implementación de ejemplo en memoria,
  con partidos de distintas categorías de torneo para poder probar
  la priorización sin depender de ninguna API externa.
- `RapidAPITennisProvider` es la implementación REAL recomendada:
  está escrita contra el patrón estándar de cualquier producto de
  tenis en RapidAPI (headers X-RapidAPI-Key / X-RapidAPI-Host). Al
  no tener todavía una cuenta/producto concreto, el mapeo de campos
  específico se deja como un único punto de ajuste claramente
  señalado (_normalize_raw_match) para no tener que tocar nada más
  del bot cuando se elija el producto definitivo.
- El scraping de Flashscore (alternativa NO recomendada) vive aparte,
  en flashscore_scraper.py, para que quede claro que es una vía
  secundaria y de mayor riesgo, no la opción por defecto.

Todos los proveedores devuelven la lista de partidos YA ordenada de
torneos grandes a pequeños (ver tournament_priority.py), de forma que
main.py procese siempre primero los partidos más relevantes.
"""

from __future__ import annotations

import json
import logging
import random
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import SURFACE_NAME_TRANSLATIONS
from models import MatchState
from tournament_priority import (
    normalize_category_from_rank_id,
    normalize_tournament_category,
    sort_matches_by_tournament_priority,
)

logger = logging.getLogger(__name__)


class DataProviderError(Exception):
    """Error genérico de la capa de ingesta (red, parseo, etc)."""


class BaseDataProvider(ABC):
    """Contrato que debe cumplir cualquier proveedor de datos en vivo."""

    @abstractmethod
    def get_live_matches(self) -> List[MatchState]:
        """
        Devuelve la lista de partidos en vivo como MatchState, YA
        ordenada de torneos grandes a pequeños.
        """
        raise NotImplementedError


def _safe_parse_match(raw: Dict[str, Any]) -> Optional[MatchState]:
    """
    Convierte un diccionario "crudo" (tal como llegaría de la API)
    en un MatchState, validando tipos y aplicando valores por defecto
    seguros. Si el registro está corrupto de forma irrecuperable,
    devuelve None en lugar de lanzar una excepción, para que un solo
    partido con JSON malformado no tumbe el polling completo.
    """
    try:
        game_id = str(raw["game_id"])

        circuit = str(raw.get("circuit", "")).upper().strip()
        surface = str(raw.get("surface", "")).strip()
        is_indoor = bool(raw.get("is_indoor", False))

        tournament_name = str(raw.get("tournament_name", "Torneo desconocido"))
        tournament_category = normalize_tournament_category(str(raw.get("tournament_category", "")))

        player_home = str(raw.get("player_home", "Jugador Home"))
        player_away = str(raw.get("player_away", "Jugador Away"))

        odds_prematch_home = float(raw["odds_prematch_home"])
        odds_prematch_away = float(raw["odds_prematch_away"])

        current_set = int(raw.get("current_set", 1))
        current_games_home = int(raw.get("current_games_home", 0))
        current_games_away = int(raw.get("current_games_away", 0))
        current_score_points = str(raw.get("current_score_points", "0-0"))
        current_server = str(raw.get("current_server", "home")).lower()
        if current_server not in ("home", "away"):
            current_server = "home"

        live_odds_home = float(raw.get("live_odds_home", odds_prematch_home))
        live_odds_away = float(raw.get("live_odds_away", odds_prematch_away))

        return MatchState(
            game_id=game_id,
            circuit=circuit,
            surface=surface,
            is_indoor=is_indoor,
            tournament_name=tournament_name,
            tournament_category=tournament_category,
            player_home=player_home,
            player_away=player_away,
            odds_prematch_home=odds_prematch_home,
            odds_prematch_away=odds_prematch_away,
            current_set=current_set,
            current_games_home=current_games_home,
            current_games_away=current_games_away,
            current_score_points=current_score_points,
            current_server=current_server,
            live_odds_home=live_odds_home,
            live_odds_away=live_odds_away,
        )

    except (KeyError, TypeError, ValueError) as exc:
        # JSON malformado / campo crítico ausente o no convertible.
        # Se registra y se descarta SOLO este partido, no se interrumpe
        # el resto del polling.
        logger.warning(
            "Registro de partido descartado por datos inválidos (game_id=%s): %s",
            raw.get("game_id", "desconocido"),
            exc,
        )
        return None


class MockBetsAPIProvider(BaseDataProvider):
    """
    Proveedor simulado para desarrollo y pruebas (paper trading).

    Mantiene un pequeño "mundo" de partidos en memoria y, en cada
    llamada a get_live_matches(), avanza ligeramente el marcador de
    forma aleatoria para simular un feed en vivo real. Incluye
    partidos de distintas categorías de torneo (Grand Slam, ATP 250,
    Challenger...) para poder verificar visualmente la priorización.
    """

    def __init__(self, seed_matches: Optional[List[Dict[str, Any]]] = None):
        self._matches: List[Dict[str, Any]] = seed_matches or self._default_seed()

    @staticmethod
    def _default_seed() -> List[Dict[str, Any]]:
        return [
            {
                "game_id": "M001",
                "circuit": "ATP",
                "surface": "Tierra Batida",
                "is_indoor": False,
                "tournament_name": "Challenger Bratislava",
                "tournament_category": "Challenger",
                "player_home": "C. Alcaraz",
                "player_away": "J. Munar",
                "odds_prematch_home": 1.20,
                "odds_prematch_away": 4.50,
                "current_set": 1,
                "current_games_home": 1,
                "current_games_away": 2,
                "current_score_points": "30-30",
                "current_server": "away",
                "live_odds_home": 1.45,
                "live_odds_away": 2.80,
            },
            {
                "game_id": "M002",
                "circuit": "WTA",
                "surface": "Hierba",
                "is_indoor": False,
                "tournament_name": "Wimbledon",
                "tournament_category": "Grand Slam",
                "player_home": "I. Swiatek",
                "player_away": "E. Rybakina",
                "odds_prematch_home": 1.30,
                "odds_prematch_away": 3.40,
                "current_set": 1,
                "current_games_home": 4,
                "current_games_away": 1,
                "current_score_points": "40-15",
                "current_server": "home",
                "live_odds_home": 1.18,
                "live_odds_away": 5.50,
            },
            {
                "game_id": "M003",
                "circuit": "ATP",
                "surface": "Dura",
                "is_indoor": True,
                "tournament_name": "ATP 250 Marsella",
                "tournament_category": "ATP 250",
                "player_home": "N. Djokovic",
                "player_away": "F. Tiafoe",
                "odds_prematch_home": 1.25,
                "odds_prematch_away": 4.00,
                "current_set": 1,
                "current_games_home": 1,
                "current_games_away": 3,
                "current_score_points": "15-0",
                "current_server": "home",
                "live_odds_home": 1.55,
                "live_odds_away": 2.50,
            },
            {
                "game_id": "M004",
                "circuit": "ATP",
                "surface": "Dura",
                "is_indoor": False,
                "tournament_name": "Masters 1000 Madrid",
                "tournament_category": "Masters 1000",
                "player_home": "J. Sinner",
                "player_away": "A. Zverev",
                "odds_prematch_home": 1.28,
                "odds_prematch_away": 3.60,
                "current_set": 1,
                "current_games_home": 1,
                "current_games_away": 3,
                "current_score_points": "0-0",
                "current_server": "away",
                "live_odds_home": 1.50,
                "live_odds_away": 2.60,
            },
        ]

    def get_live_matches(self) -> List[MatchState]:
        try:
            raw_snapshot = json.loads(json.dumps(self._matches))  # copia profunda simulando "JSON recibido"
        except (TypeError, ValueError) as exc:
            raise DataProviderError(f"Fallo simulando snapshot JSON: {exc}") from exc

        self._simulate_live_progression()

        parsed_matches: List[MatchState] = []
        for raw_match in raw_snapshot:
            match = _safe_parse_match(raw_match)
            if match is not None:
                parsed_matches.append(match)

        return sort_matches_by_tournament_priority(parsed_matches)

    def _simulate_live_progression(self) -> None:
        """Avanza aleatoriamente los juegos para simular un partido vivo."""
        for match in self._matches:
            if random.random() < 0.4:
                if random.random() < 0.5:
                    match["current_games_home"] += 1
                else:
                    match["current_games_away"] += 1
                # Pequeño ajuste de cuota en vivo para simular el "bache"
                match["live_odds_home"] = round(match["live_odds_home"] * random.uniform(0.95, 1.08), 2)
                match["live_odds_away"] = round(match["live_odds_away"] * random.uniform(0.95, 1.08), 2)


class RapidAPITennisProvider(BaseDataProvider):
    """
    Implementación REAL contra el producto "Tennis API - ATP WTA ITF"
    de RapidAPI (host tennis-api-atp-wta-itf.p.rapidapi.com), confirmada
    con respuestas reales de la cuenta del usuario.

    Estructura real del endpoint de listado en vivo
    (GET /tennis/v2/extend/api/events/live):

        {
          "success": true,
          "results": [
            {
              "id": "3681090",
              "name": "Chase Ferguson vs Fanming Meng",
              "participant1": "Chase Ferguson",
              "participant2": "Fanming Meng",
              "league": "M15 Wuning",
              "score": "4-6,0-1",
              "status": "InPlay",
              "points": "30-0",
              "indicator": "1,0",
              "tourType": "atp"
            },
            ...
          ],
          "count": 7
        }

    Limitaciones CONOCIDAS de este endpoint (no las inventa el código,
    son del propio proveedor de datos):
        - No incluye superficie (surface) ni si la pista es indoor.
          Se deja como "Desconocido" / False por defecto; si se
          necesita de verdad, habría que cruzar con otro endpoint de
          torneo (p.ej. /tennis/v2/{type}/tournament/info/{id}, que
          requiere conocer el tournament_id, no presente aquí tampoco).
        - No incluye categoría de torneo (Grand Slam / Challenger /
          ITF...). El campo "league" trae el NOMBRE del torneo (p.ej.
          "M15 Wuning", "W35 Taipei"), de donde sí se puede inferir la
          categoría en la mayoría de los casos: prefijos "M15"/"M25"
          (ATP) y "W15"/"W35"/"W75" (WTA) corresponden a torneos ITF
          de distinta dotación económica. Se mapean a "ITF" en
          _infer_tournament_category(); ajustar esa función si en tus
          datos aparecen prefijos de Challenger ("CH") o de tours
          principales que quieras distinguir mejor.
        - No incluye cuotas (odds_prematch_*, live_odds_*). Hay que
          combinarlo con el endpoint de cuotas
          (/tennis/v2/extend/api/odds/summary/movements/last-10/{id}),
          que sí está disponible en este mismo producto. Como ese
          endpoint requiere el event_id (que aquí sí tenemos en "id"),
          se consulta una vez por partido en vivo.

    Significado de "indicator" (NO documentado oficialmente por el
    proveedor; deducido empíricamente comparando dos llamadas al mismo
    partido en instantes distintos y viendo qué lado cambiaba tras un
    número impar de juegos jugados — así es como alterna el saque en
    tenis real). Bajo esta hipótesis:
        "1,0" -> sirve participant1 (home)
        "0,1" -> sirve participant2 (away)
        "0,0" -> visto solo en marcador 0-0 total; se interpreta como
                 "aún sin datos de saque" y se asume "home" por
                 defecto (ver _parse_indicator()).
    Si en producción se observa que esta hipótesis está invertida,
    el único cambio necesario es en _parse_indicator().
    """

    LIVE_LIST_PATH = "/tennis/v2/extend/api/events/live"
    ODDS_MOVEMENTS_PATH_TEMPLATE = "/tennis/v2/extend/api/odds/summary/movements/last-10/{event_id}"
    FIXTURES_PATH_TEMPLATE = "/tennis/v2/{tour_type}/fixtures/{date}"
    TOURNAMENT_INFO_PATH_TEMPLATE = "/tennis/v2/{tour_type}/tournament/info/{tournament_id}"

    # Prefijos de nombre de torneo ITF habituales (categoría más baja
    # del circuito profesional). Ampliar si aparecen otros prefijos
    # (p.ej. "CH" para Challenger) en los datos reales que vayas viendo.
    _ITF_LEAGUE_PREFIXES = ("M15", "M25", "W15", "W25", "W35", "W50", "W75", "W100")

    def __init__(
        self,
        api_key: str,
        api_host: str,
        base_url: str,
        live_endpoint: Optional[str] = None,
        fetch_odds: bool = True,
        opening_odds_tracker: Optional[Any] = None,
        fixtures_tour_types: Optional[List[str]] = None,
        http_client=None,
    ):
        self.api_key = api_key
        self.api_host = api_host
        self.base_url = base_url.rstrip("/")
        # Se permite sobreescribir la ruta vía config (RAPIDAPI_LIVE_ENDPOINT),
        # pero por defecto usa la ruta real confirmada de este producto.
        self.live_endpoint = live_endpoint or self.LIVE_LIST_PATH
        self.fetch_odds = fetch_odds
        # OpeningOddsTracker (opening_odds_tracker.OpeningOddsTracker):
        # resuelve la cuota de apertura real cruzando fixtures con
        # partidos nuevos en vivo. Si no se inyecta (None), el provider
        # cae al comportamiento simple anterior: usar directamente el
        # movimiento más antiguo de los últimos 10 como pre-partido,
        # que ya sabemos que está sesgado para partidos no recién
        # empezados (ver docstring de la clase). Se acepta así para no
        # forzar Mongo en escenarios de test/desarrollo simple.
        self.opening_odds_tracker = opening_odds_tracker
        self.fixtures_tour_types = fixtures_tour_types or ["atp", "wta"]
        # Se inyecta el cliente HTTP (httpx) para poder testear con mocks.
        self._http_client = http_client

    def get_live_matches(self) -> List[MatchState]:
        import httpx  # import local para no forzar la dependencia si no se usa este provider

        if self.opening_odds_tracker is not None and self.opening_odds_tracker.should_refresh_fixtures():
            self._refresh_fixtures(httpx)

        raw_events = self._fetch_live_events(httpx)

        parsed_matches: List[MatchState] = []
        for raw_event in raw_events:
            normalized = self._build_normalized_match(httpx, raw_event)
            if normalized is None:
                continue
            match = _safe_parse_match(normalized)
            if match is not None:
                parsed_matches.append(match)

        return sort_matches_by_tournament_priority(parsed_matches)

    def _refresh_fixtures(self, httpx_module) -> None:
        """Refresca los fixtures de hoy para cada tour configurado y los guarda en el tracker."""
        for tour_type in self.fixtures_tour_types:
            self._refresh_fixtures_for_tour(httpx_module, tour_type)

    def _refresh_fixtures_for_tour(self, httpx_module, tour_type: str) -> bool:
        """
        Refresca los fixtures de hoy para UN solo tour_type y los
        guarda en el tracker. Devuelve True si la consulta tuvo éxito
        (independientemente de si trajo o no fixtures), False si
        falló por red/HTTP/JSON.

        Aislado como método propio (en vez de vivir solo dentro de
        _refresh_fixtures) para poder reutilizarlo también desde el
        refresco "bajo demanda" de partidos huérfanos
        (_try_resolve_orphan_tournament_id), que solo necesita
        refrescar el tour_type del partido en cuestión, no ambos.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        url = f"{self.base_url}{self.FIXTURES_PATH_TEMPLATE.format(tour_type=tour_type, date=today)}"
        try:
            client = self._http_client or httpx_module
            response = client.get(url, headers=self._headers(), timeout=10.0)
            response.raise_for_status()
            payload = response.json()
        except httpx_module.RequestError as exc:
            logger.warning("Error de red refrescando fixtures de %s: %s", tour_type, exc)
            return False
        except httpx_module.HTTPStatusError as exc:
            logger.warning(
                "RapidAPI devolvió error (%s) refrescando fixtures de %s",
                exc.response.status_code, tour_type,
            )
            return False
        except json.JSONDecodeError as exc:
            logger.warning("JSON malformado en fixtures de %s: %s", tour_type, exc)
            return False

        fixtures = payload.get("data", [])
        if isinstance(fixtures, list):
            self.opening_odds_tracker.store_fixtures(tour_type, fixtures)
        return True

    def _try_resolve_orphan_tournament_id(
        self, httpx_module, participant1: str, participant2: str, game_id: str, circuit: str
    ) -> Optional[int]:
        """
        Se llama cuando un partido NUEVO no cruzó con ningún fixture
        ya guardado (típicamente porque empezó después del último
        refresco periódico de fixtures). En vez de esperar hasta
        FIXTURES_REFRESH_INTERVAL_SECONDS para el siguiente refresco
        programado, dispara un refresco DIRIGIDO e inmediato solo del
        tour_type de este partido, y reintenta el cruce una vez.

        Respeta opening_odds_tracker.should_attempt_orphan_lookup()
        para no repetir este refresco dirigido en cada ciclo de 15s si
        el partido sigue sin encontrarse tras el primer intento (p.ej.
        es una qualy/ITF de última hora que el proveedor todavía no
        listó en fixtures): en ese caso se aplica un cooldown antes de
        volver a intentarlo.
        """
        if not self.opening_odds_tracker.should_attempt_orphan_lookup(game_id):
            return None

        tour_type = (circuit or "atp").lower()
        if tour_type not in ("atp", "wta"):
            tour_type = "atp"

        logger.info(
            "Partido huérfano detectado (%s vs %s, game_id=%s): "
            "disparando refresco dirigido de fixtures (%s) en lugar de esperar al próximo ciclo periódico.",
            participant1, participant2, game_id, tour_type,
        )

        refreshed_ok = self._refresh_fixtures_for_tour(httpx_module, tour_type)
        self.opening_odds_tracker.mark_orphan_lookup_attempted(game_id)

        if not refreshed_ok:
            return None

        tournament_id = self.opening_odds_tracker.find_tournament_id_for_match(participant1, participant2)
        if tournament_id is None:
            logger.debug(
                "Refresco dirigido no encontró fixture para %s vs %s (game_id=%s); "
                "se reintentará tras el cooldown configurado.",
                participant1, participant2, game_id,
            )
        return tournament_id

    def _build_normalized_match(self, httpx_module, raw_event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Resuelve las cuotas de un evento (apertura + en vivo) y
        construye el diccionario plano para _safe_parse_match().

        Lógica de resolución de odds_prematch_*:
            - Si hay opening_odds_tracker inyectado: usar la cuota de
              apertura ya guardada para este game_id si existe; si no
              existe todavía, intentar capturarla ahora (cruzando con
              fixtures) usando la cuota en vivo actual como aproximación.
              Si tampoco se puede capturar (no se encontró fixture
              correspondiente todavía), se deja sin cuota pre-partido:
              _safe_parse_match() descartará este partido en este ciclo.
            - Si NO hay tracker inyectado (uso simple/tests): se cae al
              comportamiento anterior, menos preciso, de usar
              directamente el movimiento más antiguo de los últimos 10
              como aproximación de pre-partido (sesgado en partidos que
              ya llevan tiempo jugándose, ver docstring de la clase).
        """
        game_id = raw_event.get("id")
        participant1 = raw_event.get("participant1", "")
        participant2 = raw_event.get("participant2", "")
        circuit = raw_event.get("tourType", "atp")

        odds_movements = self._fetch_odds_for_event(httpx_module, game_id) if self.fetch_odds else {}
        live_odds_home = odds_movements.get("live_odds_home")
        live_odds_away = odds_movements.get("live_odds_away")

        tournament_id: Optional[int] = None

        if self.opening_odds_tracker is not None:
            opening = self.opening_odds_tracker.get_opening_odds(game_id)
            if opening is None:
                opening = self.opening_odds_tracker.try_capture_opening_odds(
                    game_id=game_id,
                    participant1=participant1,
                    participant2=participant2,
                    live_odds_home=live_odds_home,
                    live_odds_away=live_odds_away,
                )
            odds_prematch_home = opening.get("odds_prematch_home") if opening else None
            odds_prematch_away = opening.get("odds_prematch_away") if opening else None
            tournament_id = opening.get("tournament_id") if opening else None

            # Si todavía no hay cuota de apertura capturada (p.ej. esta
            # casa de apuestas aún no tiene movimientos), igualmente se
            # intenta resolver el tournament_id por separado: la
            # categoría del torneo no depende de tener cuotas.
            if tournament_id is None:
                tournament_id = self.opening_odds_tracker.find_tournament_id_for_match(participant1, participant2)

            # Si el cruce sigue sin encontrar nada (partido "huérfano":
            # probablemente empezó después del último refresco
            # periódico de fixtures), se dispara un refresco dirigido
            # inmediato en vez de esperar hasta el próximo ciclo
            # programado (hasta 30 min). Sujeto a cooldown para no
            # martillear la API si el partido realmente no tiene
            # fixture todavía.
            if tournament_id is None:
                tournament_id = self._try_resolve_orphan_tournament_id(
                    httpx_module, participant1, participant2, game_id, circuit
                )
        else:
            # Comportamiento simple sin tracker (ver docstring): el
            # movimiento más antiguo de los últimos 10 ya resuelto por
            # _fetch_odds_for_event, expuesto como "opening_*".
            odds_prematch_home = odds_movements.get("opening_odds_home")
            odds_prematch_away = odds_movements.get("opening_odds_away")

        tournament_category, surface = self._resolve_tournament_category_and_surface(
            httpx_module, tournament_id, circuit
        )

        return self._normalize_raw_match(
            raw_event,
            {
                "odds_prematch_home": odds_prematch_home,
                "odds_prematch_away": odds_prematch_away,
                "live_odds_home": live_odds_home,
                "live_odds_away": live_odds_away,
            },
            tournament_category_override=tournament_category,
            surface_override=surface,
        )

    def _resolve_tournament_category_and_surface(
        self, httpx_module, tournament_id: Optional[int], circuit: str
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Resuelve (categoría_canónica, superficie) para un torneo dado
        su tournament_id, usando el caché en MongoDB si está disponible
        (vía opening_odds_tracker) y, si no, consultando
        /tennis/v2/{type}/tournament/info/{id} y cacheando el resultado.

        Devuelve (None, None) si no hay tracker, no hay tournament_id
        resuelto, o la consulta falla — en cuyo caso _normalize_raw_match
        cae a su comportamiento previo (heurística por nombre de liga,
        sin superficie).
        """
        if self.opening_odds_tracker is None or tournament_id is None:
            return None, None

        cached = self.opening_odds_tracker.get_cached_tournament_info(tournament_id)
        if cached is not None:
            category = normalize_category_from_rank_id(cached.get("rank_id"))
            return category, cached.get("surface") or None

        info = self._fetch_tournament_info(httpx_module, tournament_id, circuit)
        if info is None:
            return None, None

        self.opening_odds_tracker.cache_tournament_info(
            tournament_id=tournament_id,
            rank_id=info.get("rank_id"),
            surface=info.get("surface", ""),
            tournament_name=info.get("tournament_name", ""),
        )
        category = normalize_category_from_rank_id(info.get("rank_id"))
        return category, info.get("surface") or None

    def _fetch_tournament_info(
        self, httpx_module, tournament_id: int, circuit: str
    ) -> Optional[Dict[str, Any]]:
        """
        Consulta /tennis/v2/{type}/tournament/info/{id} y devuelve
        {"rank_id": int, "surface": str, "tournament_name": str}.

        Estructura REAL confirmada de la respuesta:
            {"data": {"id": 21812, "name": "M15 Claremont",
                       "rankId": 0, "tier": "Future",
                       "court": {"id": 1, "name": "Hard"}, ...}}

        El "type" (atp/wta) en la URL es obligatorio en este producto;
        se usa el circuit del propio partido. Si la categoría real
        del torneo requiriera el tour contrario (caso raro de eventos
        mixtos), esta llamada simplemente fallaría con 404 y se
        devolvería None sin romper el ciclo.
        """
        tour_type = (circuit or "atp").lower()
        if tour_type not in ("atp", "wta"):
            tour_type = "atp"

        url = f"{self.base_url}{self.TOURNAMENT_INFO_PATH_TEMPLATE.format(tour_type=tour_type, tournament_id=tournament_id)}"
        try:
            client = self._http_client or httpx_module
            response = client.get(url, headers=self._headers(), timeout=10.0)
            response.raise_for_status()
            payload = response.json()
        except httpx_module.RequestError as exc:
            logger.warning("Error de red consultando tournament/info/%s: %s", tournament_id, exc)
            return None
        except httpx_module.HTTPStatusError as exc:
            logger.debug(
                "tournament/info/%s devolvió error (%s) con tour_type=%s",
                tournament_id, exc.response.status_code, tour_type,
            )
            return None
        except json.JSONDecodeError as exc:
            logger.warning("JSON malformado en tournament/info/%s: %s", tournament_id, exc)
            return None

        data = payload.get("data")
        if not isinstance(data, dict):
            return None

        court = data.get("court") or {}
        raw_surface_name = court.get("name", "")
        translated_surface = SURFACE_NAME_TRANSLATIONS.get(raw_surface_name.strip().lower(), raw_surface_name)

        return {
            "rank_id": data.get("rankId"),
            "surface": translated_surface,
            "tournament_name": data.get("name", ""),
        }

    # ------------------------------------------------------------------
    # Llamadas HTTP
    # ------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {
            "X-RapidAPI-Key": self.api_key,
            "X-RapidAPI-Host": self.api_host,
            "Content-Type": "application/json",
        }

    def _fetch_live_events(self, httpx_module) -> List[Dict[str, Any]]:
        url = f"{self.base_url}{self.live_endpoint}"
        try:
            client = self._http_client or httpx_module
            response = client.get(url, headers=self._headers(), timeout=10.0)
            response.raise_for_status()
            payload = response.json()
        except httpx_module.RequestError as exc:
            raise DataProviderError(f"Error de red consultando RapidAPI (events/live): {exc}") from exc
        except httpx_module.HTTPStatusError as exc:
            # Status 429 es habitual al agotar los créditos gratuitos del tier.
            raise DataProviderError(
                f"RapidAPI devolvió un status de error ({exc.response.status_code}) en events/live: {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise DataProviderError(f"Respuesta de RapidAPI con JSON malformado (events/live): {exc}") from exc

        if not payload.get("success", True):
            logger.warning("RapidAPI events/live respondió success=False: %s", payload.get("message"))
            return []

        results = payload.get("results", [])
        return results if isinstance(results, list) else []

    def _fetch_odds_for_event(self, httpx_module, event_id: Optional[str]) -> Dict[str, Optional[float]]:
        """
        Consulta el endpoint de movimientos de cuota para un partido
        concreto y devuelve la cuota más reciente (live) y la más
        antigua de los últimos 10 movimientos disponibles (opening_*,
        usada solo como aproximación cuando no hay opening_odds_tracker).

        Estructura REAL confirmada de la respuesta
        (GET /tennis/v2/extend/api/odds/summary/movements/last-10/{id}):

            {
              "success": true,
              "result": {
                "Bet365": {
                  "Full Time Result": [
                    {"od1": "4.000", "od2": "1.222", "odx": null,
                     "line": "", "sourceAddTime": "1782373253"},
                    ...
                  ]
                }
              }
            }

        Notas confirmadas empíricamente (ver README.md):
            - "result" (no "results"), agrupado por casa de apuestas
              y luego por mercado ("Full Time Result" = ganador del
              partido, que es lo que necesita este bot).
            - od1/od2 son STRINGS, no números: hay que convertir con
              float() explícitamente.
            - El array está ordenado por sourceAddTime DESCENDENTE
              (el primer elemento es el movimiento más reciente).
            - Es una ventana de los ÚLTIMOS 10 movimientos, no el
              historial completo: para partidos que ya llevan tiempo
              jugándose, ni el movimiento más antiguo de estos 10 es
              fiable como cuota pre-partido (por eso existe
              opening_odds_tracker.py como mecanismo principal).
            - Si el partido no tiene movimientos todavía, o la casa de
              apuestas/mercado esperados no están presentes, se
              devuelven cuotas vacías (None) en lugar de fallar.
        """
        empty = {
            "opening_odds_home": None,
            "opening_odds_away": None,
            "live_odds_home": None,
            "live_odds_away": None,
        }
        if not event_id:
            return empty

        url = f"{self.base_url}{self.ODDS_MOVEMENTS_PATH_TEMPLATE.format(event_id=event_id)}"
        try:
            client = self._http_client or httpx_module
            response = client.get(url, headers=self._headers(), timeout=10.0)
            response.raise_for_status()
            payload = response.json()
        except httpx_module.RequestError as exc:
            logger.warning("Error de red consultando cuotas del evento %s: %s", event_id, exc)
            return empty
        except httpx_module.HTTPStatusError as exc:
            logger.warning(
                "RapidAPI devolvió error (%s) consultando cuotas del evento %s",
                exc.response.status_code,
                event_id,
            )
            return empty
        except json.JSONDecodeError as exc:
            logger.warning("JSON malformado en cuotas del evento %s: %s", event_id, exc)
            return empty

        if not payload.get("success", True):
            logger.debug("odds/summary/movements respondió success=False para evento %s", event_id)
            return empty

        movements = self._extract_full_time_result_movements(payload.get("result", {}))
        if not movements:
            return empty

        latest = movements[0]   # más reciente (mayor sourceAddTime)
        oldest = movements[-1]  # más antiguo de los últimos 10 (NO es la apertura real, ver docstring)

        try:
            return {
                "opening_odds_home": float(oldest["od1"]),
                "opening_odds_away": float(oldest["od2"]),
                "live_odds_home": float(latest["od1"]),
                "live_odds_away": float(latest["od2"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Estructura/valores inesperados en cuotas del evento %s: %s", event_id, exc)
            return empty

    @staticmethod
    def _extract_full_time_result_movements(result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extrae la lista de movimientos del mercado "Full Time Result"
        (ganador del partido) de la PRIMERA casa de apuestas disponible
        en "result" (en los datos vistos, solo aparece "Bet365"; si en
        el futuro aparecen varias casas, se prioriza la primera que
        tenga ese mercado, ya que el motor de reglas solo necesita una
        cuota de referencia, no comparar entre casas).
        """
        if not isinstance(result, dict):
            return []
        for _bookmaker, markets in result.items():
            if not isinstance(markets, dict):
                continue
            movements = markets.get("Full Time Result")
            if isinstance(movements, list) and movements:
                return movements
        return []

    # ------------------------------------------------------------------
    # Normalización
    # ------------------------------------------------------------------

    def _normalize_raw_match(
        self,
        raw_event: Dict[str, Any],
        odds: Dict[str, Optional[float]],
        tournament_category_override: Optional[str] = None,
        surface_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Traduce un elemento de "results" de events/live (+ sus cuotas
        ya resueltas aparte) al formato plano interno que espera
        _safe_parse_match().

        tournament_category_override / surface_override: cuando se
        pudo resolver el tournament_id real del partido (vía
        opening_odds_tracker, cruzando con fixtures) y consultar/cachear
        /tournament/info/{id}, estos parámetros llevan la categoría
        REAL (derivada del rankId oficial) y la superficie REAL del
        torneo. Si son None (no se pudo resolver el tournament_id
        todavía, o no hay opening_odds_tracker inyectado), se cae al
        comportamiento previo: inferir categoría por el nombre de la
        liga (heurística, solo distingue ITF) y dejar la superficie
        vacía.

        NOTA IMPORTANTE: si no se pudieron obtener cuotas reales para
        este partido (ver _fetch_odds_for_event), se devuelve None en
        los campos de cuota. _safe_parse_match() los convierte con
        float(...), lo que lanzará ValueError/TypeError para None y
        hará que ese partido se descarte limpiamente en ese ciclo
        (en vez de generar una alerta basada en una cuota inventada).
        Esto es intencional: preferimos perder un partido sin cuota a
        arriesgar un falso positivo en el motor de reglas.
        """
        current_set, games_home, games_away = self._parse_score(raw_event.get("score", ""))
        server_side = self._parse_indicator(raw_event.get("indicator", ""))
        league_name = raw_event.get("league", "")

        tournament_category = tournament_category_override or self._infer_tournament_category(league_name)
        surface = surface_override or ""

        return {
            "game_id": raw_event.get("id"),
            "circuit": raw_event.get("tourType", ""),
            "surface": surface,
            "is_indoor": False,  # no disponible en ningún endpoint confirmado todavía
            "tournament_name": league_name,
            "tournament_category": tournament_category,
            "player_home": raw_event.get("participant1", ""),
            "player_away": raw_event.get("participant2", ""),
            "odds_prematch_home": odds.get("odds_prematch_home"),
            "odds_prematch_away": odds.get("odds_prematch_away"),
            "current_set": current_set,
            "current_games_home": games_home,
            "current_games_away": games_away,
            "current_score_points": raw_event.get("points", "0-0"),
            "current_server": server_side,
            "live_odds_home": odds.get("live_odds_home"),
            "live_odds_away": odds.get("live_odds_away"),
        }

    @staticmethod
    def _parse_score(raw_score: str) -> tuple[int, int, int]:
        """
        Convierte el formato real de "score" (p.ej. "4-6,1-3") en
        (current_set, games_home, games_away) del set EN CURSO (el
        último de la lista separada por comas). Sets ya finalizados
        (anteriores) se ignoran a propósito, porque el motor de reglas
        solo opera sobre el set actual.

        Ejemplos:
            "0-0"        -> (1, 0, 0)
            "4-6,0-1"    -> (2, 0, 1)
            "7-6,5-7,0-4"-> (3, 0, 4)
        """
        if not raw_score:
            return 1, 0, 0

        sets = [s for s in raw_score.split(",") if s.strip()]
        if not sets:
            return 1, 0, 0

        current_set_number = len(sets)
        current_set_score = sets[-1]

        try:
            games_home_str, games_away_str = current_set_score.split("-")
            return current_set_number, int(games_home_str), int(games_away_str)
        except (ValueError, IndexError):
            logger.warning("Formato de score inesperado: %r", raw_score)
            return current_set_number, 0, 0

    @staticmethod
    def _parse_indicator(raw_indicator: str) -> str:
        """
        Traduce "indicator" ("1,0" / "0,1" / "0,0") a "home"/"away".
        Ver docstring de la clase: hipótesis NO confirmada
        oficialmente, solo deducida empíricamente. Si algún día se
        confirma que está invertida, basta cambiar el "home"/"away"
        de las dos primeras ramas.
        """
        if raw_indicator == "1,0":
            return "home"
        if raw_indicator == "0,1":
            return "away"
        # "0,0" u otro valor no reconocido: sin información de saque
        # todavía (visto en partidos recién listados en 0-0 total).
        # Se asume "home" como default neutro; no afecta al resultado
        # del Filtro 3 salvo en hierba, y en 0-0 total el Filtro 2
        # (bache de break) tampoco se cumpliría aún de todas formas.
        return "home"

    @classmethod
    def _infer_tournament_category(cls, league_name: str) -> str:
        """
        Este endpoint no informa la categoría de torneo directamente.
        Se infiere de forma heurística a partir del nombre (p.ej.
        "M15 Wuning" -> ITF). Devuelve un string que luego pasa por
        normalize_tournament_category() de todas formas, así que basta
        devolver "ITF" o "" (-> "Desconocido") aquí.
        """
        if not league_name:
            return ""
        prefix = league_name.split(" ")[0].upper()
        if prefix in cls._ITF_LEAGUE_PREFIXES:
            return "ITF"
        return ""  # se normalizará a "Desconocido" en tournament_priority.py
