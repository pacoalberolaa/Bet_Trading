"""
logger.py
---------
Capa de persistencia. Guarda cada AlertEvent disparado en MongoDB
para poder auditar la estrategia más adelante (paper trading ->
trading real).

Se usa pymongo (driver oficial síncrono de MongoDB). La conexión se
abre una sola vez en el constructor y se reutiliza durante toda la
vida del bot; cualquier fallo de conexión/escritura se captura y se
loguea, sin propagar nunca la excepción hacia el bucle principal de
polling (perder un registro de auditoría no debe tumbar el bot).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

from models import AlertEvent

logger = logging.getLogger(__name__)


class AlertLogger:
    """Persiste AlertEvents en una colección de MongoDB."""

    def __init__(
        self,
        mongo_uri: str,
        db_name: str,
        collection_name: str,
        server_selection_timeout_ms: int = 5000,
    ):
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.collection_name = collection_name

        self._client: Optional[MongoClient] = None
        self._collection: Optional[Collection] = None

        self._connect(server_selection_timeout_ms)

    # ------------------------------------------------------------------
    # Conexión
    # ------------------------------------------------------------------

    def _connect(self, server_selection_timeout_ms: int) -> None:
        """
        Abre la conexión a MongoDB y crea los índices necesarios.
        Si Mongo no está disponible en este momento, se loguea el
        error pero NO se lanza excepción: el bot puede seguir vivo
        (las alertas a Telegram seguirán funcionando) y log_alert()
        simplemente fallará de forma controlada hasta que Mongo vuelva.
        """
        try:
            self._client = MongoClient(
                self.mongo_uri,
                serverSelectionTimeoutMS=server_selection_timeout_ms,
            )
            # Fuerza una comprobación de conectividad inmediata.
            self._client.admin.command("ping")

            self._collection = self._client[self.db_name][self.collection_name]

            # Índices útiles para las consultas de auditoría más habituales:
            # por partido y por fecha de la alerta.
            self._collection.create_index("game_id")
            self._collection.create_index("timestamp_utc")
            self._collection.create_index("tournament_category")

            logger.info(
                "Conectado a MongoDB (db=%s, collection=%s).",
                self.db_name,
                self.collection_name,
            )

        except PyMongoError as exc:
            logger.error(
                "No se pudo conectar a MongoDB en %s: %s. "
                "Las alertas seguirán enviándose a Telegram, pero no se "
                "persistirán hasta que la conexión se restablezca.",
                self.mongo_uri,
                exc,
            )
            self._client = None
            self._collection = None

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def log_alert(self, alert: AlertEvent) -> bool:
        """
        Persiste un AlertEvent como documento en MongoDB. Devuelve
        True si se guardó correctamente, False si hubo cualquier
        problema (sin propagar la excepción).
        """
        if self._collection is None:
            # Intento de reconexión perezoso: si Mongo estaba caído al
            # arrancar el bot pero ya se recuperó, esto permite que el
            # logging vuelva a funcionar sin reiniciar el proceso.
            self._connect(server_selection_timeout_ms=3000)
            if self._collection is None:
                logger.warning(
                    "MongoDB sigue sin estar disponible; se descarta la "
                    "persistencia de la alerta del partido %s.",
                    alert.game_id,
                )
                return False

        document = {
            "timestamp_utc": datetime.now(timezone.utc),
            "game_id": alert.game_id,
            "circuit": alert.circuit,
            "surface": alert.surface,
            "tournament_name": alert.tournament_name,
            "tournament_category": alert.tournament_category,
            "favorite_name": alert.favorite_name,
            "underdog_name": alert.underdog_name,
            "odds_prematch_favorite": alert.odds_prematch_favorite,
            "odds_live_favorite": alert.odds_live_favorite,
            "current_set": alert.current_set,
            "games_favorite": alert.games_favorite,
            "games_underdog": alert.games_underdog,
            "score_points": alert.score_points,
            "reason": alert.reason,
        }

        try:
            self._collection.insert_one(document)
            return True
        except PyMongoError as exc:
            logger.error(
                "Fallo al persistir en MongoDB la alerta del partido %s: %s",
                alert.game_id,
                exc,
            )
            return False

    def close(self) -> None:
        """Cierra la conexión limpiamente (llamar al apagar el bot)."""
        if self._client is not None:
            self._client.close()
            logger.info("Conexión a MongoDB cerrada.")
