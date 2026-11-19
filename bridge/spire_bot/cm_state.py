"""CommunicationMod game state -> input of Env.set_battle_from_cm (real-game positions for the network and the search).

PlayedCounter counts the cards played this turn from consecutive states (the game state does not say): a card that
leaves the hand between two states of the same turn, without the turn changing, counts as played, by its type.
Cards discarded or exhausted by an effect (True Grit, Burning Pact's exhaust) are counted too: an approximation.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

MOVE_TABLE = Path(__file__).resolve().parents[2] / "data/cm_move_table.json"


def load_moves(path=MOVE_TABLE) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}


class PlayedCounter:
    def __init__(self):
        self.turn = None
        self.prev_hand = None
        self.counts = Counter()

    def update(self, gs: dict) -> dict:
        cs = gs.get("combat_state") or {}
        hand = cs.get("hand", [])
        turn = cs.get("turn")
        if turn != self.turn:
            self.turn, self.counts = turn, Counter()
        elif self.prev_hand is not None:
            now = Counter(c.get("uuid") for c in hand)
            for c in self.prev_hand:
                if now[c.get("uuid")] > 0:
                    now[c.get("uuid")] -= 1
                    continue
                self.counts["cards"] += 1
                t = str(c.get("type", "")).upper()
                if t == "ATTACK":
                    self.counts["attacks"] += 1
                elif t == "SKILL":
                    self.counts["skills"] += 1
        self.prev_hand = list(hand)
        return dict(self.counts)


def to_set_json(gs: dict, moves: dict, played: dict) -> str:
    """game_state plus "_moves" ([current, last] simulator move names per monster) and "_played"."""
    cs = gs.get("combat_state") or {}
    mv = []
    for m in cs.get("monsters", []):
        cur = moves.get(f"{m.get('id')}:{m.get('move_id')}")
        last = moves.get(f"{m.get('id')}:{m.get('last_move_id')}")
        mv.append([cur, last])
    # the potion slots as the game has them (a Fairy in a Bottle that saved the player is used up in the game; without
    # the slots a resync would fail on "potions")
    pots = [p.get("id", "") if p.get("id") != "Potion Slot" else "" for p in gs.get("potions", [])]
    # a card lying in the draw pile or the discard pile: the game's one cost is its cost for the rest of the fight
    # (a card that lands in a pile has anything temporary reset), so it goes to the simulator as that ("_base_cost").
    # Without it the simulator kept the printed cost underneath, and a card made free for the fight (Chrysalis's
    # three Skills) cost 1 again the next time it was drawn (real game 5600274, floor 33: six rebuilds). A card in
    # the hand can hold a discount for this turn only, so its cost stays the turn's cost alone
    def piles(c):
        return dict(c, _base_cost=int(c["cost"])) if isinstance(c, dict) and isinstance(c.get("cost"), int) and c["cost"] >= 0 else c
    cs2 = dict(cs, **{k: [piles(c) for c in cs.get(k, [])] for k in ("draw_pile", "discard_pile") if k in cs}) if cs else cs
    return json.dumps(dict(gs, combat_state=cs2, _moves=mv, _played=played, _potions=pots) if cs else
                      dict(gs, _moves=mv, _played=played, _potions=pots))


# The card being played (the game's card_in_play) -> the simulator's choice it opens: (task, extra spec fields).
# A card missing here (or a hand choice from a potion) is not rebuilt; the bridge then keeps its older ways.
SELECT_BY_CARD = {
    "Headbutt": ("HEADBUTT", {}),
    "Exhume": ("EXHUME", {}),
    "Armaments": ("ARMAMENTS", {}),
    "Dual Wield": ("DUAL_WIELD", {"copies": 1}),
    "Burning Pact": ("EXHAUST_ONE", {"draw": 2}),
    "True Grit": ("EXHAUST_ONE", {}),
    "Warcry": ("WARCRY", {}),
    "Thinking Ahead": ("WARCRY", {}),
    "Forethought": ("FORETHOUGHT", {}),
    "Secret Technique": ("SECRET_TECHNIQUE", {}),
    "Secret Weapon": ("SECRET_WEAPON", {}),
    "Discovery": ("DISCOVERY", {"copies": 1}),
    "Purity": ("EXHAUST_MANY", {"limit": 3, "any_number": True}),
}


def open_select_spec(gs: dict, last_gs: dict | None = None):
    """The spec for Env.open_select_from_cm that opens the game's in-fight choice, and None; or None and why not.
    last_gs: the last state before the card was played (the Exhumes the game holds aside are in its exhaust pile)."""
    cs = gs.get("combat_state") or {}
    st = str(gs.get("screen_type", "")).upper()
    ss = gs.get("screen_state") or {}
    if st not in ("HAND_SELECT", "GRID", "CARD_REWARD") or not cs:
        return None, "no choice open in a fight"
    if ss.get("selected") or ss.get("selected_cards"):
        return None, "cards already picked on the screen"
    card = cs.get("card_in_play")
    relics = {r.get("id") for r in gs.get("relics", [])}
    if card is None:
        if st == "CARD_REWARD":
            ids = [c.get("id") for c in ss.get("cards", [])]
            # Toolbox at the fight's start (before the first draw) keeps the card's cost; a potion's card is free and
            # Sacred Bark doubles it
            toolbox = "Toolbox" in relics and cs.get("turn") == 1 and not cs.get("hand") and not cs.get("discard_pile")
            return {"task": "DISCOVERY", "card": None, "cards": ids, "keep_cost": toolbox,
                    "copies": 2 if "SacredBark" in relics and not toolbox else 1}, None
        if st == "GRID":  # Liquid Memories (Sacred Bark: 2)
            return {"task": "LIQUID_MEMORIES_POTION", "card": None, "limit": int(ss.get("num_cards", 1) or 1)}, None
        return None, "a hand choice without a card in play (a potion)"
    cid = card.get("id")
    if cid not in SELECT_BY_CARD:
        return None, f"no rebuild for {cid}"
    task, extra = SELECT_BY_CARD[cid]
    up = int(card.get("upgrades", 0) or 0) > 0
    spec = dict(extra, task=task, card=card)
    if task == "DISCOVERY":
        spec["cards"] = [c.get("id") for c in ss.get("cards", [])]
    if cid == "Dual Wield" and up:
        spec["copies"] = 2
    if cid == "Burning Pact" and up:
        spec["draw"] = 3
    if cid == "Purity" and up:
        spec["limit"] = 5
    if cid == "Forethought" and up:
        spec.update(limit=10, any_number=True)
    if cid == "True Grit" and not up:
        return None, "True Grit exhausts a random card (no choice)"
    if task == "EXHUME" and last_gs:
        lcs = last_gs.get("combat_state") or {}
        aside = [c for c in lcs.get("exhaust_pile", []) if c.get("id") == "Exhume"]
        # The game sets the other Exhumes aside while its grid is open: its current exhaust pile no longer has them.
        # Only those the current pile is missing are held aside; an Exhume still in it (a simulated game that keeps
        # its pile, as the fake game does) must not be added again.
        cur = [c for c in cs.get("exhaust_pile", []) if isinstance(c, dict) and c.get("id")]
        if cur:  # (the fake game sends the pile cards without ids: then the last state's ids are all there is)
            left = Counter((str(c.get("id")), int(c.get("upgrades", 0) or 0)) for c in aside)
            have = Counter((str(c.get("id")), int(c.get("upgrades", 0) or 0)) for c in cur)
            kept = []
            for c in aside:
                k = (str(c.get("id")), int(c.get("upgrades", 0) or 0))
                if left[k] > have[k]:
                    kept.append(c)
                    left[k] -= 1
            aside = kept
        spec["aside"] = aside
    return spec, None
