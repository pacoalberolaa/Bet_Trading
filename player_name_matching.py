"""
player_name_matching.py
------------------------
Utilidad aislada para comparar nombres de jugador entre dos fuentes
del mismo proveedor de datos (RapidAPI) que, en la práctica, no
siempre escriben el nombre exactamente igual: distinto uso de tildes,
mayúsculas, orden en dobles, abreviaturas, etc.

Se usa en opening_odds_tracker.py para encontrar, dentro de los
fixtures programados de hoy, cuál corresponde a un partido que acaba
de aparecer en events/live (que no comparte un id común con fixtures,
solo los nombres de los jugadores).

Diseño deliberadamente simple y explicado: en vez de una librería de
fuzzy-matching de terceros (que añadiría una dependencia más para un
problema acotado), se normaliza el texto (minúsculas, sin tildes, sin
puntuación) y se compara por igualdad o por apellido. Esto cubre la
gran mayoría de casos reales sin falsos positivos peligrosos: preferir
"no encontrado" a "emparejado con el partido equivocado", ya que un
cruce erróneo metería una cuota equivocada en el motor de reglas.
"""

from __future__ import annotations

import unicodedata
from typing import Optional


def normalize_name(raw_name: str) -> str:
    """
    Normaliza un nombre de jugador para comparación:
        - minúsculas
        - sin tildes/diacríticos (María -> maria)
        - espacios repetidos colapsados
        - se mantiene el orden de palabras tal cual (no reordena)

    Ejemplos:
        "María José Martínez"  -> "maria jose martinez"
        "C. Alcaraz"           -> "c. alcaraz"
    """
    if not raw_name:
        return ""

    normalized = unicodedata.normalize("NFKD", raw_name)
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(without_accents.lower().split())


def _last_word(normalized_name: str) -> str:
    """Devuelve la última palabra de un nombre ya normalizado (proxy del apellido)."""
    parts = normalized_name.split(" ")
    return parts[-1] if parts else ""


def _is_abbreviated_form(normalized_name: str) -> bool:
    """
    True si el nombre tiene forma "inicial(es) + apellido", p.ej.
    "c. alcaraz" o "c alcaraz" (una sola letra en alguna de las
    palabras salvo la última). Se usa para restringir la regla de
    coincidencia por apellido SOLO a este caso: comparar dos nombres
    completos por apellido sería peligroso (apellidos compartidos por
    jugadores distintos, frecuente en tenis: "Martinez", "Garcia"...).
    """
    words = normalized_name.replace(".", "").split(" ")
    if len(words) < 2:
        return False
    # Todas las palabras salvo la última deben ser de 1 carácter (inicial)
    return all(len(w) == 1 for w in words[:-1])


def _initials(normalized_name: str) -> list:
    """Devuelve la lista de iniciales (todas las palabras salvo la última, sin puntos)."""
    words = normalized_name.replace(".", "").split(" ")
    return words[:-1]


def names_match(name_a: str, name_b: str) -> bool:
    """
    True si dos nombres de jugador individual (no dobles) se
    consideran la misma persona, con tres niveles de tolerancia:
        1. Coincidencia exacta tras normalizar.
        2. Mismo conjunto de palabras en cualquier orden (cubre
           "Hiroki Sakagawa" vs "Sakagawa Hiroki", nombre/apellido
           invertidos entre proveedores).
        3. Coincidencia por apellido (última palabra) cuando AL MENOS
           UNO de los dos nombres está en forma abreviada
           ("C. Alcaraz"), con dos variantes:
             a) Si solo uno de los dos está abreviado (el otro es un
                nombre completo): basta que el apellido coincida y que
                la inicial del lado abreviado coincida con la primera
                letra del nombre de pila del lado completo. Cubre
                "C. Alcaraz" vs "Carlos Alcaraz".
             b) Si AMBOS están abreviados: además del apellido, sus
                iniciales también deben coincidir entre sí. Esto
                evita que "C. Martinez" y "P. Martinez" (dos jugadores
                DISTINTOS que comparten apellido, frecuente en tenis)
                se confundan solo por el apellido compartido.
           Dos nombres completos que comparten apellido (sin ninguna
           forma abreviada) NUNCA matchean solo por eso: "Pedro
           Martinez" vs "Carlos Martinez" son personas distintas.
    No se usa similitud difusa (Levenshtein, etc.) a propósito: un
    emparejamiento "parecido pero incorrecto" es peor que no
    encontrar nada, porque metería una cuota del partido equivocado.
    """
    norm_a = normalize_name(name_a)
    norm_b = normalize_name(name_b)

    if not norm_a or not norm_b:
        return False

    if norm_a == norm_b:
        return True

    words_a = set(norm_a.replace(".", "").split(" "))
    words_b = set(norm_b.replace(".", "").split(" "))
    if words_a and words_a == words_b:
        return True

    abbreviated_a = _is_abbreviated_form(norm_a)
    abbreviated_b = _is_abbreviated_form(norm_b)

    if not (abbreviated_a or abbreviated_b):
        return False

    last_a = _last_word(norm_a)
    last_b = _last_word(norm_b)
    if not last_a or last_a != last_b:
        return False

    if abbreviated_a and abbreviated_b:
        # Ambos abreviados: exigir que las iniciales también coincidan.
        initials_a = _initials(norm_a)
        initials_b = _initials(norm_b)
        return initials_a == initials_b and len(initials_a) > 0

    # Solo uno de los dos abreviado: comprobar que la inicial del lado
    # abreviado coincide con la primera letra del nombre de pila
    # completo del otro lado. abbreviated_initials nunca está vacío
    # aquí (es la condición que exige _is_abbreviated_form), pero
    # full_first_words sí puede estarlo si el "nombre completo" es en
    # realidad una sola palabra (p.ej. el proveedor solo dio el
    # apellido, "Alcaraz" sin nombre de pila) — en ese caso no hay
    # inicial real con la que comparar, así que se rechaza el match
    # en vez de asumir que coincide.
    abbreviated_name = norm_a if abbreviated_a else norm_b
    full_name = norm_b if abbreviated_a else norm_a

    abbreviated_initials = _initials(abbreviated_name)
    full_first_words = full_name.split(" ")[:-1]  # todo salvo el apellido

    if not full_first_words:
        return False

    return abbreviated_initials[0] == full_first_words[0][0]


def doubles_team_matches(team_a: str, team_b: str) -> bool:
    """
    Para partidos de dobles, el nombre completo del "equipo" llega
    como "Jugador1/Jugador2" (separado por barra, visto en fixtures
    reales). Se separa por "/" y se exige que AMBOS jugadores
    encuentren pareja (en cualquier orden) para considerar que los
    equipos coinciden. Si alguno de los dos strings no tiene "/"
    (no es un partido de dobles), se delega en names_match().
    """
    if "/" not in team_a or "/" not in team_b:
        return names_match(team_a, team_b)

    players_a = [p.strip() for p in team_a.split("/") if p.strip()]
    players_b = [p.strip() for p in team_b.split("/") if p.strip()]

    if len(players_a) != 2 or len(players_b) != 2:
        return False

    # Probar las dos orientaciones posibles (el orden puede variar
    # entre fixtures y events/live).
    direct = names_match(players_a[0], players_b[0]) and names_match(players_a[1], players_b[1])
    crossed = names_match(players_a[0], players_b[1]) and names_match(players_a[1], players_b[0])
    return direct or crossed


def find_matching_fixture(
    participant1: str,
    participant2: str,
    fixtures: list,
    name_field_1: str = "player1_name",
    name_field_2: str = "player2_name",
) -> Optional[dict]:
    """
    Busca, dentro de una lista de fixtures (diccionarios), el primero
    cuyos dos jugadores coincidan con (participant1, participant2) de
    events/live, en cualquier orden. Devuelve el fixture completo si
    lo encuentra, o None si no hay coincidencia.

    Se usa doubles_team_matches() para que la misma función sirva
    tanto para individuales como para dobles sin que el llamador
    tenga que distinguir.
    """
    for fixture in fixtures:
        fixture_p1 = fixture.get(name_field_1, "")
        fixture_p2 = fixture.get(name_field_2, "")

        direct = doubles_team_matches(participant1, fixture_p1) and doubles_team_matches(participant2, fixture_p2)
        crossed = doubles_team_matches(participant1, fixture_p2) and doubles_team_matches(participant2, fixture_p1)

        if direct or crossed:
            return fixture

    return None
