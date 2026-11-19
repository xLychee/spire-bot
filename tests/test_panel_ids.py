"""The panel's names by id: on a Windows set to English, the game in Chinese, CommunicationMod writes the state
in the system's charset (cp1252) and every Chinese name reaches the bridge as question marks (a Windows PC, games
5920001 and 5920002: hand "Strike_R" named "??", relics "????"). The ids are intact. So a label carries, for each
name, a marker with the id (and the game's name as sent, as a fallback); the mod (Panel.java) puts the game's own
name in the game's language there. Options without an id (an event's buttons) carry their place in the game's
choice list, which the mod also has in the right characters.

Marker: "\\x01" + kind|keys...|fallback + "\\x02"; P.plain() shows the fallbacks (what the label said before)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from spire_bot import panel as P  # noqa: E402

A, B = "\x01", "\x02"

# a fight as it reached the bridge on Windows (names garbled, ids intact)
FIGHT = {"screen_type": "NONE", "potions": [{"id": "Fire Potion", "name": "????"}, {"id": "Potion Slot", "name": "???"}],
         "combat_state": {
             "hand": [{"id": "Strike_R", "name": "??", "upgrades": 0}, {"id": "Bash", "name": "??", "upgrades": 1}],
             "monsters": [{"id": "FuzzyLouseNormal", "name": "???"}, {"id": "FuzzyLouseDefensive", "name": "???"},
                          {"id": "FuzzyLouseNormal", "name": "???"}]}}
EVENT = {"screen_type": "EVENT", "choice_list": ["????", "??"],
         "screen_state": {"event_id": "Golden Idol", "options": [{"text": "[??] ??", "choice_index": 0},
                                                                  {"text": "[??]", "choice_index": 1}]}}
CARD_REWARD = {"screen_type": "CARD_REWARD", "choice_list": ["??", "????"],
               "screen_state": {"cards": [{"id": "Anger", "name": "??", "upgrades": 0},
                                          {"id": "Feed", "name": "????", "upgrades": 1}]}}


def test_a_card_played_names_the_card_by_id():
    s = P.option_label("play 2 0", FIGHT, "zh")
    assert f"{A}c|Bash|1|??{B}" in s, repr(s)                    # (before: "打出 ?? -> ???")


def test_monsters_are_named_by_id_and_numbered_among_the_same_id():
    s0 = P.option_label("play 1 0", FIGHT, "zh")
    s1 = P.option_label("play 1 1", FIGHT, "zh")
    s2 = P.option_label("play 1 2", FIGHT, "zh")
    assert f"{A}m|0|FuzzyLouseNormal|1|??? 1{B}" in s0, repr(s0)
    assert f"{A}m|1|FuzzyLouseDefensive|0|???{B}" in s1, repr(s1)  # the only one of its kind: no number
    assert f"{A}m|2|FuzzyLouseNormal|2|??? 2{B}" in s2, repr(s2)


def test_a_potion_by_id():
    s = P.option_label("potion use 0 1", FIGHT, "zh")
    assert f"{A}p|Fire Potion|????{B}" in s and f"{A}m|1|FuzzyLouseDefensive|0|???{B}" in s, repr(s)


def test_an_event_button_by_its_place_in_the_choice_list():
    s = P.option_label("choose 1", EVENT, "zh")
    assert f"{A}x|1|??{B}" in s, repr(s)


def test_a_card_reward_by_id():
    s = P.option_label("choose 1", CARD_REWARD, "zh")
    assert f"{A}c|Feed|1|????{B}" in s, repr(s)


def test_plain_shows_the_names_as_sent():
    assert P.plain(P.option_label("play 2 0", FIGHT, "zh")) == "打出 ?? -> ??? 1"


def test_labels_alike_only_when_the_ids_are():
    # two different cards both garbled to "??" are different options; the same card twice is alike
    a = P.option_label("play 1 0", FIGHT, "zh")
    b = P.option_label("play 2 0", FIGHT, "zh")
    assert a != b
    assert P.distinct([a, b]) == [a, b]
