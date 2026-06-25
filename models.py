"""
models.py
---------
Modelos de datos tipados (dataclasses) usados a través de toda la
aplicación. Centralizar el modelo aquí evita que cada módulo trabaje
con diccionarios "mágicos" y reduce errores por claves mal escritas.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class MatchState:
    """
    Representa el estado completo de un partido de tenis en un instante
    dado. Es el objeto que viaja desde data_provider -> trading_engine
    -> notifier/logger.
    """
    game_id: str
    circuit: str                  # "ATP" | "WTA"
    surface: str                  # "Tierra Batida" | "Hierba" | "Dura"
    is_indoor: bool

    tournament_name: str          # ej. "Roland Garros", "Madrid Open", "Challenger Bratislava"
    tournament_category: str      # ej. "Grand Slam", "Masters 1000", "ATP 250", "Challenger"...

    player_home: str
    player_away: str

    odds_prematch_home: float
    odds_prematch_away: float

    current_set: int
    current_games_home: int
    current_games_away: int
    current_score_points: str     # ej "30-40"
    current_server: str           # "home" | "away"

    live_odds_home: float
    live_odds_away: float

    @property
    def favorite_side(self) -> Optional[str]:
        """
        Determina qué lado (home/away) era el favorito PRE-PARTIDO
        en base a la cuota más baja. No aplica todavía ningún filtro
        de rango; eso es responsabilidad del trading_engine.
        """
        if self.odds_prematch_home is None or self.odds_prematch_away is None:
            return None
        if self.odds_prematch_home < self.odds_prematch_away:
            return "home"
        if self.odds_prematch_away < self.odds_prematch_home:
            return "away"
        return None  # cuotas idénticas -> no hay favorito claro

    def games_for(self, side: str) -> int:
        return self.current_games_home if side == "home" else self.current_games_away

    def games_against(self, side: str) -> int:
        return self.current_games_away if side == "home" else self.current_games_home

    def odds_prematch_for(self, side: str) -> float:
        return self.odds_prematch_home if side == "home" else self.odds_prematch_away

    def live_odds_for(self, side: str) -> float:
        return self.live_odds_home if side == "home" else self.live_odds_away

    def opponent(self, side: str) -> str:
        return "away" if side == "home" else "home"

    def player_name(self, side: str) -> str:
        return self.player_home if side == "home" else self.player_away


@dataclass
class AlertEvent:
    """
    Representa una alerta ya disparada, lista para ser notificada
    y persistida. Es el objeto de salida del trading_engine.
    """
    game_id: str
    circuit: str
    surface: str
    tournament_name: str
    tournament_category: str
    favorite_name: str
    underdog_name: str
    odds_prematch_favorite: float
    odds_live_favorite: float
    current_set: int
    games_favorite: int
    games_underdog: int
    score_points: str
    reason: str  # descripción corta de qué regla disparó (auditoría)
