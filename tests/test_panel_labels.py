"""The thinking panel's labels: the game's own card names, same-named options told apart, plain English.

Seen in an English game (seed 5900102, floor 11): the card reward showed "Choose feed" and "Choose
clothesline" (CommunicationMod's choice_list has the card names in lower case; screen_state.cards has the game's
names); two Louses gave two rows "Play Strike -> Louse"; and on the card reward three rows "Skip". Those three are
different options of the simulator: leave the cards and take the gold, the relic or the potion (reward_gold,
reward_relic, reward_potion; the game's command for each is skip, the reward screen comes next), besides plain skip.
Only the panel's text changes; the commands sent to the game are the same.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from spire_bot import messages as M  # noqa: E402
from spire_bot import panel as P  # noqa: E402

# the game's state on the card reward, floor 11 (English), cut down to what the labels read
CARD_REWARD = {"screen_type": "CARD_REWARD", "choice_list": ["clothesline", "feed", "rampage"],
               "screen_state": {"cards": [{"name": "Clothesline", "id": "Clothesline"}, {"name": "Feed", "id": "Feed"},
                                          {"name": "Rampage", "id": "Rampage"}]}}
# (the Chinese names here are made up for the test; only their being Chinese matters)
CARD_REWARD_ZH = {"screen_type": "CARD_REWARD", "choice_list": ["晾衣绳", "狂宴+", "暴走"],
                  "screen_state": {"cards": [{"name": "晾衣绳", "id": "Clothesline"}, {"name": "狂宴+", "id": "Feed"},
                                             {"name": "暴走", "id": "Rampage"}]}}
TWO_LOUSES = {"screen_type": "NONE", "combat_state": {
    "hand": [{"name": "Strike"}, {"name": "Defend"}, {"name": "Strike"}],
    "monsters": [{"name": "Louse"}, {"name": "Louse"}]}}
TWO_LOUSES_ZH = {"screen_type": "NONE", "combat_state": {
    "hand": [{"name": "打击"}, {"name": "防御"}, {"name": "打击"}],
    "monsters": [{"name": "虱虫"}, {"name": "虱虫"}]}}


def test_card_reward_uses_the_games_card_names():
    # (the label carries the card's id for the mod, test_panel_ids.py; P.plain shows the names as sent)
    assert P.plain(P.option_label("choose 1", CARD_REWARD, "en")) == "Choose Feed"          # (before: "Choose feed")
    assert P.plain(P.option_label("choose 0", CARD_REWARD, "en")) == "Choose Clothesline"
    assert P.plain(P.option_label("choose 1", CARD_REWARD_ZH, "zh")) == "选择 狂宴+"


def test_same_named_monsters_are_numbered_left_to_right():
    assert P.option_label("play 1 0", TWO_LOUSES, "en") == "Play Strike -> Louse 1"  # (before: "... -> Louse")
    assert P.option_label("play 3 1", TWO_LOUSES, "en") == "Play Strike -> Louse 2"
    assert P.option_label("play 1 1", TWO_LOUSES_ZH, "zh") == "打出 打击 -> 虱虫 2"
    one = {"screen_type": "NONE", "combat_state": {"hand": [{"name": "Strike"}], "monsters": [{"name": "Lagavulin"}]}}
    assert P.option_label("play 1 0", one, "en") == "Play Strike -> Lagavulin"     # no number when the name is alone


def test_skip_on_the_card_reward_says_what_comes_after():
    gold = {"type": "reward_gold", "gold": 34}
    relic = {"type": "reward_relic", "relic": "Juzu Bracelet"}
    potion = {"type": "reward_potion", "potion": "SmokeBomb"}
    plain = {"type": "reward_skip"}
    assert P.option_label("skip", CARD_REWARD, "en", gold) == "Skip, take the gold"
    assert P.option_label("skip", CARD_REWARD, "en", relic) == "Skip, take the relic"
    assert P.option_label("skip", CARD_REWARD, "en", potion) == "Skip, take the potion"
    assert P.option_label("skip", CARD_REWARD, "en", plain) == "Skip"
    assert P.option_label("skip", CARD_REWARD_ZH, "zh", gold) == "跳过，拿金币"
    assert P.option_label("skip", CARD_REWARD_ZH, "zh", plain) == "跳过"


def test_labels_still_alike_get_a_number():
    assert P.distinct(["Play Strike -> Louse 1", "Skip", "Skip", "End turn"]) == \
        ["Play Strike -> Louse 1", "Skip (1)", "Skip (2)", "End turn"]
    assert P.distinct(["跳过", "跳过"]) == ["跳过 (1)", "跳过 (2)"]
    assert P.distinct(["A", "B"]) == ["A", "B"]


def test_plainer_english():
    assert M.text("FEELING", "en") == "AI's own estimate"       # (before: "AI's own feeling")
    assert M.text("TORN", "en") == "Unsure"                      # (before: "Torn")
    assert M.text("FEELING", "zh") == "AI 自我感觉" and M.text("TORN", "zh") == "在纠结"   # Chinese as it was
