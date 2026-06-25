"""
trading_window.py
-------------------
Utilidad aislada para decidir si el momento actual cae dentro de la
"ventana de trading" configurada (días de la semana + rango horario,
en una zona horaria concreta). Se usa en el modo "solo alerta"
(ver config.ALERT_ONLY_MODE_*) para no consultar la API fuera de las
horas en que el usuario puede operar manualmente.

Usa zoneinfo (librería estándar desde Python 3.9) en vez de cálculos
manuales de UTC+1/+2, para que el cambio de horario de verano/invierno
de Europe/Madrid (o cualquier otra zona) se gestione automáticamente
sin mantenimiento.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from typing import List


class TradingWindow:
    """
    Representa una ventana de trading: días de la semana activos +
    rango horario, en una zona horaria dada.
    """

    def __init__(
        self,
        timezone_name: str,
        start_time_str: str,
        end_time_str: str,
        active_weekdays: List[int],
    ):
        self.timezone = ZoneInfo(timezone_name)
        self.start_time = self._parse_time(start_time_str)
        self.end_time = self._parse_time(end_time_str)
        self.active_weekdays = set(active_weekdays)

        if self.start_time >= self.end_time:
            raise ValueError(
                f"ALERT_ONLY_MODE_START_TIME ({start_time_str}) debe ser anterior a "
                f"ALERT_ONLY_MODE_END_TIME ({end_time_str}); no se soportan ventanas que cruzan medianoche."
            )

    @staticmethod
    def _parse_time(time_str: str) -> time:
        try:
            hours_str, minutes_str = time_str.split(":")
            return time(hour=int(hours_str), minute=int(minutes_str))
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"Formato de hora inválido: {time_str!r}. Se espera 'HH:MM'.") from exc

    def is_open_now(self) -> bool:
        """True si el instante actual cae dentro de la ventana configurada."""
        now_local = datetime.now(self.timezone)
        if now_local.weekday() not in self.active_weekdays:
            return False
        current_time = now_local.time()
        return self.start_time <= current_time < self.end_time

    def seconds_until_next_open(self) -> int:
        """
        Calcula cuántos segundos faltan hasta el próximo instante en
        que la ventana esté abierta, partiendo de "ahora". Útil para
        que el bot pueda dormir más tiempo de una vez en lugar de
        comprobar el reloj cada pocos segundos mientras está cerrado
        (aunque por simplicidad el bucle principal igualmente revisa
        cada ALERT_ONLY_MODE_CLOCK_CHECK_INTERVAL_SECONDS; este método
        se deja disponible para quien quiera optimizarlo más adelante).
        """
        now_local = datetime.now(self.timezone)

        for days_ahead in range(0, 8):
            candidate_weekday = (now_local.weekday() + days_ahead) % 7
            if candidate_weekday not in self.active_weekdays:
                continue

            candidate_datetime = datetime.combine(
                now_local.date() + timedelta(days=days_ahead),
                self.start_time,
                tzinfo=self.timezone,
            )
            if candidate_datetime > now_local:
                return int((candidate_datetime - now_local).total_seconds())

        # No debería llegar aquí si active_weekdays no está vacío.
        return 0
