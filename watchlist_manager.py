"""
watchlist_manager.py
----------------------
Componente central del "modo de prueba manual": en vez de vigilar
todos los partidos en vivo continuamente (caro en requests), el bot
presenta los partidos que YA cumplen el bache+cuota como candidatos en
Telegram, y solo empieza a vigilar de cerca (cada
MANUAL_MODE_WATCH_INTERVAL_SECONDS, p.ej. 5 minutos) aquellos que el
usuario elige explícitamente respondiendo ".{game_id}" en el chat.

Flujo:
    1. trading_engine detecta un partido que cumple el bache (como ya
       hacía). En vez de notificarlo y persistirlo directamente, se
       pasa por WatchlistManager.offer_candidate(), que lo envía a
       Telegram como candidato con su código de selección.
    2. Cada ciclo, WatchlistManager.process_incoming_selections() lee
       los mensajes nuevos de Telegram (vía
       TelegramNotifier.get_new_selection_commands()) y, si el texto
       es ".{game_id}" de un candidato ofrecido recientemente, lo
       mueve a la "watchlist" (seguimiento activo).
    3. Una vez en la watchlist, en cada ciclo de seguimiento (más
       lento que el de detección) se compara el marcador actual contra
       el último visto para ese partido: si el favorito ha sufrido un
       NUEVO break (deficit de juegos aumentó), se envía
       send_break_alert() y se persiste igual que una alerta normal.

Todo el estado (candidatos ofrecidos, partidos en seguimiento, último
marcador visto) vive en memoria del proceso: es deliberadamente simple
para un modo de prueba manual, no pensado para sobrevivir reinicios.
Si el bot se reinicia, se pierden los candidatos pendientes de elegir
(habría que esperar a que reaparezcan en el siguiente ciclo de
detección) y el seguimiento ya confirmado de partidos en curso.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from models import AlertEvent
from notifier import TelegramNotifier

logger = logging.getLogger(__name__)

_SELECTION_PATTERN = re.compile(r"^\.(\S+)")


@dataclass
class WatchedMatch:
    """Estado de un partido en seguimiento activo (ya elegido por el usuario)."""
    game_id: str
    favorite_name: str
    underdog_name: str
    last_games_favorite: int
    last_games_underdog: int
    selected_at: datetime


class WatchlistManager:
    """
    Gestiona el ciclo "candidato ofrecido -> elegido por el usuario ->
    en seguimiento activo -> alerta de nuevo break".
    """

    def __init__(self, notifier: TelegramNotifier, candidate_offer_ttl_seconds: int = 3600):
        self.notifier = notifier
        # Cuánto tiempo sigue siendo válido un código de selección tras
        # ofrecerse como candidato. Pasado este tiempo, si el usuario
        # responde ".{game_id}" para un candidato ya "caducado", se
        # ignora (probablemente el partido ya cambió mucho o terminó).
        self.candidate_offer_ttl_seconds = candidate_offer_ttl_seconds

        # game_id -> momento en que se ofreció como candidato (para
        # poder expirar candidatos viejos y no aceptar selecciones de
        # partidos ya muy desactualizados).
        self._offered_candidates: Dict[str, datetime] = {}
        # game_id -> AlertEvent más reciente del candidato (para poder
        # construir el WatchedMatch inicial cuando se confirme la
        # selección, sin tener que re-evaluar el partido).
        self._last_alert_by_game_id: Dict[str, AlertEvent] = {}
        # game_id -> WatchedMatch, partidos ya en seguimiento activo.
        self._watchlist: Dict[str, WatchedMatch] = {}

    # ------------------------------------------------------------------
    # Oferta de candidatos
    # ------------------------------------------------------------------

    def offer_candidate(self, alert: AlertEvent) -> None:
        """
        Presenta un partido candidato en Telegram. Si ya se había
        ofrecido este mismo game_id antes (y sigue sin elegirse, ni
        caducado), no se reenvía para no saturar el chat con el mismo
        candidato en cada ciclo de detección — solo se actualiza el
        AlertEvent guardado, por si el usuario lo elige más tarde con
        datos más recientes.
        """
        already_offered = alert.game_id in self._offered_candidates
        self._last_alert_by_game_id[alert.game_id] = alert

        if already_offered or alert.game_id in self._watchlist:
            return

        selection_code = f".{alert.game_id}"
        sent_ok = self.notifier.send_candidate_prompt(alert, selection_code)
        if sent_ok:
            self._offered_candidates[alert.game_id] = datetime.now(timezone.utc)
            logger.info("Candidato ofrecido: %s vs %s (game_id=%s)", alert.favorite_name, alert.underdog_name, alert.game_id)
        else:
            logger.warning("No se pudo ofrecer el candidato game_id=%s (fallo de envío a Telegram).", alert.game_id)

    # ------------------------------------------------------------------
    # Procesamiento de selecciones del usuario
    # ------------------------------------------------------------------

    def process_incoming_selections(self) -> None:
        """
        Lee los mensajes nuevos de Telegram y, para cada uno que tenga
        forma ".{game_id}", intenta moverlo de "candidato ofrecido" a
        "en seguimiento activo". Ignora selecciones de game_id no
        ofrecidos o ya caducados, con un mensaje de confirmación o
        error según corresponda.
        """
        commands = self.notifier.get_new_selection_commands()
        for _update_id, text in commands:
            match = _SELECTION_PATTERN.match(text)
            if not match:
                continue
            self._handle_selection(match.group(1))

    def _handle_selection(self, game_id: str) -> None:
        if game_id in self._watchlist:
            logger.debug("Selección repetida para un partido ya en seguimiento (game_id=%s); se ignora.", game_id)
            return

        offered_at = self._offered_candidates.get(game_id)
        if offered_at is None:
            self.notifier.send_plain_message(
                f"⚠️ No reconozco el partido `.{game_id}` (no fue ofrecido como candidato o ya expiró)."
            )
            return

        elapsed = (datetime.now(timezone.utc) - offered_at).total_seconds()
        if elapsed > self.candidate_offer_ttl_seconds:
            self.notifier.send_plain_message(
                f"⚠️ El candidato `.{game_id}` ha caducado (ofrecido hace demasiado tiempo). "
                "Espera a que vuelva a aparecer como candidato si sigue cumpliendo las condiciones."
            )
            self._offered_candidates.pop(game_id, None)
            self._last_alert_by_game_id.pop(game_id, None)
            return

        last_alert = self._last_alert_by_game_id.get(game_id)
        if last_alert is None:
            # No debería pasar (siempre se guarda junto al candidato),
            # pero por robustez se maneja sin romper el ciclo.
            self.notifier.send_plain_message(f"⚠️ No tengo datos recientes del partido `.{game_id}`; inténtalo de nuevo.")
            return

        self._watchlist[game_id] = WatchedMatch(
            game_id=game_id,
            favorite_name=last_alert.favorite_name,
            underdog_name=last_alert.underdog_name,
            last_games_favorite=last_alert.games_favorite,
            last_games_underdog=last_alert.games_underdog,
            selected_at=datetime.now(timezone.utc),
        )
        self._offered_candidates.pop(game_id, None)

        self.notifier.send_plain_message(
            f"✅ Siguiendo *{last_alert.favorite_name} vs {last_alert.underdog_name}* "
            f"cada pocos minutos. Te avisaré si rompen el saque del favorito otra vez."
        )
        logger.info("Partido movido a seguimiento activo: %s (game_id=%s)", last_alert.favorite_name, game_id)

    # ------------------------------------------------------------------
    # Seguimiento activo: detección de nuevo break
    # ------------------------------------------------------------------

    def is_watched(self, game_id: str) -> bool:
        return game_id in self._watchlist

    def get_watched_game_ids(self) -> List[str]:
        return list(self._watchlist.keys())

    def check_for_new_break(self, alert: AlertEvent) -> Optional[AlertEvent]:
        """
        Compara el marcador actual de un partido en seguimiento contra
        el último marcador visto. Si el déficit de juegos del favorito
        aumentó (le rompieron el saque otra vez), envía send_break_alert
        y actualiza el marcador guardado. Devuelve el AlertEvent si se
        disparó una alerta de break nuevo, o None si no hubo cambio
        relevante.

        Acepta un AlertEvent (mismo objeto que ya usa el resto del
        bot) porque ya contiene games_favorite/games_underdog y las
        cuotas actuales; no hace falta reevaluar todo el trading_engine
        para esto, solo comparar el marcador.
        """
        watched = self._watchlist.get(alert.game_id)
        if watched is None:
            return None

        new_deficit = alert.games_underdog - alert.games_favorite
        old_deficit = watched.last_games_underdog - watched.last_games_favorite

        watched.last_games_favorite = alert.games_favorite
        watched.last_games_underdog = alert.games_underdog

        if new_deficit > old_deficit:
            self.notifier.send_break_alert(
                alert,
                games_favorite=alert.games_favorite,
                games_underdog=alert.games_underdog,
            )
            logger.info(
                "Nuevo break detectado en seguimiento: %s vs %s (game_id=%s), deficit %d -> %d",
                watched.favorite_name, watched.underdog_name, watched.game_id, old_deficit, new_deficit,
            )
            return alert

        return None

    def stop_watching(self, game_id: str) -> None:
        """Deja de seguir un partido (p.ej. cuando termina)."""
        self._watchlist.pop(game_id, None)
