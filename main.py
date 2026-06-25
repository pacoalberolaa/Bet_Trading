"""
main.py
-------
Punto de entrada del bot. Orquesta el bucle de polling:

    1. Pide al data_provider el snapshot actual de partidos en vivo.
    2. Pasa cada partido por el trading_engine.
    3. Si hay AlertEvent, lo envía por Telegram y lo persiste en logs.
    4. Duerme POLL_INTERVAL_SECONDS y repite.

Diseño orientado a eventos: cada "tick" de polling genera, como mucho,
un AlertEvent por partido, que se trata como un evento independiente
y se reparte a los dos consumidores (notifier y logger) sin que estos
sepan nada del resto del pipeline.
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from types import FrameType
from typing import Optional

import config
from data_provider import BaseDataProvider, DataProviderError, MockBetsAPIProvider
from logger import AlertLogger
from notifier import TelegramNotifier
from trading_engine import TradingEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("main")


class TennisTradingBot:
    """
    Orquestador principal. Mantiene las referencias a los servicios
    (data_provider, trading_engine, notifier, alert_logger) y ejecuta
    el bucle de control.
    """

    def __init__(
        self,
        data_provider: BaseDataProvider,
        trading_engine: TradingEngine,
        notifier: TelegramNotifier,
        alert_logger: AlertLogger,
        poll_interval_seconds: int = config.POLL_INTERVAL_SECONDS,
        opening_odds_tracker: Optional[object] = None,
    ):
        self.data_provider = data_provider
        self.trading_engine = trading_engine
        self.notifier = notifier
        self.alert_logger = alert_logger
        self.poll_interval_seconds = poll_interval_seconds
        # Referencia opcional solo para poder cerrar su conexión Mongo
        # de forma ordenada al detener el bot (ver run_forever). El
        # propio data_provider ya lo usa internamente si se le inyectó.
        self.opening_odds_tracker = opening_odds_tracker

        self._running = False

    def run_forever(self) -> None:
        """Bucle principal de polling. Corre hasta recibir señal de parada."""
        self._running = True
        logger.info(
            "Bot de trading de tenis iniciado. Intervalo de polling: %ss",
            self.poll_interval_seconds,
        )

        while self._running:
            self._run_single_cycle()
            time.sleep(self.poll_interval_seconds)

        self.alert_logger.close()
        if self.opening_odds_tracker is not None:
            self.opening_odds_tracker.close()
        logger.info("Bot detenido limpiamente.")

    def stop(self) -> None:
        """Permite una parada ordenada (ej. desde un manejador de señal)."""
        self._running = False

    def _run_single_cycle(self) -> None:
        """
        Ejecuta un único ciclo de polling: ingesta -> evaluación ->
        notificación -> persistencia. Cualquier excepción a nivel de
        ciclo se captura para que un fallo puntual (ej. caída de red
        de la API de marcadores) no mate el proceso del bot.
        """
        try:
            live_matches = self.data_provider.get_live_matches()
        except DataProviderError as exc:
            logger.error("No se pudo obtener el snapshot de partidos en vivo: %s", exc)
            return
        except Exception:
            logger.exception("Error inesperado obteniendo partidos en vivo.")
            return

        logger.info("Ciclo de polling: %d partidos en vivo recibidos.", len(live_matches))
        if live_matches:
            logger.debug(
                "Orden de procesamiento (grandes -> pequeños): %s",
                [f"{m.tournament_category}:{m.game_id}" for m in live_matches],
            )

        for match in live_matches:
            alert = self.trading_engine.evaluate(match)
            if alert is None:
                continue

            logger.info(
                "ALERTA generada -> %s vs %s (%s, %s) | %s",
                alert.favorite_name,
                alert.underdog_name,
                alert.tournament_name,
                alert.game_id,
                alert.reason,
            )

            sent_ok = self.notifier.send_alert(alert)
            if not sent_ok:
                logger.warning(
                    "La alerta del partido %s no pudo enviarse a Telegram; "
                    "se persiste igualmente para no perder el registro.",
                    alert.game_id,
                )

            self.alert_logger.log_alert(alert)

            # ------------------------------------------------------------------
            # FUTURO: integración con Betfair Exchange para automatizar la orden.
            # Aquí es donde, tras confirmar la alerta, se llamaría a un servicio
            # de ejecución real, por ejemplo:
            #
            #     betfair_client.place_back_order(
            #         player_name=alert.favorite_name,
            #         price=alert.odds_live_favorite,
            #         stake=calculated_stake,
            #     )
            #
            # Quedaría como un nuevo módulo `betfair_client.py` inyectado en
            # este bot de la misma forma que `notifier` y `alert_logger`,
            # manteniendo la arquitectura modular y desacoplada.
            # ------------------------------------------------------------------


def _build_default_bot() -> TennisTradingBot:
    """
    Construye el bot con las implementaciones por defecto.

    Usa RapidAPITennisProvider (producto "Tennis API - ATP WTA ITF",
    ya confirmado contra respuestas reales) si hay una RAPIDAPI_KEY
    configurada en .env; si no, cae automáticamente a
    MockBetsAPIProvider para que el bot siga siendo ejecutable sin
    credenciales (por ejemplo, recién clonado el repo).

    Cuando se usa RapidAPITennisProvider, también se construye un
    OpeningOddsTracker (ver opening_odds_tracker.py) y se inyecta en
    el provider: resuelve el problema de que el endpoint de cuotas en
    vivo solo da los últimos 10 movimientos, insuficiente como cuota
    pre-partido para partidos que ya llevan tiempo jugándose.
    """
    opening_odds_tracker = None

    if config.RAPIDAPI_KEY and config.RAPIDAPI_KEY != "TU_RAPIDAPI_KEY_AQUI":
        from data_provider import RapidAPITennisProvider
        from opening_odds_tracker import OpeningOddsTracker

        opening_odds_tracker = OpeningOddsTracker(
            mongo_uri=config.MONGO_URI,
            db_name=config.MONGO_DB_NAME,
            fixtures_collection=config.MONGO_FIXTURES_COLLECTION,
            opening_odds_collection=config.MONGO_OPENING_ODDS_COLLECTION,
            tournament_info_collection=config.MONGO_TOURNAMENT_INFO_COLLECTION,
            fixtures_refresh_interval_seconds=config.FIXTURES_REFRESH_INTERVAL_SECONDS,
            orphan_retry_cooldown_seconds=config.ORPHAN_LOOKUP_COOLDOWN_SECONDS,
            server_selection_timeout_ms=config.MONGO_SERVER_SELECTION_TIMEOUT_MS,
        )

        data_provider = RapidAPITennisProvider(
            api_key=config.RAPIDAPI_KEY,
            api_host=config.RAPIDAPI_HOST,
            base_url=config.RAPIDAPI_BASE_URL,
            live_endpoint=config.RAPIDAPI_LIVE_ENDPOINT,
            fetch_odds=config.RAPIDAPI_FETCH_ODDS,
            opening_odds_tracker=opening_odds_tracker,
            fixtures_tour_types=config.FIXTURES_TOUR_TYPES,
        )
        logger.info("Usando RapidAPITennisProvider (host=%s) como fuente de datos.", config.RAPIDAPI_HOST)
    else:
        data_provider = MockBetsAPIProvider()
        logger.warning(
            "RAPIDAPI_KEY no configurada en .env; usando MockBetsAPIProvider "
            "(datos simulados, no reales). Rellena .env para conectar datos reales."
        )

    # Alternativa NO recomendada (scraping de Flashscore), solo si se
    # agotan los créditos gratuitos y se acepta el riesgo (ver
    # flashscore_scraper.py y config.FLASHSCORE_SCRAPING_ENABLED):
    #
    #   from flashscore_scraper import FlashscoreScraperProvider
    #   data_provider = FlashscoreScraperProvider()

    trading_engine = TradingEngine()

    notifier = TelegramNotifier(
        bot_token=config.TELEGRAM_BOT_TOKEN,
        chat_id=config.TELEGRAM_CHAT_ID,
    )

    alert_logger = AlertLogger(
        mongo_uri=config.MONGO_URI,
        db_name=config.MONGO_DB_NAME,
        collection_name=config.MONGO_ALERTS_COLLECTION,
        server_selection_timeout_ms=config.MONGO_SERVER_SELECTION_TIMEOUT_MS,
    )

    return TennisTradingBot(
        data_provider=data_provider,
        trading_engine=trading_engine,
        notifier=notifier,
        alert_logger=alert_logger,
        poll_interval_seconds=config.POLL_INTERVAL_SECONDS,
        opening_odds_tracker=opening_odds_tracker,
    )


def main() -> None:
    bot = _build_default_bot()

    def _handle_shutdown_signal(signum: int, frame: Optional[FrameType]) -> None:
        logger.info("Señal de parada recibida (%s). Cerrando bot...", signum)
        bot.stop()

    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)

    try:
        bot.run_forever()
    except Exception:
        logger.exception("Error fatal no controlado. El bot se detiene.")
        sys.exit(1)


if __name__ == "__main__":
    main()
