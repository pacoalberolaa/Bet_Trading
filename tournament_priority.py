"""
tournament_priority.py
-----------------------
Utilidad pequeña y aislada para:
    1. Normalizar el texto de categoría de torneo que llega de la API
       (distintos proveedores usan distinta nomenclatura) hacia las
       claves canónicas definidas en config.TOURNAMENT_CATEGORY_PRIORITY.
    2. Calcular un "rango de prioridad" numérico (0 = máxima prioridad)
       para poder ordenar una lista de partidos de grandes a pequeños.

Se mantiene como módulo independiente (en lugar de meterlo dentro de
data_provider.py o trading_engine.py) porque tanto la capa de ingesta
como el bucle principal (main.py) necesitan esta misma lógica de
ordenación, y así evitamos duplicarla.
"""

from __future__ import annotations

from typing import List, Optional, TypeVar

from config import (
    RANK_ID_TO_CATEGORY,
    TOURNAMENT_CATEGORY_ALIASES,
    TOURNAMENT_CATEGORY_PRIORITY,
    TOURNAMENT_CATEGORY_UNKNOWN,
)
from models import MatchState

T = TypeVar("T")


def normalize_category_from_rank_id(rank_id: Optional[int]) -> str:
    """
    Fuente PRIMARIA de categoría: traduce el "rankId" oficial devuelto
    por /tennis/v2/{type}/tournament/info/{tournamentId} (confirmado
    contra /tennis/v2/ms-api/calendar/atp/filters) a la categoría
    canónica. Es fiable porque viene directamente del proveedor de
    datos, no se infiere de texto libre.

    Devuelve TOURNAMENT_CATEGORY_UNKNOWN si rank_id es None o no está
    en el mapeo (p.ej. Davis Cup, Juniors, Olympics, que no son
    relevantes para la priorización de singles/dobles regulares).
    """
    if rank_id is None:
        return TOURNAMENT_CATEGORY_UNKNOWN
    return RANK_ID_TO_CATEGORY.get(rank_id, TOURNAMENT_CATEGORY_UNKNOWN)


def normalize_tournament_category(raw_category: str) -> str:
    """
    Fuente SECUNDARIA de categoría (heurística por texto): se usa
    cuando no se dispone del rankId oficial del torneo (p.ej. al leer
    directamente el nombre de "league" en events/live, que no trae
    tournamentId). Convierte el texto de categoría/nombre tal como lo
    informa la API a una de las claves canónicas de
    config.TOURNAMENT_CATEGORY_PRIORITY.

    Si no se reconoce ninguna variante, devuelve
    config.TOURNAMENT_CATEGORY_UNKNOWN en lugar de fallar, para que un
    torneo con categoría rara nunca tumbe el pipeline: simplemente
    queda al final de la cola de prioridad.
    """
    if not raw_category:
        return TOURNAMENT_CATEGORY_UNKNOWN

    cleaned = raw_category.strip().lower()

    # Coincidencia directa con alguna clave canónica (case-insensitive)
    for canonical in TOURNAMENT_CATEGORY_PRIORITY:
        if cleaned == canonical.lower():
            return canonical

    # Coincidencia vía diccionario de alias
    if cleaned in TOURNAMENT_CATEGORY_ALIASES:
        return TOURNAMENT_CATEGORY_ALIASES[cleaned]

    return TOURNAMENT_CATEGORY_UNKNOWN


def priority_rank(tournament_category: str) -> int:
    """
    Devuelve un entero: 0 = máxima prioridad (Grand Slam), valores
    crecientes = menor prioridad. Las categorías no reconocidas
    reciben el rango más bajo posible (se procesan último).
    """
    try:
        return TOURNAMENT_CATEGORY_PRIORITY.index(tournament_category)
    except ValueError:
        return len(TOURNAMENT_CATEGORY_PRIORITY)  # peor que cualquier categoría conocida


def sort_matches_by_tournament_priority(matches: List[MatchState]) -> List[MatchState]:
    """
    Ordena una lista de MatchState de torneos grandes a pequeños según
    config.TOURNAMENT_CATEGORY_PRIORITY. El orden es estable: partidos
    de la misma categoría conservan el orden relativo en que llegaron
    de la API (por ejemplo, el orden de inicio de partido).
    """
    return sorted(matches, key=lambda m: priority_rank(m.tournament_category))
