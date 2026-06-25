"""
notifier.py
-----------
Servicio de mensajería. Encapsula el envío Y LECTURA de mensajes con
Telegram usando peticiones HTTP puras contra la Bot API (sin
dependencia de python-telegram-bot, para mantener el footprint mínimo).

Además del envío de alertas (modo automático original), soporta el
"modo de prueba manual" (ver watchlist_manager.py): presenta partidos
candidatos y lee las respuestas del usuario (mensajes que empiezan por
".") para saber a qué partido seguir de cerca.

Se usa httpx por su soporte nativo de timeouts y por ser fácilmente
intercambiable por requests si se prefiere.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import httpx

from models import AlertEvent

logger = logging.getLogger(__name__)


class TelegramNotifierError(Exception):
    """Error al intentar notificar vía Telegram."""


class TelegramNotifier:
    """Cliente mínimo para enviar y leer mensajes de un chat de Telegram."""

    def __init__(self, bot_token: str, chat_id: str, timeout: float = 10.0):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout
        self._base_url = f"https://api.telegram.org/bot{self.bot_token}"
        # offset de getUpdates: id del último update_id ya procesado + 1.
        # Se mantiene en memoria; si el bot se reinicia, Telegram sigue
        # entregando los updates pendientes (no se pierden, solo se
        # podrían reprocesar algunos ya vistos, lo cual es inocuo aquí
        # porque _parse_selection_command es idempotente por game_id).
        self._update_offset: Optional[int] = None

    def send_match_detected(self, alert: AlertEvent) -> bool:
        """
        Notifica que un partido candidato ha sido detectado en vivo por
        primera vez: el favorito tiene cuota pre-partido en el rango de
        interés. No implica que haya bache todavía; es un aviso previo
        para que el usuario esté atento.
        """
        message = (
            "👀 *PARTIDO CANDIDATO EN VIVO* 👀\n\n"
            f"🏆 *Torneo:* {alert.tournament_name} ({alert.tournament_category})\n"
            f"🎾 *Partido:* {alert.favorite_name} vs {alert.underdog_name} "
            f"({alert.circuit} - {alert.surface})\n"
            f"📈 *Cuota Inicial Favorito:* {alert.odds_prematch_favorite:.2f}\n"
            f"➡️ *Cuota LIVE actual:* {alert.odds_live_favorite:.2f}\n"
            f"📊 *Marcador:* Set {alert.current_set} "
            f"[{alert.games_favorite}-{alert.games_underdog}]\n\n"
            "_Vigilando este partido. Te avisaré si el favorito pierde el saque._"
        )
        return self._send_message(message)

    def send_alert(self, alert: AlertEvent) -> bool:
        """
        Envía la alerta formateada (modo automático original). Devuelve
        True si Telegram confirmó la entrega, False si hubo un fallo
        controlado (se loguea pero no se interrumpe el bucle principal).
        """
        message = self._format_message(alert)
        return self._send_message(message)

    def send_candidate_prompt(self, alert: AlertEvent, selection_code: str) -> bool:
        """
        Envía un partido CANDIDATO (modo de prueba manual): el mismo
        contenido informativo que send_alert, pero con una instrucción
        explícita de cómo seleccionarlo para iniciar el seguimiento.
        """
        message = (
            self._format_message(alert)
            + f"\n\n👉 Responde con *{selection_code}* para seguir este "
            "partido cada 5 minutos."
        )
        return self._send_message(message)

    def send_break_alert(self, alert: AlertEvent, games_favorite: int, games_underdog: int) -> bool:
        """
        Envía la alerta de SEGUIMIENTO: se usa cuando, durante el
        seguimiento activo de un partido ya elegido por el usuario, se
        detecta un NUEVO break en contra del favorito respecto al
        último marcador visto. Mensaje más corto que la alerta inicial,
        pensado para lectura rápida en medio de un seguimiento ya en marcha.
        """
        message = (
            "⚠️ *NUEVO BREAK EN CONTRA DEL FAVORITO* ⚠️\n\n"
            f"🎾 {alert.favorite_name} vs {alert.underdog_name}\n"
            f"📊 Marcador actual: Set {alert.current_set} "
            f"[{games_favorite}-{games_underdog}] ({alert.score_points})\n"
            f"➡️ Cuota LIVE: {alert.odds_live_favorite:.2f}"
        )
        return self._send_message(message)

    def send_plain_message(self, text: str) -> bool:
        """Envía un mensaje de texto plano (sin formato de alerta), p.ej. confirmaciones de seguimiento."""
        return self._send_message(text)

    @staticmethod
    def _format_message(alert: AlertEvent) -> str:
        """
        Construye el mensaje en Markdown siguiendo el formato scannable
        solicitado.
        """
        return (
            "🚨 *ALERTA DE TRADING: BACHE DE FAVORITO* 🚨\n\n"
            f"🏆 *Torneo:* {alert.tournament_name} ({alert.tournament_category})\n"
            f"🎾 *Partido:* {alert.favorite_name} vs {alert.underdog_name} "
            f"({alert.circuit} - {alert.surface})\n"
            f"📈 *Cuota Inicial:* {alert.odds_prematch_favorite:.2f}\n"
            f"➡️ *Cuota LIVE actual:* {alert.odds_live_favorite:.2f}\n"
            f"📊 *Marcador Actual:* Set {alert.current_set} "
            f"[{alert.games_favorite}-{alert.games_underdog}] "
            f"({alert.score_points})\n\n"
            "⚠️ _Estrategia: Entrar en BACK al favorito y buscar cierre "
            "(Cash Out) tras recuperación del break o en el descanso del "
            "set (Stop Loss)._"
        )

    def _send_message(self, text: str) -> bool:
        url = f"{self._base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }

        try:
            response = httpx.post(url, json=payload, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            if not data.get("ok", False):
                logger.error("Telegram respondió ok=False: %s", data)
                return False
            return True

        except httpx.RequestError as exc:
            # Caída de red, timeout, DNS, etc. No relanzamos: el bot
            # debe seguir vivo y reintentar en el siguiente ciclo.
            logger.error("Error de red enviando alerta a Telegram: %s", exc)
            return False
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Telegram devolvió un error HTTP %s: %s",
                exc.response.status_code,
                exc.response.text,
            )
            return False
        except ValueError as exc:
            # JSON malformado en la respuesta de Telegram (poco común, pero posible)
            logger.error("Respuesta de Telegram con JSON inválido: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Lectura de mensajes (modo de prueba manual)
    # ------------------------------------------------------------------

    def get_new_selection_commands(self) -> List[Tuple[int, str]]:
        """
        Consulta getUpdates y devuelve la lista de comandos de
        selección nuevos: tuplas (update_id, texto_completo_del_mensaje)
        para cada mensaje del chat configurado que empiece por ".".

        Avanza automáticamente el offset interno para no releer los
        mismos mensajes en la siguiente llamada (vía el parámetro
        "offset" de getUpdates, que le dice a Telegram que puede
        descartar updates ya confirmados).
        """
        url = f"{self._base_url}/getUpdates"
        params = {"timeout": 0}
        if self._update_offset is not None:
            params["offset"] = self._update_offset

        try:
            response = httpx.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except httpx.RequestError as exc:
            logger.warning("Error de red leyendo mensajes de Telegram (getUpdates): %s", exc)
            return []
        except httpx.HTTPStatusError as exc:
            logger.warning("Telegram (getUpdates) devolvió error HTTP %s", exc.response.status_code)
            return []
        except ValueError as exc:
            logger.warning("Respuesta de Telegram (getUpdates) con JSON inválido: %s", exc)
            return []

        if not data.get("ok", False):
            logger.warning("Telegram (getUpdates) respondió ok=False: %s", data)
            return []

        results = data.get("result", [])
        commands: List[Tuple[int, str]] = []
        highest_update_id = self._update_offset - 1 if self._update_offset else None

        for update in results:
            update_id = update.get("update_id")
            if update_id is None:
                continue
            if highest_update_id is None or update_id > highest_update_id:
                highest_update_id = update_id

            message = update.get("message") or {}
            chat = message.get("chat") or {}
            text = message.get("text", "")

            # Solo procesamos mensajes del chat configurado, para evitar
            # que comandos de otro chat (si el bot estuviera en varios
            # grupos) interfieran con el seguimiento de este bot.
            if str(chat.get("id")) != str(self.chat_id):
                continue

            if text.startswith("."):
                commands.append((update_id, text))

        if highest_update_id is not None:
            self._update_offset = highest_update_id + 1

        return commands

    # ------------------------------------------------------------------
    # FUTURO: integración con Betfair Exchange
    # ------------------------------------------------------------------
    # Cuando se quiera automatizar la orden de compra (placeBets) en
    # lugar de (o además de) notificar por Telegram, este sería el
    # punto natural para añadir un método del tipo:
    #
    #   def trigger_betfair_order(self, alert: AlertEvent) -> None:
    #       """
    #       Llamaría a un BetfairExecutionService (módulo nuevo,
    #       betfair_client.py) que:
    #         1. Resuelve el market_id de Betfair Exchange correspondiente
    #            al alert.game_id (requiere mapping game_id -> market_id,
    #            normalmente vía el nombre de los jugadores + fecha).
    #         2. Construye la orden BACK sobre el runner del favorito
    #            (alert.favorite_name) al precio live actual o mejor.
    #         3. Envía la orden vía el endpoint placeOrders de la
    #            Betting API de Betfair, usando BETFAIR_APP_KEY y
    #            BETFAIR_SESSION_TOKEN (ver config.py).
    #         4. Registra la respuesta de la orden (bet_id, status) en
    #            el logger.py para trazabilidad de paper trading -> trading real.
    #       """
    #       raise NotImplementedError("Pendiente de integración con Betfair Exchange API")
