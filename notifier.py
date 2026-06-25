"""
notifier.py
-----------
Servicio de mensajería. Encapsula el envío de alertas a Telegram
usando peticiones HTTP puras contra la Bot API (sin dependencia de
python-telegram-bot, para mantener el footprint mínimo).

Se usa httpx por su soporte nativo de timeouts y por ser fácilmente
intercambiable por requests si se prefiere.
"""

from __future__ import annotations

import logging

import httpx

from models import AlertEvent

logger = logging.getLogger(__name__)


class TelegramNotifierError(Exception):
    """Error al intentar notificar vía Telegram."""


class TelegramNotifier:
    """Cliente mínimo para enviar mensajes formateados a un chat de Telegram."""

    def __init__(self, bot_token: str, chat_id: str, timeout: float = 10.0):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout
        self._base_url = f"https://api.telegram.org/bot{self.bot_token}"

    def send_alert(self, alert: AlertEvent) -> bool:
        """
        Envía la alerta formateada. Devuelve True si Telegram confirmó
        la entrega, False si hubo un fallo controlado (se loguea pero
        no se interrumpe el bucle principal del bot).
        """
        message = self._format_message(alert)
        return self._send_message(message)

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
