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
from typing import Any, Dict, List, Optional

from models import MatchState
from tournament_priority import normalize_tournament_category, sort_matches_by_tournament_priority

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
    Implementación REAL recomendada, contra el patrón estándar de
    RapidAPI (headers X-RapidAPI-Key / X-RapidAPI-Host).

    Como todavía no hay cuenta/producto concreto elegido, este
    proveedor está escrito de forma genérica:
        - Usa httpx para la petición HTTP.
        - Asume una respuesta JSON con una lista de eventos en vivo
          bajo una clave razonable ("results", "events" o "response",
          probadas en ese orden) -- ajustar si el producto real usa
          otra clave.
        - El mapeo campo-a-campo específico del producto se hace en
          _normalize_raw_match(), que es EL ÚNICO sitio que habrá que
          tocar al conectar un producto real de RapidAPI distinto.

    Pasos para activarlo cuando tengas cuenta:
        1. Suscríbete a un producto de tenis en RapidAPI (recomendado:
           buscar "tennis live" en el marketplace y revisar el tier
           gratuito de créditos).
        2. Define las variables de entorno RAPIDAPI_KEY, RAPIDAPI_HOST,
           RAPIDAPI_BASE_URL y RAPIDAPI_LIVE_ENDPOINT (ver config.py),
           copiando los valores exactos que te de el panel de RapidAPI.
        3. Ejecuta una petición de prueba y observa la forma real del
           JSON de respuesta; ajusta _extract_events_list() y
           _normalize_raw_match() a esa estructura concreta.
        4. En main.py, sustituye MockBetsAPIProvider por
           RapidAPITennisProvider(...) en _build_default_bot().
    """

    def __init__(self, api_key: str, api_host: str, base_url: str, live_endpoint: str, http_client=None):
        self.api_key = api_key
        self.api_host = api_host
        self.base_url = base_url.rstrip("/")
        self.live_endpoint = live_endpoint
        # Se inyecta el cliente HTTP (httpx) para poder testear con mocks.
        self._http_client = http_client

    def get_live_matches(self) -> List[MatchState]:
        import httpx  # import local para no forzar la dependencia si no se usa este provider

        url = f"{self.base_url}{self.live_endpoint}"
        headers = {
            "X-RapidAPI-Key": self.api_key,
            "X-RapidAPI-Host": self.api_host,
        }

        try:
            client = self._http_client or httpx
            response = client.get(url, headers=headers, timeout=10.0)
            response.raise_for_status()
            payload = response.json()
        except httpx.RequestError as exc:
            raise DataProviderError(f"Error de red consultando RapidAPI: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            # Status 429 es habitual al agotar los créditos gratuitos del tier.
            raise DataProviderError(
                f"RapidAPI devolvió un status de error ({exc.response.status_code}): {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise DataProviderError(f"Respuesta de RapidAPI con JSON malformado: {exc}") from exc

        raw_events = self._extract_events_list(payload)

        parsed_matches: List[MatchState] = []
        for raw_event in raw_events:
            normalized = self._normalize_raw_match(raw_event)
            match = _safe_parse_match(normalized)
            if match is not None:
                parsed_matches.append(match)

        return sort_matches_by_tournament_priority(parsed_matches)

    @staticmethod
    def _extract_events_list(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Distintos productos de RapidAPI envuelven la lista de eventos
        en vivo bajo claves diferentes. Se prueban las más comunes;
        ajustar/añadir aquí si el producto elegido usa otra.
        """
        for key in ("results", "events", "response", "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
        logger.warning(
            "No se encontró una lista de eventos reconocible en la respuesta de RapidAPI "
            "(claves probadas: results/events/response/data). Revisa _extract_events_list()."
        )
        return []

    @staticmethod
    def _normalize_raw_match(raw_event: Dict[str, Any]) -> Dict[str, Any]:
        """
        TODO (punto único de ajuste): traducir la estructura específica
        del producto de RapidAPI elegido al formato plano interno que
        espera _safe_parse_match(). Se deja un mapeo "best effort" con
        nombres de campo habituales en APIs de tenis, a modo de punto
        de partida razonable; revisar y corregir contra la respuesta
        real del producto contratado.
        """
        return {
            "game_id": raw_event.get("id") or raw_event.get("match_id") or raw_event.get("event_id"),
            "circuit": raw_event.get("tour") or raw_event.get("circuit", ""),
            "surface": raw_event.get("surface", ""),
            "is_indoor": raw_event.get("indoor", False),
            "tournament_name": raw_event.get("tournament_name") or raw_event.get("tournament", ""),
            "tournament_category": raw_event.get("tournament_category") or raw_event.get("category", ""),
            "player_home": raw_event.get("home_player") or raw_event.get("player_home", ""),
            "player_away": raw_event.get("away_player") or raw_event.get("player_away", ""),
            "odds_prematch_home": raw_event.get("odds_prematch_home"),
            "odds_prematch_away": raw_event.get("odds_prematch_away"),
            "current_set": raw_event.get("current_set", 1),
            "current_games_home": raw_event.get("games_home", 0),
            "current_games_away": raw_event.get("games_away", 0),
            "current_score_points": raw_event.get("score_points", "0-0"),
            "current_server": raw_event.get("server", "home"),
            "live_odds_home": raw_event.get("live_odds_home"),
            "live_odds_away": raw_event.get("live_odds_away"),
        }
