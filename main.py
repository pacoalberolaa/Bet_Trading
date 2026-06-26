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
from trading_window import TradingWindow
from watchlist_manager import WatchlistManager

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
            candidate = self.trading_engine.check_candidate(match)
            if candidate is not None:
                logger.info(
                    "Candidato detectado -> %s vs %s (%s, %s) | cuota favorito: %.2f",
                    candidate.favorite_name,
                    candidate.underdog_name,
                    candidate.tournament_name,
                    candidate.game_id,
                    candidate.odds_prematch_favorite,
                )
                self.notifier.send_match_detected(candidate)

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


class ManualWatchBot:
    """
    Orquestador del "modo de prueba manual" (ver watchlist_manager.py
    para el diseño completo). Pensado para planes de RapidAPI con
    límite diario bajo de requests (p.ej. Free, 50/día): en vez de
    vigilar todos los partidos en vivo de forma continua, presenta los
    candidatos detectados y solo sigue de cerca los que el usuario
    elige explícitamente respondiendo ".{game_id}" en Telegram.

    Mantiene DOS relojes independientes en un único bucle (sin hilos):
        - scan: cada MANUAL_MODE_SCAN_INTERVAL_SECONDS, llama a
          get_live_matches() completo y ofrece nuevos candidatos.
        - watch: cada MANUAL_MODE_WATCH_INTERVAL_SECONDS, vuelve a
          consultar SOLO los partidos ya en seguimiento activo (lee de
          nuevo get_live_matches(), pero filtra al subconjunto
          elegido) y comprueba si hubo un nuevo break.

    La lectura de selecciones del usuario (process_incoming_selections)
    se hace en CADA iteración del bucle base, que corre al ritmo del
    intervalo más corto de los dos, para que la confirmación de
    selección se sienta razonablemente inmediata sin gastar ningún
    request adicional de RapidAPI (getUpdates de Telegram no consume
    cuota de RapidAPI, solo de la propia API de Telegram, que no tiene
    el mismo límite restrictivo).
    """

    def __init__(
        self,
        data_provider: BaseDataProvider,
        trading_engine: TradingEngine,
        notifier: TelegramNotifier,
        alert_logger: AlertLogger,
        watchlist_manager: WatchlistManager,
        scan_interval_seconds: int,
        watch_interval_seconds: int,
        opening_odds_tracker: Optional[object] = None,
    ):
        self.data_provider = data_provider
        self.trading_engine = trading_engine
        self.notifier = notifier
        self.alert_logger = alert_logger
        self.watchlist_manager = watchlist_manager
        self.scan_interval_seconds = scan_interval_seconds
        self.watch_interval_seconds = watch_interval_seconds
        self.opening_odds_tracker = opening_odds_tracker

        # Bucle base: el más corto de los dos intervalos, para poder
        # leer selecciones del usuario con razonable prontitud.
        self._base_tick_seconds = min(scan_interval_seconds, watch_interval_seconds, 15)
        self._seconds_since_last_scan = 0
        self._seconds_since_last_watch = 0

        self._running = False

    def run_forever(self) -> None:
        self._running = True
        logger.info(
            "Bot en MODO DE PRUEBA MANUAL iniciado. Scan cada %ss, seguimiento cada %ss.",
            self.scan_interval_seconds,
            self.watch_interval_seconds,
        )

        # Forzamos un primer scan inmediato al arrancar.
        self._seconds_since_last_scan = self.scan_interval_seconds

        while self._running:
            # Leer selecciones del usuario en cada tick base (gratis en
            # cuanto a requests de RapidAPI, solo gasta llamadas a la
            # API de Telegram).
            try:
                self.watchlist_manager.process_incoming_selections()
            except Exception:
                logger.exception("Error inesperado procesando selecciones de Telegram.")

            if self._seconds_since_last_scan >= self.scan_interval_seconds:
                self._run_scan_cycle()
                self._seconds_since_last_scan = 0

            if self._seconds_since_last_watch >= self.watch_interval_seconds:
                self._run_watch_cycle()
                self._seconds_since_last_watch = 0

            time.sleep(self._base_tick_seconds)
            self._seconds_since_last_scan += self._base_tick_seconds
            self._seconds_since_last_watch += self._base_tick_seconds

        self.alert_logger.close()
        if self.opening_odds_tracker is not None:
            self.opening_odds_tracker.close()
        logger.info("Bot (modo manual) detenido limpiamente.")

    def stop(self) -> None:
        self._running = False

    def _run_scan_cycle(self) -> None:
        """Detecta nuevos candidatos (bache + cuota favorita) y los ofrece en Telegram."""
        try:
            live_matches = self.data_provider.get_live_matches()
        except DataProviderError as exc:
            logger.error("No se pudo obtener el snapshot de partidos en vivo (scan): %s", exc)
            return
        except Exception:
            logger.exception("Error inesperado obteniendo partidos en vivo (scan).")
            return

        logger.info("Scan de candidatos: %d partidos en vivo recibidos.", len(live_matches))
        already_watched = set(self.watchlist_manager.get_watched_game_ids())

        for match in live_matches:
            candidate = self.trading_engine.check_candidate(match)
            if candidate is not None:
                logger.info(
                    "Candidato detectado -> %s vs %s (%s, %s) | cuota favorito: %.2f",
                    candidate.favorite_name,
                    candidate.underdog_name,
                    candidate.tournament_name,
                    candidate.game_id,
                    candidate.odds_prematch_favorite,
                )
                self.notifier.send_match_detected(candidate)

            if match.game_id in already_watched:
                continue  # ya en seguimiento, no hace falta volver a ofrecerlo
            alert = self.trading_engine.evaluate(match)
            if alert is None:
                continue
            self.watchlist_manager.offer_candidate(alert)
            # No se persiste todavía en alert_logger: solo se persiste
            # cuando el usuario confirma seguimiento o se detecta un
            # break real durante el seguimiento (ver _run_watch_cycle),
            # para no llenar la auditoría de candidatos nunca elegidos.

    def _run_watch_cycle(self) -> None:
        """Vuelve a consultar el marcador SOLO de los partidos en seguimiento activo."""
        watched_ids = set(self.watchlist_manager.get_watched_game_ids())
        if not watched_ids:
            return

        try:
            live_matches = self.data_provider.get_live_matches()
        except DataProviderError as exc:
            logger.error("No se pudo obtener el snapshot de partidos en vivo (watch): %s", exc)
            return
        except Exception:
            logger.exception("Error inesperado obteniendo partidos en vivo (watch).")
            return

        live_by_id = {m.game_id: m for m in live_matches}

        for game_id in watched_ids:
            match = live_by_id.get(game_id)
            if match is None:
                # El partido ya no aparece en vivo (probablemente terminó).
                logger.info("Partido en seguimiento ya no está en vivo (game_id=%s); se deja de seguir.", game_id)
                self.watchlist_manager.stop_watching(game_id)
                continue

            alert = self.trading_engine.evaluate(match)
            # evaluate() puede devolver None si ya se alertó antes en
            # este set (anti-duplicados del propio trading_engine);
            # para el seguimiento manual reconstruimos el AlertEvent
            # mínimo necesario a partir del match si hace falta.
            current_alert = alert or self._build_minimal_alert(match)

            new_break = self.watchlist_manager.check_for_new_break(current_alert)
            if new_break is not None:
                self.alert_logger.log_alert(new_break)

    @staticmethod
    def _build_minimal_alert(match) -> "AlertEvent":
        """
        Construye un AlertEvent "de seguimiento" a partir de un
        MatchState, usado cuando trading_engine.evaluate() no genera
        uno nuevo (porque ya alertó antes en este set) pero el modo
        manual necesita igualmente el marcador actual para comparar
        contra el último visto en check_for_new_break().
        """
        from models import AlertEvent

        favorite_side = match.favorite_side or "home"
        underdog_side = match.opponent(favorite_side)
        return AlertEvent(
            game_id=match.game_id,
            circuit=match.circuit,
            surface=match.surface,
            tournament_name=match.tournament_name,
            tournament_category=match.tournament_category,
            favorite_name=match.player_name(favorite_side),
            underdog_name=match.player_name(underdog_side),
            odds_prematch_favorite=match.odds_prematch_for(favorite_side),
            odds_live_favorite=match.live_odds_for(favorite_side),
            current_set=match.current_set,
            games_favorite=match.games_for(favorite_side),
            games_underdog=match.games_for(underdog_side),
            score_points=match.current_score_points,
            reason="Seguimiento manual (sin nueva alerta del motor de reglas)",
        )


class AlertOnlyBot:
    """
    Orquestador del modo "solo alerta" (el más simple de los tres
    disponibles): sin selección ni seguimiento automático. Escanea
    events/live únicamente dentro de la ventana de trading configurada
    (días + horas, ver trading_window.py) y manda UN aviso por
    partido que cumpla el bache+cuota — el usuario decide manualmente
    cuándo entrar y cuándo registrar el resultado.

    Pensado como primera fase de validación de la metodología con el
    plan Free de RapidAPI (50 requests/día): fuera de la ventana de
    trading, el bot duerme sin hacer ninguna llamada a la API.
    """

    def __init__(
        self,
        data_provider: BaseDataProvider,
        trading_engine: TradingEngine,
        notifier: TelegramNotifier,
        alert_logger: AlertLogger,
        trading_window: TradingWindow,
        scan_interval_seconds: int,
        clock_check_interval_seconds: int = 30,
        opening_odds_tracker: Optional[object] = None,
    ):
        self.data_provider = data_provider
        self.trading_engine = trading_engine
        self.notifier = notifier
        self.alert_logger = alert_logger
        self.trading_window = trading_window
        self.scan_interval_seconds = scan_interval_seconds
        self.clock_check_interval_seconds = clock_check_interval_seconds
        self.opening_odds_tracker = opening_odds_tracker

        self._running = False
        self._was_open_last_check: Optional[bool] = None

    def run_forever(self) -> None:
        self._running = True
        logger.info(
            "Bot en MODO SOLO ALERTA iniciado. Ventana: %s, %s-%s (días activos: %s). Escaneo cada %ss dentro de ventana.",
            self.trading_window.timezone,
            self.trading_window.start_time,
            self.trading_window.end_time,
            sorted(self.trading_window.active_weekdays),
            self.scan_interval_seconds,
        )

        while self._running:
            is_open = self.trading_window.is_open_now()

            if is_open != self._was_open_last_check:
                if is_open:
                    logger.info("Ventana de trading ABIERTA. Empezando a escanear partidos en vivo.")
                else:
                    logger.info("Ventana de trading CERRADA. El bot duerme hasta la próxima apertura (sin gastar requests).")
                self._was_open_last_check = is_open

            if is_open:
                self._run_scan_cycle()
                time.sleep(self.scan_interval_seconds)
            else:
                time.sleep(self.clock_check_interval_seconds)

        self.alert_logger.close()
        if self.opening_odds_tracker is not None:
            self.opening_odds_tracker.close()
        logger.info("Bot (modo solo alerta) detenido limpiamente.")

    def stop(self) -> None:
        self._running = False

    def _run_scan_cycle(self) -> None:
        """
        Ejecuta un único ciclo de escaneo: ingesta -> evaluación ->
        notificación -> persistencia. Idéntico en espíritu al ciclo de
        TennisTradingBot, pero solo se llama dentro de la ventana de
        trading y sin seguimiento posterior: una vez avisado, el
        partido sigue evaluándose en ciclos futuros (el propio
        trading_engine ya evita re-alertar el mismo set), pero el bot
        no hace nada más automático con él.
        """
        try:
            live_matches = self.data_provider.get_live_matches()
        except DataProviderError as exc:
            logger.error("No se pudo obtener el snapshot de partidos en vivo: %s", exc)
            return
        except Exception:
            logger.exception("Error inesperado obteniendo partidos en vivo.")
            return

        logger.info("Ciclo de escaneo (modo solo alerta): %d partidos en vivo recibidos.", len(live_matches))

        for match in live_matches:
            candidate = self.trading_engine.check_candidate(match)
            if candidate is not None:
                logger.info(
                    "Candidato detectado -> %s vs %s (%s, %s) | cuota favorito: %.2f",
                    candidate.favorite_name,
                    candidate.underdog_name,
                    candidate.tournament_name,
                    candidate.game_id,
                    candidate.odds_prematch_favorite,
                )
                self.notifier.send_match_detected(candidate)

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
                    "La alerta del partido %s no pudo enviarse a Telegram; se persiste igualmente.",
                    alert.game_id,
                )

            self.alert_logger.log_alert(alert)


def _build_default_bot() -> TennisTradingBot:
    """
    Construye el bot con las implementaciones por defecto (modo
    automático: vigila todos los partidos en vivo continuamente).

    Usa RapidAPITennisProvider (producto "Tennis API - ATP WTA ITF",
    ya confirmado contra respuestas reales) si hay una RAPIDAPI_KEY
    configurada en .env; si no, cae automáticamente a
    MockBetsAPIProvider para que el bot siga siendo ejecutable sin
    credenciales (por ejemplo, recién clonado el repo).

    Para planes de RapidAPI con límite diario bajo de requests, ver en
    su lugar MANUAL_MODE_ENABLED / _build_manual_watch_bot().
    """
    data_provider, trading_engine, notifier, alert_logger, opening_odds_tracker = _build_shared_components()

    # Alternativa NO recomendada (scraping de Flashscore), solo si se
    # agotan los créditos gratuitos y se acepta el riesgo (ver
    # flashscore_scraper.py y config.FLASHSCORE_SCRAPING_ENABLED):
    #
    #   from flashscore_scraper import FlashscoreScraperProvider
    #   data_provider = FlashscoreScraperProvider()

    return TennisTradingBot(
        data_provider=data_provider,
        trading_engine=trading_engine,
        notifier=notifier,
        alert_logger=alert_logger,
        poll_interval_seconds=config.POLL_INTERVAL_SECONDS,
        opening_odds_tracker=opening_odds_tracker,
    )


def _build_shared_components(with_tracker: bool = True):
    """
    Construye los componentes compartidos entre el modo automático
    (TennisTradingBot) y el modo de prueba manual (ManualWatchBot):
    data_provider, trading_engine, notifier, alert_logger y
    opening_odds_tracker. Aislar esto en una función evita duplicar la
    lógica de selección Mock/RapidAPI entre ambos modos.

    with_tracker=False desactiva el OpeningOddsTracker y las llamadas a
    fixtures, útil en ALERT_ONLY mode para no consumir cuota extra en
    planes con límite diario bajo (p.ej. Free, 50 req/día).
    """
    opening_odds_tracker = None

    if config.RAPIDAPI_KEY and config.RAPIDAPI_KEY != "TU_RAPIDAPI_KEY_AQUI":
        from data_provider import RapidAPITennisProvider
        from opening_odds_tracker import OpeningOddsTracker

        if with_tracker:
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

    return data_provider, trading_engine, notifier, alert_logger, opening_odds_tracker


def _build_manual_watch_bot() -> ManualWatchBot:
    """
    Construye el bot en modo de prueba manual CON seguimiento (ver
    watchlist_manager.py y ManualWatchBot). Se activa con
    MANUAL_MODE_ENABLED=true en .env.
    """
    data_provider, trading_engine, notifier, alert_logger, opening_odds_tracker = _build_shared_components()

    watchlist_manager = WatchlistManager(
        notifier=notifier,
        candidate_offer_ttl_seconds=config.MANUAL_MODE_CANDIDATE_TTL_SECONDS,
    )

    return ManualWatchBot(
        data_provider=data_provider,
        trading_engine=trading_engine,
        notifier=notifier,
        alert_logger=alert_logger,
        watchlist_manager=watchlist_manager,
        scan_interval_seconds=config.MANUAL_MODE_SCAN_INTERVAL_SECONDS,
        watch_interval_seconds=config.MANUAL_MODE_WATCH_INTERVAL_SECONDS,
        opening_odds_tracker=opening_odds_tracker,
    )


def _build_alert_only_bot() -> AlertOnlyBot:
    """
    Construye el bot en modo "solo alerta" (el más simple): escanea
    únicamente dentro de la ventana de trading configurada y manda un
    aviso por candidato, sin selección ni seguimiento automático. Se
    activa con ALERT_ONLY_MODE_ENABLED=true en .env.

    No crea OpeningOddsTracker (with_tracker=False) para no gastar
    requests de fixtures en planes con cuota diaria baja.
    """
    data_provider, trading_engine, notifier, alert_logger, opening_odds_tracker = _build_shared_components(with_tracker=False)

    trading_window = TradingWindow(
        timezone_name=config.ALERT_ONLY_MODE_TIMEZONE,
        start_time_str=config.ALERT_ONLY_MODE_START_TIME,
        end_time_str=config.ALERT_ONLY_MODE_END_TIME,
        active_weekdays=config.ALERT_ONLY_MODE_ACTIVE_WEEKDAYS,
    )

    return AlertOnlyBot(
        data_provider=data_provider,
        trading_engine=trading_engine,
        notifier=notifier,
        alert_logger=alert_logger,
        trading_window=trading_window,
        scan_interval_seconds=config.ALERT_ONLY_MODE_SCAN_INTERVAL_SECONDS,
        clock_check_interval_seconds=config.ALERT_ONLY_MODE_CLOCK_CHECK_INTERVAL_SECONDS,
        opening_odds_tracker=opening_odds_tracker,
    )


def main() -> None:
    if config.ALERT_ONLY_MODE_ENABLED:
        bot = _build_alert_only_bot()
    elif config.MANUAL_MODE_ENABLED:
        bot = _build_manual_watch_bot()
    else:
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
