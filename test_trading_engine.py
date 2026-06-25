"""
Test funcional manual (no pytest) para validar el trading_engine
contra los escenarios exactos descritos en el enunciado, sin tocar
red ni Telegram.
"""

from models import MatchState
from trading_engine import TradingEngine


def make_match(**overrides) -> MatchState:
    base = dict(
        game_id="TEST",
        circuit="ATP",
        surface="Tierra Batida",
        is_indoor=False,
        tournament_name="Torneo de Prueba",
        tournament_category="ATP 250",
        player_home="Favorito",
        player_away="Rival",
        odds_prematch_home=1.20,
        odds_prematch_away=4.50,
        current_set=1,
        current_games_home=1,
        current_games_away=3,
        current_score_points="30-30",
        current_server="away",
        live_odds_home=1.60,
        live_odds_away=2.40,
    )
    base.update(overrides)
    return MatchState(**base)


def run_case(label, match, expect_alert):
    engine = TradingEngine()
    alert = engine.evaluate(match)
    status = "ALERTA" if alert else "sin alerta"
    ok = (alert is not None) == expect_alert
    print(f"[{'OK' if ok else 'FALLO'}] {label}: {status}" + (f" | motivo: {alert.reason}" if alert else ""))
    return ok


results = []

# Caso 1: Tierra batida, favorito 1.20, pierde 1-3 -> debe alertar
results.append(run_case(
    "Tierra batida 1-3, favorito 1.20",
    make_match(surface="Tierra Batida", odds_prematch_home=1.20, current_games_home=1, current_games_away=3),
    expect_alert=True,
))

# Caso 2: WTA doble break 1-4 -> debe alertar
results.append(run_case(
    "WTA doble break 1-4",
    make_match(circuit="WTA", surface="Dura", odds_prematch_home=1.25, current_games_home=1, current_games_away=4, current_server="home"),
    expect_alert=True,
))

# Caso 3: WTA doble break 2-5 -> debe alertar
results.append(run_case(
    "WTA doble break 2-5",
    make_match(circuit="WTA", surface="Dura", odds_prematch_home=1.25, current_games_home=2, current_games_away=5, current_server="home"),
    expect_alert=True,
))

# Caso 4: Hierba, favorito restando (server=away, favorito=home) en bache temprano -> debe alertar
results.append(run_case(
    "Hierba, favorito restando, bache temprano 1-2",
    make_match(surface="Hierba", odds_prematch_home=1.25, current_games_home=1, current_games_away=2, current_server="away"),
    expect_alert=True,
))

# Caso 5: Hierba, favorito SIRVIENDO (acaba de perder su propio saque) -> NO debe alertar
results.append(run_case(
    "Hierba, favorito sirviendo (descartar)",
    make_match(surface="Hierba", odds_prematch_home=1.25, current_games_home=1, current_games_away=2, current_server="home"),
    expect_alert=False,
))

# Caso 6: Hierba, bache en juego crítico de final de set (games suman >=7) -> NO debe alertar
results.append(run_case(
    "Hierba, bache tardio en juego critico (descartar)",
    make_match(surface="Hierba", odds_prematch_home=1.25, current_games_home=3, current_games_away=4, current_server="away"),
    expect_alert=False,
))

# Caso 7: Cuota pre-partido fuera de rango (1.50, no es favorito "real") -> NO debe alertar
results.append(run_case(
    "Cuota fuera de rango (1.50)",
    make_match(odds_prematch_home=1.50, current_games_home=1, current_games_away=3),
    expect_alert=False,
))

# Caso 8: Set 2 (fuera de alcance del bache de 1er set) -> NO debe alertar
results.append(run_case(
    "Set 2 (fuera de alcance)",
    make_match(current_set=2, current_games_home=1, current_games_away=3),
    expect_alert=False,
))

# Caso 9: Favorito va POR DELANTE -> NO debe alertar
results.append(run_case(
    "Favorito va ganando (no hay bache)",
    make_match(current_games_home=3, current_games_away=1),
    expect_alert=False,
))

# Caso 10: Anti-duplicados -> misma instancia de engine, segunda evaluación del mismo set NO debe re-alertar
print("\n--- Test anti-duplicados ---")
engine = TradingEngine()
m = make_match(surface="Tierra Batida", odds_prematch_home=1.20, current_games_home=1, current_games_away=3)
first = engine.evaluate(m)
second = engine.evaluate(m)
dedup_ok = (first is not None) and (second is None)
print(f"[{'OK' if dedup_ok else 'FALLO'}] Primera alerta: {'ALERTA' if first else 'sin alerta'}; Segunda (mismo set): {'ALERTA' if second else 'sin alerta'}")
results.append(dedup_ok)

# Caso 11: el motor solo opera sobre set 1 (regla explícita del enunciado).
# Si el partido avanza a set 2, no debe alertar más (independientemente
# del marcador), confirmando que el filtro de "set 1" sigue vigente.
m2 = make_match(surface="Tierra Batida", odds_prematch_home=1.20, current_set=2, current_games_home=1, current_games_away=3)
m2.game_id = m.game_id
fourth = engine.evaluate(m2)
set_filter_ok = fourth is None
print(f"[{'OK' if set_filter_ok else 'FALLO'}] Set 2 tras alerta en set 1 (debe seguir sin alertar, regla = solo set 1): {'ALERTA' if fourth else 'sin alerta'}")
results.append(set_filter_ok)

print(f"\nResumen: {sum(results)}/{len(results)} casos correctos")
