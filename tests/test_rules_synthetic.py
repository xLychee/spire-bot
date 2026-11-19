"""Rules of the bridge checked on hand-built states (no game record needed).

Each of these rules was added after a real game showed the case; the states here are built by hand with the fields
the rule reads. The whole-game replays of those real games are not part of this repository (a record carries the
player's profile); tests/test_fake_record.py replays a record made with the fake game instead.

Needs the compiled simulator on the path: SPIRE_SIM_SITE=<folder that holds spire_sim>; skipped without it.
  SPIRE_SIM_SITE=... python -m pytest tests/test_rules_synthetic.py
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def test_limit_helper():
    sys.path.insert(0, str(HERE.parent / "bridge"))
    import pytest
    if not os.environ.get("SPIRE_SIM_SITE"):
        pytest.skip("SPIRE_SIM_SITE not set")
    sys.path.insert(0, os.environ["SPIRE_SIM_SITE"])
    from spire_bot.bridge import hand_pick_limit_reached as reached
    assert reached({"max_cards": 3, "selected": [1, 2, 3]}) and not reached({"max_cards": 3, "selected": [1, 2]})
    assert not reached({"max_cards": 99, "selected": [1, 2, 3]}) and not reached({"selected": [1]}) and not reached({"max_cards": 0, "selected": []})


def test_escape_hp_ok():
    sys.path.insert(0, str(HERE.parent / "bridge"))
    if not os.environ.get("SPIRE_SIM_SITE"):
        import pytest
        pytest.skip("SPIRE_SIM_SITE not set")
    sys.path.insert(0, os.environ["SPIRE_SIM_SITE"])
    from spire_bot.bridge import escape_hp_ok as ok
    assert ok(30, 41, True, 35) and ok(30, 41, True, 30) and ok(30, 41, True, 41)
    assert not ok(30, 41, True, 42) and not ok(30, 41, True, 29)
    assert ok(30, 36, False, 30) and not ok(30, 36, False, 35)   # no Toy Ornithopter: the HP before the bomb only
    assert ok(30, 30, True, 30) and not ok(30, 30, True, 35)     # Mark of the Bloom: the escape gives nothing


def _choice(relics, discard=0, exhaust=0, energy=3, counter=0, block=0):
    card = lambda i: {"id": i, "name": i, "upgrades": 0, "cost": 1}
    return {"floor": 5, "current_hp": 60, "max_hp": 75, "gold": 100, "deck": [], "potions": [],
            "relics": [{"id": r, "name": r, "counter": counter if r == "Happy Flower" else -1} for r in relics],
            "screen_type": "CARD_REWARD", "room_phase": "COMBAT",
            "screen_state": {"cards": [card("Strike_R"), card("Defend_R"), card("Bash")], "skip_available": True},
            "combat_state": {"hand": [], "draw_pile": [card("Strike_R")] * 10, "discard_pile": [card("Bash")] * discard,
                             "exhaust_pile": [card("Bash")] * exhaust, "player": {"energy": energy, "block": block, "powers": []},
                             "turn": 1, "monsters": [{"id": "Cultist", "current_hp": 40, "block": 0, "is_gone": False, "powers": []}]}}


def test_the_rule_fires_only_at_a_fights_start():
    import pytest
    if not os.environ.get("SPIRE_SIM_SITE"):
        pytest.skip("SPIRE_SIM_SITE not set")
    sys.path[:0] = [str(HERE.parent / "bridge"), os.environ["SPIRE_SIM_SITE"]]
    from spire_bot.bridge import cm_signature
    # Toolbox's choice at the fight's start, Happy Flower just fired: the energy as the game has it (the +1 is gone:
    # the simulator queues the flower's energy behind the choice as the game does)
    assert cm_signature(_choice(["Toolbox", "Happy Flower"]))["energy"] == 3
    # the Codex at the end of turn 1, hand played out: the energy as the game has it
    assert cm_signature(_choice(["Nilry's Codex", "Happy Flower"], discard=3, exhaust=7, energy=1))["energy"] == 1
    # the same with Toolbox owned as well: cards have left the hand, so it is not the fight's start
    assert cm_signature(_choice(["Toolbox", "Nilry's Codex", "Happy Flower"], discard=3, energy=1))["energy"] == 1
    # no Toolbox at all, piles empty (a turn with nothing played, then the Codex): not Toolbox's choice
    assert cm_signature(_choice(["Nilry's Codex", "Happy Flower"], energy=3))["energy"] == 3
    # Happy Flower not at 0: never the rule
    assert cm_signature(_choice(["Toolbox", "Happy Flower"], counter=1))["energy"] == 3


def test_the_block_is_left_out_only_at_a_fights_start():
    import pytest
    if not os.environ.get("SPIRE_SIM_SITE"):
        pytest.skip("SPIRE_SIM_SITE not set")
    sys.path[:0] = [str(HERE.parent / "bridge"), os.environ["SPIRE_SIM_SITE"]]
    from spire_bot.bridge import cm_signature
    # Toolbox's choice at the fight's start: the block is not compared (as before)
    assert "block" not in cm_signature(_choice(["Toolbox", "Anchor"]))
    # turn 1, the hand played out (4 cards exhausted, none discarded), then a potion's three-card choice: an
    # ordinary state of the fight, the block is compared (real game 5600299, floor 41)
    sig = cm_signature(_choice(["Toy Ornithopter"], exhaust=4, energy=0, block=12))
    assert sig.get("block") == 12, "the block is not compared on a potion's choice after the hand was played out"
    # the same with Toolbox owned: cards have left the hand
    assert cm_signature(_choice(["Toolbox"], exhaust=4, block=12)).get("block") == 12
    # a hand emptied by playing only powers (both piles empty), then the Codex, no Toolbox: compared
    assert cm_signature(_choice(["Nilry's Codex"], block=5)).get("block") == 5


def _state(selected, hand, max_cards, hp=68, max_hp=75):
    card = lambda i: {"id": i, "name": i, "upgrades": 0, "cost": 1}
    return {"floor": 5, "current_hp": hp, "max_hp": max_hp, "gold": 100, "deck": [], "relics": [], "potions": [],
            "screen_type": "HAND_SELECT", "room_phase": "COMBAT",
            "screen_state": {"max_cards": max_cards, "can_pick_zero": True, "selected": [card(i) for i in selected],
                             "hand": [card(i) for i in hand]},
            "combat_state": {"hand": [card(i) for i in hand], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
                             "player": {"energy": 2, "block": 0, "powers": []}, "turn": 1,
                             "monsters": [{"id": "Cultist", "current_hp": 40, "block": 0, "is_gone": False, "powers": []}]}}


def test_signature_leaves_the_hp_out_only_while_the_confirm_is_pending():
    import pytest
    if not os.environ.get("SPIRE_SIM_SITE"):
        pytest.skip("SPIRE_SIM_SITE not set")
    sys.path[:0] = [str(HERE.parent / "bridge"), os.environ["SPIRE_SIM_SITE"]]
    from spire_bot.bridge import cm_signature
    # every card picked (Gambler's Brew, Elixir: any number) and a limit reached (Purity: 3): confirm pending
    for st in (_state(["Strike_R", "Defend_R"], [], 99), _state(["Strike_R", "Defend_R", "Bash"], ["Defend_R"], 3)):
        sig = cm_signature(st)
        assert sig.get("select_closed") is True and "hp" not in sig and "max_hp" not in sig, sig
    # picks still possible: the screen is open on both sides and the HP is compared
    sig = cm_signature(_state(["Strike_R"], ["Defend_R", "Bash"], 99))
    assert sig.get("select_open") is True and sig["hp"] == 68 and sig["max_hp"] == 75


def _grid(relics):
    card = lambda i: {"id": i, "name": i, "upgrades": 0, "cost": 1}
    return {"floor": 17, "current_hp": 60, "max_hp": 80, "gold": 100, "deck": [card("Strike_R")], "potions": [],
            "relics": [{"id": "Burning Blood", "counter": -1}] + relics + [{"id": "Calling Bell", "counter": -1}],
            "screen_type": "GRID", "room_phase": "COMPLETE", "room_type": "TreasureRoomBoss",
            "screen_state": {"confirm_up": True, "cards": [card("CurseOfTheBell")], "selected_cards": [], "num_cards": 1}}


def test_signature_expects_the_curse_unless_omamori_has_a_charge():
    import importlib
    sys.path.insert(0, str(HERE.parent / "bridge"))
    os.environ.setdefault("CM_MODEL", "none")
    B = importlib.import_module("spire_bot.bridge")
    deck = lambda relics: sorted(B.cm_signature(_grid(relics))["deck"])
    assert deck([]) == ["curseofthebell", "strike"]
    assert deck([{"id": "Omamori", "counter": 0}]) == ["curseofthebell", "strike"]  # used up
    assert deck([{"id": "Omamori", "counter": 2}]) == ["strike"]
    assert deck([{"id": "Omamori", "counter": 1}]) == ["strike"]
    # Ceramic Fish pays for a card that joins the deck only
    fish = [{"id": "CeramicFish", "counter": -1}]
    assert B.cm_signature(_grid(fish))["gold"] == 109
    assert B.cm_signature(_grid(fish + [{"id": "Omamori", "counter": 1}]))["gold"] == 100
