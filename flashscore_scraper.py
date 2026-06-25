"""
flashscore_scraper.py
----------------------
⚠️ MÓDULO OPCIONAL Y NO RECOMENDADO PARA PRODUCCIÓN ⚠️

Flashscore prohíbe explícitamente el scraping automatizado de su sitio
en sus Términos de Servicio, y utiliza protecciones anti-bot activas
(fingerprinting de navegador, límites de tasa, bloqueo de IP) que
cambian con frecuencia. Además, no expone una API pública estable: su
HTML/JS interno puede cambiar en cualquier momento y romper este
scraper sin previo aviso.

Este módulo se incluye únicamente como referencia de "plan C" para
quien quiera experimentar bajo su propio riesgo si se agotan los
créditos gratuitos de RapidAPI (config.FLASHSCORE_SCRAPING_ENABLED
debe activarse explícitamente; por defecto está desactivado). No se
garantiza que funcione de forma estable ni legal en tu jurisdicción.
Antes de usarlo en serio:
    1. Lee los Términos de Servicio actuales de Flashscore.
    2. Considera el riesgo de bloqueo de IP / cuenta.
    3. Evalúa alternativas legítimas (RapidAPI, APIs oficiales de
       casas de apuestas con las que tengas acuerdo, etc).

La implementación real de scraping (Playwright/Selenium + parseo de
HTML) se deja como placeholder: requiere un navegador headless
instalado en el entorno de ejecución, que no forma parte de las
dependencias por defecto de este proyecto (ver requirements.txt).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from data_provider import BaseDataProvider, DataProviderError, _safe_parse_match
from models import MatchState
from tournament_priority import sort_matches_by_tournament_priority

logger = logging.getLogger(__name__)

_WARNING_MESSAGE = (
    "FlashscoreScraperProvider está activo. Este proveedor incumple los "
    "Términos de Servicio de Flashscore y puede dejar de funcionar o "
    "provocar el bloqueo de tu IP en cualquier momento. Úsalo solo como "
    "último recurso y bajo tu propio riesgo."
)


class FlashscoreScraperProvider(BaseDataProvider):
    """
    Placeholder de proveedor basado en scraping de Flashscore.

    No implementa scraping real "out of the box" a propósito: forzar
    a quien lo active a escribir explícitamente la lógica de
    extracción es una salvaguarda adicional para que nadie lo use
    "por accidente" en producción sin entender los riesgos.
    """

    def __init__(self, target_urls: List[str] | None = None):
        logger.warning(_WARNING_MESSAGE)
        self.target_urls = target_urls or []

    def get_live_matches(self) -> List[MatchState]:
        raise NotImplementedError(
            "El scraping real de Flashscore no está implementado a propósito. "
            "Si decides continuar bajo tu propio riesgo, esta es la guía orientativa:\n"
            "  1. Usa un navegador headless (ej. Playwright) para cargar las páginas "
            "de partidos en vivo de Flashscore, ya que el contenido se genera vía JS.\n"
            "  2. Extrae del DOM: jugadores, marcador por sets/juegos, superficie, "
            "torneo y categoría (la cuota pre-partido y en vivo Flashscore NO las "
            "publica directamente; necesitarías combinarlo con otra fuente de cuotas).\n"
            "  3. Normaliza cada partido extraído a un diccionario con las mismas "
            "claves que usa RapidAPITennisProvider y pásalo por _safe_parse_match().\n"
            "  4. Respeta un intervalo de polling conservador (varios segundos) y "
            "evita el paralelismo agresivo para minimizar el riesgo de bloqueo.\n"
            "  5. Revisa periódicamente que la estructura del HTML no haya cambiado."
        )

    @staticmethod
    def _normalize_scraped_match(raw_match: Dict[str, Any]) -> Dict[str, Any]:
        """
        Punto de ajuste equivalente a RapidAPITennisProvider._normalize_raw_match():
        aquí se traduciría la estructura extraída del DOM de Flashscore
        al formato plano interno. Se deja sin implementar a propósito.
        """
        raise NotImplementedError("Pendiente: mapear estructura real extraída de Flashscore.")
