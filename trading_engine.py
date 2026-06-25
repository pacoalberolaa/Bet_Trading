"""
trading_engine.py
------------------
Core de inteligencia del bot. Evalúa cada MatchState contra un
conjunto de reglas estrictas para detectar "baches de favorito"
(situaciones donde el favorito pre-partido va perdiendo el primer
set por uno o más breaks, lo cual suele generar una ineficiencia
de cuota explotable en trading).

Diseño de las reglas (en orden de aplicación):
    Filtro 1 - Favoritismo real:
        El jugador debe haber sido favorito CLARO pre-partido,
        con cuota estrictamente entre FAVORITE_ODDS_MIN y FAVORITE_ODDS_MAX.

    Filtro 2 - Bache en el primer set:
        Solo se considera el current_set == 1. Se calcula el
        "déficit de juegos" del favorito (games del rival - games
        del favorito). Un déficit >= 1 break ya es candidato; el
        umbral exacto depende del circuito (ATP vs WTA) y se afina
        en el Filtro 3 según superficie.

    Filtro 3 - Condicional de superficie:
        - Tierra Batida: se permite alertar ya con un solo break
          abajo en fases tempranas del set (ej. 1-2, 1-3).
        - Hierba: más restrictivo. Solo se alerta si el favorito
          está actualmente RESTANDO (current_server == rival) en
          el momento del bache, evitando alertar justo cuando el
          favorito acaba de perder su propio saque en un juego
          crítico al final del set (game >= 7, es decir, posible
          tie-break inminente).
        - Dura: postura intermedia entre tierra y hierba (se exige
          al menos un break claro, sin las restricciones especiales
          de hierba ni la laxitud de tierra).

    Filtro 4 - Anti-duplicados:
        Una vez se dispara una alerta para un game_id, ese partido
        queda "marcado" en memoria y no se vuelve a evaluar en el
        resto del set (se resetea si el bot detecta que ha empezado
        un set nuevo, ya que el contexto de "bache" cambia).
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Set

from config import (
    FAVORITE_ODDS_MIN,
    FAVORITE_ODDS_MAX,
    CIRCUIT_WTA,
    SURFACE_CLAY,
    SURFACE_GRASS,
)
from models import AlertEvent, MatchState

logger = logging.getLogger(__name__)


class TradingEngine:
    """
    Motor de reglas con estado (mantiene en memoria qué partidos ya
    dispararon alerta) para evitar notificaciones repetidas.
    """

    def __init__(self):
        # Guarda, por game_id, el número de set en el que se disparó
        # la última alerta. Esto permite resetear el "ya alertado"
        # cuando empieza un nuevo set, sin volver a alertar dentro
        # del mismo set una vez ya se notificó.
        self._alerted_matches: Dict[str, int] = {}
        # game_ids ya notificados como "candidato detectado" (primera vez
        # que el partido aparece en vivo con cuota de favorito en rango).
        self._seen_candidates: Set[str] = set()

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def evaluate(self, match: MatchState) -> Optional[AlertEvent]:
        """
        Evalúa un único partido. Devuelve un AlertEvent si se cumplen
        todas las condiciones para disparar una alerta nueva, o None
        si no hay alerta (no cumple condiciones, o ya se alertó antes
        para ese mismo set).
        """
        try:
            return self._evaluate_internal(match)
        except Exception:
            # Defensa adicional: un fallo evaluando un partido concreto
            # nunca debe tumbar el bucle principal de polling.
            logger.exception(
                "Error inesperado evaluando el partido %s. Se omite este ciclo.",
                match.game_id,
            )
            return None

    def check_candidate(self, match: MatchState) -> Optional[AlertEvent]:
        """
        Devuelve un AlertEvent la primera vez que un partido aparece en
        vivo con cuota pre-partido del favorito dentro del rango. Las
        llamadas posteriores para el mismo game_id devuelven None.
        """
        if match.game_id in self._seen_candidates:
            return None

        favorite_side = match.favorite_side
        if favorite_side is None:
            return None

        favorite_prematch_odds = match.odds_prematch_for(favorite_side)
        if not (FAVORITE_ODDS_MIN < favorite_prematch_odds < FAVORITE_ODDS_MAX):
            return None

        self._seen_candidates.add(match.game_id)
        underdog_side = match.opponent(favorite_side)

        return AlertEvent(
            game_id=match.game_id,
            circuit=match.circuit,
            surface=match.surface,
            tournament_name=match.tournament_name,
            tournament_category=match.tournament_category,
            favorite_name=match.player_name(favorite_side),
            underdog_name=match.player_name(underdog_side),
            odds_prematch_favorite=favorite_prematch_odds,
            odds_live_favorite=match.live_odds_for(favorite_side),
            current_set=match.current_set,
            games_favorite=match.games_for(favorite_side),
            games_underdog=match.games_for(underdog_side),
            score_points=match.current_score_points,
            reason="Partido candidato detectado (favorito en rango de cuota)",
        )

    def reset_match(self, game_id: str) -> None:
        """Permite forzar el olvido de un partido (ej. al finalizar)."""
        self._alerted_matches.pop(game_id, None)
        self._seen_candidates.discard(game_id)

    # ------------------------------------------------------------------
    # Lógica interna
    # ------------------------------------------------------------------

    def _evaluate_internal(self, match: MatchState) -> Optional[AlertEvent]:
        # --- Filtro 1: Favoritismo real pre-partido ---
        favorite_side = match.favorite_side
        if favorite_side is None:
            return None

        favorite_prematch_odds = match.odds_prematch_for(favorite_side)
        if not (FAVORITE_ODDS_MIN < favorite_prematch_odds < FAVORITE_ODDS_MAX):
            return None  # no es un favorito "real" según nuestro rango

        # --- Filtro 2: Bache exclusivamente en el primer set ---
        if match.current_set != 1:
            return None

        underdog_side = match.opponent(favorite_side)
        games_favorite = match.games_for(favorite_side)
        games_underdog = match.games_for(underdog_side)

        # El favorito tiene que ir POR DETRÁS en el marcador de juegos.
        game_deficit = games_underdog - games_favorite
        if game_deficit <= 0:
            return None  # el favorito no va perdiendo: no hay bache

        is_double_break_situation = self._is_double_break(
            circuit=match.circuit,
            games_favorite=games_favorite,
            games_underdog=games_underdog,
        )
        is_single_break_situation = self._is_single_break(
            games_favorite=games_favorite,
            games_underdog=games_underdog,
        )

        if not (is_single_break_situation or is_double_break_situation):
            return None

        # --- Filtro 3: Condicional de superficie ---
        surface_allows, surface_reason = self._surface_condition(
            surface=match.surface,
            match=match,
            favorite_side=favorite_side,
            games_favorite=games_favorite,
            games_underdog=games_underdog,
        )
        if not surface_allows:
            return None

        # --- Filtro 4: Anti-duplicados (por set) ---
        if self._already_alerted_this_set(match):
            return None

        # Si llegamos aquí, todas las condiciones se cumplen: se construye
        # la alerta y se marca el partido como "ya alertado" en este set.
        self._alerted_matches[match.game_id] = match.current_set

        reason = (
            f"{'Doble break' if is_double_break_situation else 'Break simple'} "
            f"en set 1 ({surface_reason})"
        )

        return AlertEvent(
            game_id=match.game_id,
            circuit=match.circuit,
            surface=match.surface,
            tournament_name=match.tournament_name,
            tournament_category=match.tournament_category,
            favorite_name=match.player_name(favorite_side),
            underdog_name=match.player_name(underdog_side),
            odds_prematch_favorite=favorite_prematch_odds,
            odds_live_favorite=match.live_odds_for(favorite_side),
            current_set=match.current_set,
            games_favorite=games_favorite,
            games_underdog=games_underdog,
            score_points=match.current_score_points,
            reason=reason,
        )

    @staticmethod
    def _is_single_break(games_favorite: int, games_underdog: int) -> bool:
        """
        Un solo break de diferencia: el rival va exactamente 1 juego
        por delante del esperado tras el intercambio de saques.
        Ejemplos válidos: 1-2, 2-3, 1-3 (tras error de saque propio
        + rival sostiene), etc. Se modela de forma simple como
        deficit == 1 o deficit == 2 dentro de un mismo "ciclo" de
        juegos tempranos (se afina por superficie en el Filtro 3).
        """
        deficit = games_underdog - games_favorite
        return deficit in (1, 2)

    @staticmethod
    def _is_double_break(circuit: str, games_favorite: int, games_underdog: int) -> bool:
        """
        Doble break: deficit de 2 breaks reales. Se interpreta como
        un déficit de juegos >= 3 manteniendo proporciones bajas de
        set (es decir, todavía en fase temprana). Para WTA se admite
        explícitamente el patrón 1-4 / 2-5 mencionado en el requisito.
        """
        deficit = games_underdog - games_favorite
        if deficit < 3:
            return False

        if circuit == CIRCUIT_WTA:
            # Patrones explícitos solicitados: 1-4, 2-5
            wta_patterns = {(1, 4), (2, 5)}
            return (games_favorite, games_underdog) in wta_patterns or deficit >= 3

        # ATP u otros circuitos: cualquier déficit >= 3 en fase temprana
        # (se exige que el rival no haya cerrado ya el set, games_underdog < 6)
        return games_underdog < 6

    @staticmethod
    def _surface_condition(
        surface: str,
        match: MatchState,
        favorite_side: str,
        games_favorite: int,
        games_underdog: int,
    ) -> tuple[bool, str]:
        """
        Aplica el Filtro 3 (condicional de superficie) y devuelve
        (permitido: bool, motivo_legible: str).
        """
        deficit = games_underdog - games_favorite

        if surface == SURFACE_CLAY:
            # Tierra batida: laxo, se permite con un solo break abajo
            # en fases tempranas (1-2, 1-3 explícitamente solicitados).
            if games_favorite <= 1 and deficit in (1, 2):
                return True, "tierra batida, bache temprano permitido"
            # También se acepta cualquier bache temprano con deficit >=1
            # siempre que el set siga en fase inicial (underdog < 5).
            if games_underdog < 5:
                return True, "tierra batida, bache temprano"
            return False, "tierra batida, fuera de fase temprana"

        if surface == SURFACE_GRASS:
            # Hierba: restrictivo. Solo si el favorito está RESTANDO
            # en este momento (no es su saque), para evitar alertar
            # justo tras perder su propio servicio en un juego crítico
            # de final de set (game >= 7, cerca de un posible tie-break).
            favorite_is_serving = match.current_server == favorite_side
            if favorite_is_serving:
                return False, "hierba: favorito al servicio, se descarta"

            is_critical_late_game = (games_favorite + games_underdog) >= 7
            if is_critical_late_game:
                return False, "hierba: bache en juego crítico de final de set, se descarta"

            return True, "hierba: favorito restando en bache temprano"

        # Dura (u otras superficies no especificadas): postura intermedia,
        # exige un break claro pero sin las restricciones especiales de
        # hierba ni la laxitud extra de tierra batida.
        if deficit >= 1 and games_underdog < 6:
            return True, "superficie dura: break claro en fase temprana/media"

        return False, "superficie dura: fuera de condiciones"

    def _already_alerted_this_set(self, match: MatchState) -> bool:
        """
        True si ya se disparó una alerta para este partido EN EL MISMO
        set actual. Si el set ha cambiado respecto a la última alerta
        registrada, se considera un contexto nuevo y se permite alertar
        de nuevo (por ejemplo, un bache distinto en el set 2).
        """
        last_alerted_set = self._alerted_matches.get(match.game_id)
        return last_alerted_set == match.current_set
