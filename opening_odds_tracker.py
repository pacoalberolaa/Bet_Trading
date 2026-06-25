"""
opening_odds_tracker.py
-------------------------
Componente central que resuelve el problema de la "cuota pre-partido":
el endpoint de cuotas en vivo (odds/summary/movements/last-10/{id})
solo devuelve una ventana deslizante de los últimos 10 movimientos,
así que si un partido lleva tiempo jugándose, esos 10 movimientos ya
están contaminados por el propio desarrollo del partido y NO sirven
como cuota de apertura real (esto se confirmó empíricamente: ver
README.md, sección RapidAPI).

Estrategia (decidida junto al usuario):
    1. Refrescar periódicamente (FIXTURES_REFRESH_INTERVAL_SECONDS) los
       fixtures del día desde /tennis/v2/{type}/fixtures/{fecha}, que
       sí incluyen la hora programada de cada partido. Se guardan en
       MongoDB (colección "fixtures").
    2. En cada ciclo de polling, cuando aparece un game_id NUEVO en
       events/live (nunca visto antes), buscar entre los fixtures
       guardados cuál corresponde a ese partido (cruce por nombres de
       jugador, ver player_name_matching.py, ya que los ids de
       fixtures y de events/live NO son el mismo identificador).
    3. Si se encuentra, capturar en ese instante la cuota vía
       odds/summary/movements y guardarla PARA SIEMPRE en MongoDB
       (colección "opening_odds") como la cuota de apertura de ese
       partido. Esta es una aproximación (la cuota en el momento en
       que el bot detectó el partido, no la cuota exacta al segundo
       de empezar), pero es la mejor disponible con este proveedor.
    4. En ciclos posteriores, ese mismo game_id ya tiene su cuota de
       apertura guardada: se reutiliza sin volver a calcularla.

Limitación conocida y aceptada: si el bot arranca cuando un partido
YA lleva tiempo jugándose, nunca se podrá recuperar su cuota de
apertura real (se perdió esa oportunidad). Ese partido se descartará
limpiamente en _safe_parse_match() por falta de odds_prematch_*, igual
que cualquier otro dato incompleto.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, Optional

from pymongo import MongoClient
from pymongo.errors import PyMongoError

from player_name_matching import find_matching_fixture

logger = logging.getLogger(__name__)


class OpeningOddsTracker:
    """
    Gestiona las colecciones MongoDB de fixtures y de cuotas de
    apertura ya capturadas, y orquesta el flujo de "detectar partido
    nuevo -> buscar su fixture -> capturar y guardar su cuota inicial".
    """

    def __init__(
        self,
        mongo_uri: str,
        db_name: str,
        fixtures_collection: str,
        opening_odds_collection: str,
        tournament_info_collection: str = "tournament_info",
        fixtures_refresh_interval_seconds: int = 1800,
        orphan_retry_cooldown_seconds: int = 120,
        server_selection_timeout_ms: int = 5000,
    ):
        self.fixtures_refresh_interval_seconds = fixtures_refresh_interval_seconds
        self._last_fixtures_refresh: Optional[datetime] = None

        # Cooldown EN MEMORIA (no persistido) para partidos "huérfanos":
        # aquellos cuyo cruce con fixtures falló incluso tras un
        # refresco bajo demanda. Evita martillear la API con un
        # refresco dirigido en cada ciclo de polling si el partido
        # realmente no tiene fixture (qualy de última hora, datos
        # inconsistentes del proveedor, etc). No necesita sobrevivir
        # a un reinicio del bot: el coste de un reintento extra tras
        # reiniciar es insignificante.
        self.orphan_retry_cooldown_seconds = orphan_retry_cooldown_seconds
        self._orphan_last_attempt: Dict[str, datetime] = {}

        self._client: Optional[MongoClient] = None
        self._fixtures_col = None
        self._opening_odds_col = None
        self._tournament_info_col = None

        self._mongo_uri = mongo_uri
        self._db_name = db_name
        self._fixtures_collection_name = fixtures_collection
        self._opening_odds_collection_name = opening_odds_collection
        self._tournament_info_collection_name = tournament_info_collection

        self._connect(server_selection_timeout_ms)

    # ------------------------------------------------------------------
    # Conexión (mismo patrón defensivo que logger.AlertLogger)
    # ------------------------------------------------------------------

    def _connect(self, server_selection_timeout_ms: int) -> None:
        try:
            self._client = MongoClient(self._mongo_uri, serverSelectionTimeoutMS=server_selection_timeout_ms)
            self._client.admin.command("ping")

            db = self._client[self._db_name]
            self._fixtures_col = db[self._fixtures_collection_name]
            self._opening_odds_col = db[self._opening_odds_collection_name]
            self._tournament_info_col = db[self._tournament_info_collection_name]

            self._fixtures_col.create_index("tour_type")
            self._fixtures_col.create_index("fetched_date")
            self._opening_odds_col.create_index("game_id", unique=True)
            self._tournament_info_col.create_index("tournament_id", unique=True)

            logger.info("OpeningOddsTracker conectado a MongoDB (db=%s).", self._db_name)
        except PyMongoError as exc:
            logger.error(
                "OpeningOddsTracker no pudo conectar a MongoDB en %s: %s. "
                "El seguimiento de cuotas de apertura no funcionará hasta "
                "que la conexión se restablezca.",
                self._mongo_uri,
                exc,
            )
            self._client = None
            self._fixtures_col = None
            self._opening_odds_col = None
            self._tournament_info_col = None

    def _ensure_connected(self) -> bool:
        if self._fixtures_col is not None and self._opening_odds_col is not None and self._tournament_info_col is not None:
            return True
        self._connect(server_selection_timeout_ms=3000)
        return self._fixtures_col is not None and self._opening_odds_col is not None and self._tournament_info_col is not None

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            logger.info("Conexión MongoDB de OpeningOddsTracker cerrada.")

    # ------------------------------------------------------------------
    # Refresco de fixtures
    # ------------------------------------------------------------------

    def should_refresh_fixtures(self) -> bool:
        """True si ha pasado suficiente tiempo desde el último refresco."""
        if self._last_fixtures_refresh is None:
            return True
        elapsed = (datetime.now(timezone.utc) - self._last_fixtures_refresh).total_seconds()
        return elapsed >= self.fixtures_refresh_interval_seconds

    def store_fixtures(self, tour_type: str, fixtures: list) -> None:
        """
        Guarda (reemplazando los previos del mismo tour_type) la lista
        de fixtures recién obtenida de la API. Se llama típicamente una
        vez por tour (atp/wta) cada vez que toca refrescar.

        Cada fixture se normaliza a un documento con player1_name /
        player2_name (los nombres "planos" que espera
        player_name_matching.find_matching_fixture), conservando el
        resto de campos originales por si hacen falta más adelante.
        """
        if not self._ensure_connected():
            logger.warning("No se pudieron guardar fixtures de %s: MongoDB no disponible.", tour_type)
            return

        now = datetime.now(timezone.utc)
        documents = []
        for fixture in fixtures:
            player1 = fixture.get("player1") or {}
            player2 = fixture.get("player2") or {}
            documents.append({
                "tour_type": tour_type,
                "fixture_id": fixture.get("id"),
                "player1_name": player1.get("name", ""),
                "player2_name": player2.get("name", ""),
                "date": fixture.get("date"),
                "tournament_id": fixture.get("tournamentId"),
                "fetched_date": now,
            })

        try:
            # Se borran los fixtures previos de este tour_type antes de
            # insertar los nuevos, para no acumular partidos ya jugados
            # indefinidamente en la colección.
            self._fixtures_col.delete_many({"tour_type": tour_type})
            if documents:
                self._fixtures_col.insert_many(documents)
            self._last_fixtures_refresh = now
            logger.info("Fixtures de %s refrescados: %d partidos guardados.", tour_type, len(documents))
        except PyMongoError as exc:
            logger.error("Fallo guardando fixtures de %s en MongoDB: %s", tour_type, exc)

    def _get_all_fixtures(self) -> list:
        if not self._ensure_connected():
            return []
        try:
            return list(self._fixtures_col.find({}))
        except PyMongoError as exc:
            logger.error("Fallo leyendo fixtures de MongoDB: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Cuota de apertura: lectura y captura
    # ------------------------------------------------------------------

    def has_opening_odds(self, game_id: str) -> bool:
        """True si ya se capturó (y guardó) la cuota de apertura de este partido."""
        if not self._ensure_connected():
            return False
        try:
            return self._opening_odds_col.find_one({"game_id": game_id}) is not None
        except PyMongoError as exc:
            logger.error("Fallo comprobando cuota de apertura de %s: %s", game_id, exc)
            return False

    def get_opening_odds(self, game_id: str) -> Optional[Dict[str, float]]:
        """
        Devuelve {"odds_prematch_home": ..., "odds_prematch_away": ...,
        "tournament_id": ...} si ya está guardada, o None si no hay
        registro para este game_id todavía. "tournament_id" puede ser
        None si en su momento no se pudo determinar.
        """
        if not self._ensure_connected():
            return None
        try:
            doc = self._opening_odds_col.find_one({"game_id": game_id})
            if doc is None:
                return None
            return {
                "odds_prematch_home": doc.get("odds_prematch_home"),
                "odds_prematch_away": doc.get("odds_prematch_away"),
                "tournament_id": doc.get("tournament_id"),
            }
        except PyMongoError as exc:
            logger.error("Fallo leyendo cuota de apertura de %s: %s", game_id, exc)
            return None

    def should_attempt_orphan_lookup(self, game_id: str) -> bool:
        """
        True si, para este game_id, ha pasado suficiente tiempo desde
        el último intento de resolución "bajo demanda" (o nunca se ha
        intentado). Se usa para no disparar un refresco dirigido de
        fixtures en cada ciclo de polling si el partido sigue sin
        encontrarse — limita el coste a un reintento cada
        orphan_retry_cooldown_seconds, no en cada ciclo de 15s.
        """
        last_attempt = self._orphan_last_attempt.get(game_id)
        if last_attempt is None:
            return True
        elapsed = (datetime.now(timezone.utc) - last_attempt).total_seconds()
        return elapsed >= self.orphan_retry_cooldown_seconds

    def mark_orphan_lookup_attempted(self, game_id: str) -> None:
        """Registra que se acaba de intentar (con o sin éxito) resolver este game_id bajo demanda."""
        self._orphan_last_attempt[game_id] = datetime.now(timezone.utc)

    def find_tournament_id_for_match(
        self, participant1: str, participant2: str
    ) -> Optional[int]:
        """
        Busca el tournament_id correspondiente a un partido cruzando
        sus nombres de jugador contra los fixtures guardados. A
        diferencia de try_capture_opening_odds(), esto NO depende de
        tener cuotas en vivo disponibles: la categoría de un torneo es
        información independiente de si se pudo o no capturar la
        cuota de apertura, así que se expone como método aparte para
        poder resolver la categoría incluso en partidos donde la
        captura de cuota todavía no tuvo éxito.
        """
        if not self._ensure_connected():
            return None
        fixtures = self._get_all_fixtures()
        matching_fixture = find_matching_fixture(participant1, participant2, fixtures)
        if matching_fixture is None:
            return None
        return matching_fixture.get("tournament_id")

    def try_capture_opening_odds(
        self,
        game_id: str,
        participant1: str,
        participant2: str,
        live_odds_home: Optional[float],
        live_odds_away: Optional[float],
    ) -> Optional[Dict[str, float]]:
        """
        Intenta resolver y guardar la cuota de apertura de un partido
        NUEVO (que no tiene registro todavía en opening_odds).

        Pasos:
            1. Busca entre los fixtures guardados uno cuyos jugadores
               coincidan con (participant1, participant2).
            2. Si lo encuentra, usa las cuotas EN VIVO actuales
               (live_odds_home/away, ya resueltas por el llamador vía
               odds/summary/movements) como mejor aproximación
               disponible a la cuota de apertura, y las guarda de
               forma permanente para este game_id (junto al
               tournament_id del fixture, para poder resolver luego la
               categoría real sin tener que repetir el cruce de
               nombres en cada ciclo).
            3. Si no encuentra fixture correspondiente, no guarda nada
               y devuelve None (este partido se descartará en este
               ciclo por falta de cuota pre-partido; se podrá reintentar
               en el siguiente ciclo si para entonces sí hay fixture).

        Devuelve el dict de cuotas guardadas, o None si no se pudo
        resolver todavía.
        """
        if live_odds_home is None or live_odds_away is None:
            return None

        if not self._ensure_connected():
            return None

        fixtures = self._get_all_fixtures()
        matching_fixture = find_matching_fixture(participant1, participant2, fixtures)

        if matching_fixture is None:
            logger.debug(
                "No se encontró fixture correspondiente para %s vs %s (game_id=%s) todavía.",
                participant1, participant2, game_id,
            )
            return None

        opening_odds = {
            "odds_prematch_home": live_odds_home,
            "odds_prematch_away": live_odds_away,
            "tournament_id": matching_fixture.get("tournament_id"),
        }

        try:
            self._opening_odds_col.insert_one({
                "game_id": game_id,
                "tournament_id": matching_fixture.get("tournament_id"),
                "participant1": participant1,
                "participant2": participant2,
                "fixture_id": matching_fixture.get("fixture_id"),
                "odds_prematch_home": live_odds_home,
                "odds_prematch_away": live_odds_away,
                "captured_at": datetime.now(timezone.utc),
            })
            logger.info(
                "Cuota de apertura capturada para %s vs %s (game_id=%s): %.3f / %.3f",
                participant1, participant2, game_id, live_odds_home, live_odds_away,
            )
            return opening_odds
        except PyMongoError as exc:
            # Posible condición de carrera: si dos ciclos casi
            # simultáneos intentan capturar el mismo game_id, el índice
            # único en game_id hará que el segundo insert falle con
            # DuplicateKeyError (subclase de PyMongoError). Se trata
            # como un fallo benigno: el primero ya lo guardó.
            logger.warning("No se pudo guardar cuota de apertura de %s (game_id=%s): %s", participant1, game_id, exc)
            return None

    # ------------------------------------------------------------------
    # Caché de info de torneo (categoría + superficie, vía rankId real)
    # ------------------------------------------------------------------

    def get_cached_tournament_info(self, tournament_id: int) -> Optional[Dict[str, object]]:
        """
        Devuelve {"rank_id": int, "surface": str, "tournament_name": str}
        si ya se cacheó la info de este torneo, o None si todavía no
        se ha consultado /tennis/v2/{type}/tournament/info/{id} para
        él. La categoría/superficie de un torneo no cambian durante su
        disputa, así que una vez cacheado no hace falta volver a
        pedirlo en cada ciclo de polling.
        """
        if not self._ensure_connected():
            return None
        try:
            doc = self._tournament_info_col.find_one({"tournament_id": tournament_id})
            if doc is None:
                return None
            return {
                "rank_id": doc.get("rank_id"),
                "surface": doc.get("surface", ""),
                "tournament_name": doc.get("tournament_name", ""),
            }
        except PyMongoError as exc:
            logger.error("Fallo leyendo caché de info de torneo %s: %s", tournament_id, exc)
            return None

    def cache_tournament_info(
        self, tournament_id: int, rank_id: Optional[int], surface: str, tournament_name: str
    ) -> None:
        """
        Guarda (o reemplaza) la info resuelta de un torneo, para no
        tener que volver a pedir /tournament/info/{id} en próximos
        ciclos. Se usa upsert porque, en teoría, la categoría de un
        torneo no debería cambiar, pero por robustez se permite
        sobreescribir si alguna vez se vuelve a llamar.
        """
        if not self._ensure_connected():
            logger.warning("No se pudo cachear info del torneo %s: MongoDB no disponible.", tournament_id)
            return
        try:
            self._tournament_info_col.update_one(
                {"tournament_id": tournament_id},
                {"$set": {
                    "tournament_id": tournament_id,
                    "rank_id": rank_id,
                    "surface": surface,
                    "tournament_name": tournament_name,
                    "cached_at": datetime.now(timezone.utc),
                }},
                upsert=True,
            )
        except PyMongoError as exc:
            logger.error("Fallo cacheando info del torneo %s: %s", tournament_id, exc)
