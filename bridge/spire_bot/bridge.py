"""Bridge between the real game (CommunicationMod) and the Spire Bot network.

CommunicationMod starts this program, sends one JSON game state per line on stdin whenever the game is stable, and
treats every line we print on stdout as a game command. So stdout carries only "ready" and commands.

The bridge follows the game on the simulator step by step (lockstep): on a new run (floor 0, Neow) the simulator
starts from the game's seed and ascension. On each new state it looks for the simulator action (or two in a row)
whose result matches what the game shows (floor, HP, max HP, gold, deck, relics, potions, and in battle hand, energy
and monster HP). When one is found the simulator moves on, the network (with the key rule, see policy.py)
chooses an action for the new state, and the bridge turns it into a game command (see auto.py). When none is found the mismatch is logged (mismatch.jsonl) and the bridge waits for the next state.

Every state is saved raw (states.jsonl.gz). Logs go to the package's logs/ folder (LOG_DIR), the player's log to ADVICE.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import os
import signal
import subprocess
import sys
import time
import types
from collections import Counter
from pathlib import Path

import numpy as np

from . import config as CFG  # noqa: E402
from . import preflight  # noqa: E402
from . import messages as M  # noqa: E402
from . import panel as P  # noqa: E402
from .auto import CARD_ID_ALIAS  # noqa: E402

PANEL = None  # the in-game panel (panel.Panel), made in main()
RUN_OVER = M.Msg("R00")


def panel_show(*a, **kw):
    """Update the in-game panel. Never raises: the panel must not disturb the bot."""
    try:
        if PANEL is not None:
            PANEL.show(*a, **kw)
    except Exception as ex:
        log("errors.jsonl", {"t": time.time(), "error": f"panel: {ex!r}"})

ADVICE = CFG.ADVICE
LOG_DIR = CFG.LOG_ROOT / time.strftime("%Y%m%d-%H%M%S")
MODEL = CFG.MODEL
MAX_CANDS = int(os.environ.get("CM_MAX_CANDS", "512"))
PLAY_SETTLE = 1.0  # seconds between a card play and a potion (see main)

# Relics that stay out of the relic pools until the profile has unlocked them (game behavior: with the profile's
# locked relics left out, the simulator gives the game's relics).
UNLOCKABLE_RELICS = ["Omamori", "Prayer Wheel", "Shovel", "Art of War", "The Courier", "Pandora's Box", "Blue Candle",
                     "Dead Branch", "Singing Bowl", "Du-Vu Doll", "Smiling Mask", "Tiny Chest", "Cables", "DataDisk",
                     "Emotion Chip", "Runic Capacitor", "Turnip", "Symbiotic Virus", "Akabeko", "Yang", "CeramicFish",
                     "StrikeDummy", "TeardropLocket", "CloakClasp"]
PROFILE = CFG.PROFILE


def locked_relics(profile: Path = PROFILE) -> list:
    """Relics still locked in the player's profile (unlock value 2 = unlocked)."""
    try:
        un = json.loads(profile.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [r for r in UNLOCKABLE_RELICS if str(un.get(r)) != "2"]


# CM_FRAME_CLOCK=1 (set by tests/fake_game.py for the bridge it starts): the lockstep's clock is the frames the bridge
# has waited (60 a second) instead of the wall clock. Against the fake game no real time passes during a wait, so the
# time a state has stood (out of fights: OUT_SETTLE_SECONDS) could not be reached, or would depend on how fast the
# machine is. Not set in a real game: the clock is the wall clock, as before
FRAME_CLOCK = os.environ.get("CM_FRAME_CLOCK", "") == "1"


def advise(line: str):
    with open(ADVICE, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {line}\n")


def heartbeat():
    """Last time the bridge did anything."""
    try:
        (LOG_DIR / "heartbeat").write_text(f"{time.time():.0f}\n")
    except OSError:
        pass


def log(name: str, obj):
    line = json.dumps(obj, ensure_ascii=False, default=str) + "\n"
    if name in ("states.jsonl", "autosaves.jsonl"):  # the big ones: gzip (one member per line is still valid .gz)
        with gzip.open(LOG_DIR / (name + ".gz"), "at", encoding="utf-8") as f:
            f.write(line)
        return
    with open(LOG_DIR / name, "a", encoding="utf-8") as f:
        f.write(line)


# ---------- signatures: what must agree between the game and the simulator ----------

def _norm(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum() or ch == "+")


def _cm_card(c: dict) -> str:
    """A CommunicationMod card as the simulator names it. Names are localized, so
    use the card id: "Strike_R" -> strike, "AscendersBane" -> ascendersbane; "+" when upgraded."""
    if "id" not in c:  # tests/fake_game.py sends simulator names
        return _norm(c["name"])
    cid = c["id"]
    for suf in ("_R", "_G", "_B", "_P"):
        if cid.endswith(suf):
            cid = cid[: -len(suf)]
    cid = _norm(cid)
    cid = CARD_ID_ALIAS.get(cid, cid)  # the only card id that differs from its name (auto.CARD_ID_ALIAS)
    return cid + ("+" if c.get("upgrades", 0) else "")


# Game ids that differ from the display names the simulator uses: game id -> simulator name (both normalized). Add one
# here whenever a replay shows a "difference" that is only a name.
ID_ALIAS = {"steroidpotion": "flexpotion", "boot": "theboot", "neowsblessing": "neowslament", "yang": "duality",
            "papercrane": "paperkrane", "paperfrog": "paperphrog", "snakeskull": "sneckoskull",
            "cables": "goldplatedcables", "gremlinmask": "gremlinvisage", "sling": "slingofcourage",
            "cultistmask": "cultistheadpiece", "frozenegg2": "frozenegg", "moltenegg2": "moltenegg",
            "nlothsmask": "nlothshungryface", "toxicegg2": "toxicegg", "wingedgreaves": "wingboots"}


def _cm_thing(x: dict) -> str:
    k = _norm(x["id"] if "id" in x else x["name"])
    return ID_ALIAS.get(k, k)


# CommunicationMod screen_type -> the simulator's screen name; None: not compared (unsure how the two line up).
# The simulator's own names in capitals are accepted too (tests/fake_game.py sends those).
CM_SCREEN = {"EVENT": "event", "COMBAT_REWARD": "rewards", "BOSS_REWARD": "boss_relic", "GRID": "card_select",
             "MAP": "map", "CHEST": "treasure", "REST": "rest", "SHOP_ROOM": "shop", "SHOP_SCREEN": "shop",
             "CARD_REWARD": None, "HAND_SELECT": None, "NONE": None, "COMPLETE": None, "GAME_OVER": None}
CM_SCREEN.update({k.upper(): k for k in ("event", "rewards", "boss_relic", "card_select", "map", "treasure", "rest",
                                          "shop", "battle", "done")})


def _round_half_up(x: float) -> int:
    """libgdx MathUtils.round: (int)(x + 0.5) for x >= 0"""
    return int(x + 0.5)


def calling_bell_grid(gs: dict) -> bool:
    """the confirm-only card grid Calling Bell opens with its curse"""
    ss = gs.get("screen_state") or {}
    return str(gs.get("screen_type", "")).upper() == "GRID" and not gs.get("combat_state") \
        and bool(ss.get("confirm_up")) and not any(ss.get(k) for k in ("for_transform", "for_upgrade", "for_purge")) \
        and bool(ss.get("cards")) and all(c.get("id") == "CurseOfTheBell" for c in ss["cards"]) \
        and any(r.get("id") == "Calling Bell" for r in gs.get("relics", []))


def auto_already_marked() -> str:
    from .auto import ALREADY_MARKED
    return ALREADY_MARKED


def grid_picks_left(gs: dict):
    """On a card grid in a fight that takes more than one card (Liquid Memories with Sacred Bark: 2 from the discard
    pile): how many of its cards can still be picked, the cards shown less the ones already marked. Else None.

    The game marks each pick and moves the cards when the last one is picked (the grid then closes by itself); the
    simulator also moves them only at the end. So after a pick nothing either side shows has changed, except that
    the picked card can no longer be picked: the simulator offers one pick fewer. Without this count the state with
    one card marked fitted the simulator before its pick as well as after it, the bot decided again on the state
    before, sent the same pick again, and in the game that unmarked the card."""
    ss = gs.get("screen_state") or {}
    if str(gs.get("screen_type", "")).upper() != "GRID" or not gs.get("combat_state") or gs.get("room_phase") != "COMBAT":
        return None
    sel, cards = ss.get("selected_cards"), ss.get("cards")
    if not isinstance(sel, list) or not isinstance(cards, list) or int(ss.get("num_cards") or 0) < 2:
        return None
    return len(cards) - len(sel)


def sim_picks_left(e) -> int:
    """How many single picks the simulator's open choice still offers."""
    return sum(1 for a in e.legal_actions() if json.loads(e.action_info_json(a)).get("type") == "select")


def fight_start_choice(gs: dict) -> bool:
    """The fight's very start, where Toolbox opens its card choice before the first draw: turn 1, nothing in the
    hand, in the discard pile or in the exhaust pile, and the player has Toolbox.

    Other card choices in a fight show the same screen on turn 1 with an empty hand: Nilry's Codex at the turn's end
    with every card played, a Discovery card or a choice potion used after the hand is played out. They are told
    apart by the piles (a card has left the hand) and by Toolbox (a hand emptied by playing only powers leaves the
    piles empty too). Not used: "the draw pile holds the whole deck", since a relic can add cards to it at a fight's
    start (Mark of Pain's two Wounds)."""
    cs = gs.get("combat_state") or {}
    return bool(cs) and cs.get("turn") == 1 and not cs.get("hand") and not cs.get("discard_pile") \
        and not cs.get("exhaust_pile") and any(r.get("id") == "Toolbox" for r in gs.get("relics", []))


def stuck_choice(msg: dict, polls: int) -> bool:
    """A fight's card choice the game waits on that CommunicationMod does not report as ready: answer it as ready.

    CommunicationMod calls a state ready only when it sees a change of screen, screen-up or room phase since the last
    state it sent, or (its fallback) with the action queue empty; with end turn queued it never does
    (GameStateListener.hasDungeonStateChanged). Release game 5800165: Nilry's Codex offered three cards at the end of
    turn 5 (sent, ready); Time Warp ended turn 6 early and the Codex offered again: the same screen as the last state
    sent, end turn queued, the Codex's action on the queue, so never ready, though the game waits for the pick and the
    commands offer choose. The bot polled the state for a minute and stopped (R09).
    Only after two polls (ten seconds with the same not-ready state, polls counts them), only in a fight, only on the
    card reward screen with cards on it, and only when choose is among the commands."""
    if msg.get("ready_for_command", True) or "error" in msg or polls < 2:
        return False
    gs = msg.get("game_state") or {}
    return bool(msg.get("in_game") and gs.get("combat_state") and str(gs.get("screen_type", "")).upper() == "CARD_REWARD"
                and (gs.get("screen_state") or {}).get("cards") and "choose" in (msg.get("available_commands") or []))


class StuckChoice:
    """Counts the not-ready replies that are the same game state one after another; see(msg) is True when the
    stuck choice is to be answered (stuck_choice with that count). A different game state starts again from one, a
    ready state (or an error) from zero. While a wait the bot sent is running (waited(), until the next ready state)
    nothing is counted: the game is not ready during a wait, and two polls during it give the same state (release
    game 5800092, floor 42: wait 620 on Toolbox's choice)."""

    def __init__(self):
        self.last, self.count, self.waiting = None, 0, False

    def waited(self):
        self.waiting, self.last, self.count = True, None, 0

    def see(self, msg: dict) -> bool:
        if msg.get("ready_for_command", True) or "error" in msg:
            self.last, self.count, self.waiting = None, 0, False
            return False
        if self.waiting:
            return False
        gs = msg.get("game_state")
        self.count = self.count + 1 if gs == self.last else 1
        self.last = gs
        return stuck_choice(msg, self.count)


def hand_pick_limit_reached(ss: dict) -> bool:
    """A hand pick screen that takes a limited number of cards has that many picked (the game then offers no
    further pick, only its confirm)."""
    mx = ss.get("max_cards")
    return isinstance(mx, int) and 0 < mx <= len(ss.get("selected") or [])


def cm_signature(gs: dict, last_gs: dict | None = None) -> dict:
    if str(gs.get("screen_type", "")).upper() == "GAME_OVER":
        return {"screen": "done"}  # the run is over (the simulator's state then has no fight to compare)
    if gs.get("room_type") == "TrueVictoryRoom":
        # the room after the Heart (floor 57): the game plays its ending for some seconds and takes no command, then
        # shows GAME_OVER. The simulator's run ended at the Heart, won; that is in step, not a mismatch (it was
        # logged as one every few seconds and the player was told so at the moment of the win; game 5700032)
        return {"screen": "done", "heart_won": True}
    sig = {"floor": gs.get("floor"), "hp": gs.get("current_hp"), "max_hp": gs.get("max_hp"), "gold": gs.get("gold"),
           "deck": [_cm_card(c) for c in gs.get("deck", [])],
           "relics": sorted(_cm_thing(r) for r in gs.get("relics", [])),
           "potions": sorted(_cm_thing(p) for p in gs.get("potions", []) if p.get("id") != "Potion Slot"
                             and p.get("name") not in ("Potion Slot", "EMPTY_POTION_SLOT"))}
    scr = CM_SCREEN.get(str(gs.get("screen_type", "")).upper())
    ss = gs.get("screen_state") or {}
    if scr == "rewards" and not ss.get("rewards"):
        scr = None  # an empty reward screen (after Smoke Bomb's escape) waits for proceed; the simulator is on the map
    if scr == "card_select" and gs.get("room_phase") == "COMBAT" and gs.get("combat_state"):
        scr = None  # a card grid inside a fight (Liquid Memories, Exhume, ...): the simulator is still on "battle
    mk = scr == "event" and ss.get("event_id") == "Match and Keep!" and len(ss.get("options", [])) > 1 \
        and all(not o.get("disabled") for o in ss["options"])
    if mk:
        # Match and Keep!'s card grid: the game lists its face-down cards as event options, the simulator has a card
        # select screen over the 12 positions; compare how many cards can still be flipped
        scr = "card_select"
        # a card never shown is labelled "cardN", N its grid position (kept after a pair is taken); a card shown
        # once keeps its id as its label when face down again
        sig["mk_down"] = [_norm(o.get("label", o.get("text", ""))) for o in ss["options"]]
        del sig["deck"]  # a pair taken joins the deck only after its show-card effect; it leaves the list at once
    if scr == "event" and len(ss.get("options", [])) == 1:
        scr = None  # a one-button event screen ("leave" after an outcome, Neow's talk): the simulator has moved on
        if ss.get("event_id") == "The Joust":
            # the game takes the 50 gold at the bet and pays two "watch" screens later; the simulator does both at
            # once. On those screens only the losing bet's branch had the game's gold, so the lockstep could take the wrong
            # bet. The map after the event compares gold again
            del sig["gold"]
    if scr is None and ss.get("event_id") == "Spire Heart" and isinstance(sig.get("hp"), int) and sig.get("max_hp"):
        # the door before act 4: the simulator has already gone on to act 4 and healed for it, the game heals when the
        # door has opened (A5+: 75% of the missing HP, rounded; else to full). Compare the HP it will have.
        # Without the three keys the run ends at this door (no heal; the simulator's run is over): both are accepted,
        # each with its own simulator state (see fits)
        hp = sig.pop("hp")
        miss = sig["max_hp"] - hp
        heal = _round_half_up(miss * 0.75) if (gs.get("ascension_level") or 0) >= 5 else miss
        sig["hp_door"] = [hp, hp + heal]
    if scr == "treasure" and ss.get("chest_type") == "BossChest":
        # after a boss the game shows a chest to open while the simulator is already at the relic choice; once the
        # relic is taken the open chest waits for proceed while the simulator is on the next map
        scr = None if ss.get("chest_open") else "boss_relic"
        if ss.get("chest_open"):
            del sig["hp"]  # the simulator heals for the next act as soon as the relic is taken, the game on leaving
    if str(gs.get("screen_type", "")).upper() == "CARD_REWARD" and not gs.get("combat_state") \
            and gs.get("room_type") == "RestRoom":
        # Dream Catcher's card after resting: the branch still at the campfire fitted as well when resting changed
        # nothing we see (full HP) and was kept first; the bot then asked to rest again. The simulator must have left the campfire's choice
        sig["left_rest"] = True
    if str(gs.get("screen_type", "")).upper() == "CARD_REWARD" and not gs.get("combat_state") \
            and gs.get("room_type") in ("NeowRoom", "EventRoom"):
        # a card choice an event opened (Neow's "choose a card"): the branch still on the event fitted too (nothing we
        # see changes before the pick) and was kept first; the bot then chose the event's option again, sent as
        # "choose 0", which took the first card. The simulator must have left the event's choice
        sig["left_event"] = True
    if scr == "rest" and ss.get("has_rested"):
        scr = None  # the campfire after its choice, only "proceed" left: the simulator is already on the map
        sig["rest_done"] = True  # and must have left it: Recall (the ruby key) changes nothing else we see
    sel = ss.get("selected_cards") or []
    if str(gs.get("screen_type", "")).upper() == "GRID" and not gs.get("combat_state") \
            and int(ss.get("num_cards") or 1) > 1 and len(sel) < int(ss.get("num_cards") or 1):
        # a multi-pick grid (Astrolabe 3, Empty Cage 2) part way: the deck changes only after the last pick, so the
        # branch that had not picked yet fitted too and was kept first; the bot then asked for the same card again
        # and the game unselected it. The cards picked so far must match
        sig["grid_picked"] = sorted(_cm_card(c) for c in sel)
    if str(gs.get("screen_type", "")).upper() == "GRID" and gs.get("room_type") == "ShopRoom":
        # the shop's card removal: the simulator takes the fee when the grid opens, the game after the pick; the
        # next state compares gold again
        del sig["gold"]
    if calling_bell_grid(gs):
        # Calling Bell: the game shows its curse on a confirm grid and adds it to the deck on confirm, then opens the
        # three relics; the simulator adds the curse and opens the relics at once. Compare the deck with the curse in it; the screen is the simulator's rewards
        scr = None
        # Omamori with a charge left stops the curse (real game 5600448: after the confirm its counter was one less
        # and the deck had no curse): those cards do not join, and nothing below is given for them
        charges = max([int(r.get("counter") or 0) for r in gs.get("relics", []) if r.get("id") == "Omamori"] + [0])
        joining = ss.get("cards", [])[charges:]
        sig["deck"] = sig["deck"] + [_cm_card(c) for c in joining]
        if any(r.get("id") == "Darkstone Periapt" for r in gs.get("relics", [])):
            # and the curse joining gives Darkstone Periapt's 6 max HP, healed too
            n = len(joining)
            for k in ("hp", "max_hp"):
                if isinstance(sig.get(k), int):
                    sig[k] += 6 * n
        if any(r.get("id") == "CeramicFish" for r in gs.get("relics", [])) and isinstance(sig.get("gold"), int) \
                and not any(r.get("id") == "Ectoplasm" for r in gs.get("relics", [])):
            # and Ceramic Fish's 9 gold for each card that joins, which the game gives on the confirm too
            sig["gold"] += 9 * len(joining)
    if scr is not None:
        sig["screen"] = scr
    node = (gs.get("screen_state") or {}).get("current_node") if scr == "map" else None
    if node and 0 <= node.get("y", -1) <= 14:  # at a new act's start the game still names last act's boss (row 15)
        sig["map_xy"] = [node["x"], node["y"]]
    if str(gs.get("screen_type", "")).upper() == "COMBAT_REWARD":
        # keys on offer: taking one changes nothing else the signature sees. Only keys: a picked card leaves the
        # list before it joins the deck, so the whole list would race with the deck
        # gold too: with Ectoplasm taking it changes nothing else we see, so the simulator could stay put and the
        # model then asked for a gold reward the game no longer had
        # potions too: with Sozu a potion taken changes nothing else we see
        sig["rewards"] = sorted("GOLD" if r.get("reward_type") == "STOLEN_GOLD" else r.get("reward_type")
                                for r in ss.get("rewards", []) if r.get("reward_type", "").endswith(("_KEY", "GOLD", "POTION")))
    if str(gs.get("screen_type", "")).upper() == "SHOP_SCREEN":
        # what is on sale and for how much: each item with its price (prices alone would miss a different relic at
        # the same price)
        sig["shop_prices"] = sorted((_norm(x.get("id")) + "+" * int(bool(x.get("upgrades"))) if k == "cards" else _norm(x.get("id")),
                                     x.get("price")) for k in ("cards", "relics", "potions") for x in ss.get(k, []))
    cs = gs.get("combat_state")
    if str(gs.get("screen_type", "")).upper() == "COMPLETE":
        # a finished room waiting for proceed (A20: between the two act 3 bosses): the simulator is already in the
        # next fight (and on the next floor)
        sig.pop("floor", None)
        # but it must have left this floor: after the last boss a branch still in the fight fitted too (only gold,
        # deck, relics and potions are compared here), was kept first, and the bot played on in a fight the game
        # had already won
        sig["left_floor"] = gs.get("floor")
        sig.pop("hp", None)  # and has fired the next fight's start relics (Blood Vial)
        if any(r.get("id") == "MawBank" and r.get("counter") != -2 for r in gs.get("relics", [])):
            # and has Maw Bank's 12 gold for entering the next room (the relic is used up once gold is spent:
            # counter -2). With gold compared nothing fitted, the fight counted as won in the game only and the
            # simulator was rebuilt from the next floor's autosave (a clean-copy game, floors 50 and 51)
            sig.pop("gold", None)
        # and in that fight its deck leaves Parasites out (see below), so leave them out here too (a Parasite
        # from Writhing Mass)
        sig["deck"] = [c for c in sig["deck"] if c != "parasite"]
        # ... on both sides: after act 3's last boss the simulator has left the fight and put the Parasite into its
        # deck
        sig["deck_no_parasite"] = sig.pop("deck")
        return sig
    sig["combat"] = bool(cs) and gs.get("room_phase") == "COMBAT"
    if sig["combat"]:
        # Writhing Mass's Implant: the game puts the Parasite into the deck at once, the simulator when the fight ends;
        # Parasites are compared outside fights
        sig["deck"] = [c for c in sig["deck"] if c != "parasite"]
        pl = cs.get("player", {})
        sig["hand"] = [_cm_card(c) for c in cs.get("hand", [])]
        sig["energy"], sig["block"] = pl.get("energy"), pl.get("block")
        if str(gs.get("screen_type", "")).upper() == "CARD_REWARD" and fight_start_choice(gs):
            # a choice at the fight's start (Toolbox): the game runs the relics' battle-start effects in the order they
            # were taken, so a block relic taken after Toolbox (Anchor) gives its block only after the pick; the
            # simulator gives it first. The next state
            # compares block again
            del sig["block"]
        if any(m.get("id") == "WrithingMass" for m in cs.get("monsters", [])) \
                and any(r.get("id") == "CeramicFish" for r in gs.get("relics", [])):
            # and Ceramic Fish's 9 gold for that Parasite comes with it, in the game at once; gold is compared after the fight
            del sig["gold"]
        # (Toolbox's choice at a fight's start with Happy Flower firing: the simulator queues the flower's energy
        # behind the choice as the game does, so energy is compared as it is. The +1 that stood here only held with
        # an empty hand: release game 5800092 had Enchiridion's card in it and stopped)
        sig["piles"] = [len(cs.get(k, [])) for k in ("draw_pile", "discard_pile", "exhaust_pile")]
        if str(gs.get("screen_type", "")).upper() == "HAND_SELECT" and ss.get("selected") and (
                not cs.get("hand") or hand_pick_limit_reached(ss)):
            # a multi-pick hand screen (Elixir, Gambler's Brew, Forethought+) with every card picked, or one with a
            # limit (Purity: up to 3) with that many picked and cards still in the hand: the game takes no more
            # picks and waits for its confirm, the simulator has already closed the screen and moved the cards (and
            # drawn, for the Brew); the bridge sends the confirm and the next state is compared in full
            del sig["hand"], sig["piles"]
            # and what exhausting the picks sets off in the simulator, before the game's confirm (a monster's HP,
            # Feel No Pain's block, Sentinel's energy)
            sig["_confirm_pending"] = True  # (the monster keys are added below: dropped at the end)
            sig["select_closed"] = True  # and the simulator must have closed its screen (not still one pick short)
        elif str(gs.get("screen_type", "")).upper() in ("HAND_SELECT", "GRID", "CARD_REWARD"):
            # a choice open inside the fight (Armaments, Exhume, Discovery, Nilry's Codex at the turn's end): the
            # simulator must have one open too. Without this the branch that had not yet ended its turn fitted as well
            # (the hand was unchanged) and was kept first, and the bot sent "end" again
            sig["select_open"] = True
            if str(gs.get("screen_type", "")).upper() == "CARD_REWARD":
                # and a choice of three shows its cards: the simulator's own three must be the same. They come from
                # the card random stream, so they tell whether the stream is where the game's is (release game
                # 5600083 floor 42: after a Discovery the simulator offered other cards three turns running and
                # counted as in step, because the bridge then gave it the game's three)
                sig["offer"] = [c.get("id") for c in ss.get("cards") or []]
        elif str(gs.get("screen_type", "")).upper() == "NONE":
            # and no choice open in the game: the simulator must have closed its own too (a branch still on the choice
            # fitted as well and was kept first)
            sig["select_closed"] = True
        if str(gs.get("screen_type", "")).upper() == "GRID" and "piles" in sig:
            # Exhume's grid: the game sets the other Exhumes aside while its grid is open (its current exhaust pile
            # no longer holds them), and the simulator keeps them in its exhaust. Compare the exhaust card by card:
            # the game's current non-Exhume cards and Exhumes, plus the Exhumes the last ready state had and the
            # current pile no longer does (the held-aside ones). A game that keeps its pile (the fake game) can
            # already hold an Exhume there: it is compared as the game has it, not demanded a second time. The fake
            # game sends the pile cards without ids: there the grid cards are the non-Exhume exhaust and only the
            # Exhume count is known.
            ex_pile = cs.get("exhaust_pile") or []
            ex_cards = [c for c in ex_pile if isinstance(c, dict) and c.get("id")]
            grid_cards = (ss.get("cards") or []) if isinstance(ss, dict) else []
            if ex_cards:
                is_ex = lambda c: str(c.get("id", "")).lower().startswith("exhume")
                sig["exhume_current"] = [_cm_card(c) for c in ex_cards if is_ex(c)]
                sig["exhume_rest"] = [_cm_card(c) for c in ex_cards if not is_ex(c)]
                sig["aside_expected"] = []
                if last_gs is not None:
                    lcs = last_gs.get("combat_state") or {}
                    last_ex = [_cm_card(c) for c in lcs.get("exhaust_pile", [])
                               if isinstance(c, dict) and str(c.get("id", "")).lower().startswith("exhume")]
                    sig["aside_expected"] = list((Counter(last_ex) - Counter(sig["exhume_current"])).elements())
                sig["exhume_pile"] = len(sig["exhume_current"])
            else:
                sig["exhume_current"] = None  # (count only: see the fits check)
                sig["exhume_rest"] = [_norm(c.get("name", "")) for c in grid_cards]
                sig["aside_expected"] = []  # (the simulated game keeps its Exhumes in the pile)
                sig["exhume_pile"] = max(0, len(ex_pile) - len(grid_cards))
            sig["piles_aside"] = sig.pop("piles")
        if str(gs.get("screen_type", "")).upper() == "HAND_SELECT" and "hand" in sig:
            # a hand pick that takes only some cards (Dual Wield: attacks and powers; Armaments: upgradable ones): the
            # game sets the others aside until the pick, the simulator keeps them in the hand. The game's hand must be
            # part of the simulator's
            sig["hand_sub"] = sig.pop("hand")
        # living ones in list order: sorted HP alone could not tell two lice apart, and a split slime stays in the
        # game's list as a dead entry while the simulator reuses its slot
        sig["monsters"] = [(m["current_hp"], m.get("block", 0)) for m in cs.get("monsters", [])
                           if not m.get("is_gone") and m["current_hp"] > 0]
        # and their Weak and Vulnerable: a debuff on one of two monsters changes nothing else we see, so the branch
        # that debuffed the other one fitted too and could be kept (Blind on Spire Shield in the game, on Spire
        # Spear in the simulator)
        pw = lambda m, i: next((int(p.get("amount", 0)) for p in m.get("powers", []) if p.get("id") == i), 0)
        sig["monster_debuffs"] = [(pw(m, "Weakened"), pw(m, "Vulnerable")) for m in cs.get("monsters", [])
                                  if not m.get("is_gone") and m["current_hp"] > 0]
        # the card each Bronze Orb holds with Stasis: the orbs pick it from the card random stream, so after a
        # Discovery the search finds the extra draws when they pick, not when they die and give the card back
        st = lambda m: next((_cm_card(p["card"]) for p in m.get("powers", []) if p.get("id") == "Stasis"
                             and isinstance(p.get("card"), dict)), None)
        sig["monster_stasis"] = [st(m) for m in cs.get("monsters", []) if not m.get("is_gone") and m["current_hp"] > 0]
    if sig.pop("_confirm_pending", False):
        # (and the HP: a heal queued behind the pick, Toy Ornithopter's for the potion that opened it, is already in
        # the simulator's HP once its screen has closed, and in the game's only after the confirm)
        for k in ("monsters", "monster_debuffs", "monster_stasis", "block", "energy", "hp", "max_hp"):
            sig.pop(k, None)
    return sig


def sim_signature(env, want_battle: bool, want_screen: bool = True, want_xy: bool = False, want_shop: bool = False,
                  want_rewards: bool = False, want_rest_done: bool = False, want_mk: bool = False, want_closed: bool = False,
                  want_open: bool = False) -> dict:
    st = env.state()
    sig = {"floor": st["floor"], "hp": env.hp, "max_hp": env.max_hp, "gold": st["gold"],
           "deck": [_norm(name + ("+" if up else "")) for _, up, name in st["deck"]],
           "relics": sorted(_norm(r[1]) for r in st["relics"]),
           "potions": sorted(_norm(p[1]) for p in st["potions"] if p[1] != "EMPTY_POTION_SLOT")}
    from spire_sim import Space
    if st["screen"] == "card_select" and not st.get("battle"):  # a multi-pick grid's picks so far (cm_signature)
        sig["grid_picked"] = sorted(_norm(name + ("+" if up else "")) for up, name in st.get("select_picked", []))
    if env.done:
        sig["done"] = True  # (fits "hp_door": the run ended at the door before act 4)
        if env.heart_won:
            sig["heart_won"] = True  # (fits the Heart's COMPLETE screen: "left_floor")
    if want_screen:
        sig["screen"] = st["screen"]
    if want_xy:
        sig["map_xy"] = list(st["map_xy"])
    if want_rest_done:
        sig["rest_done"] = st["screen"] == "map"  # left the campfire: not still at it, nor at Smith's card choice
    if want_closed:  # a hand screen with every card picked (cm_signature)
        sig["select_closed"] = not (st.get("battle") or {}).get("card_select", False)
    if want_open:  # a choice open inside a fight (cm_signature)
        sig["select_open"] = bool((st.get("battle") or {}).get("card_select", False))
    if want_mk:  # Match and Keep!: face-down cards left to flip (cm_signature)
        if st["screen"] == "card_select":
            infos = sorted((json.loads(env.action_info_json(a)) for a in env.legal_actions()),
                           key=lambda i: i.get("grid_idx", -1))
            sig["mk_down"] = [_norm(i["card"]["id"]) if i.get("card") else f"card{i['grid_idx']}" for i in infos]
        else:
            sig["mk_down"] = None
    if want_rewards:
        sig["rewards"] = sorted(t for t in (st.get("rewards") or []) if t.endswith(("_KEY", "GOLD", "POTION")))
    if want_shop:
        sh = json.loads(env.room_json()).get("shop") or {}
        sig["shop_prices"] = sorted([(_norm(c["card"]["id"]) + "+" * int(bool(c["card"]["upgrades"])), c["price"]) for c in sh.get("cards", [])] +
                                    [(_norm(x["id"]), x["price"]) for k in ("relics", "potions") for x in sh.get(k, [])])
    b = st.get("battle")
    sig["combat"] = env.space == Space.BATTLE
    if sig["combat"]:  # see cm_signature: Parasites are compared outside fights
        sig["deck"] = [c for c in sig["deck"] if c != "parasite"]
    if want_battle and b:
        sig["hand"] = [_norm(c[3] + ("+" if c[1] else "")) for c in b["hand"]]
        if b.get("card_select"):  # the hand cards the open pick cannot take (cm_signature "hand_sub")
            can = {json.loads(env.action_info_json(a)).get("select") for a in env.legal_actions()}
            sig["hand_fixed"] = [h for i, h in enumerate(sig["hand"]) if i not in can]
        sig["energy"], sig["block"] = b["energy"], b["block"]
        sig["piles"] = [b["draw_pile"], b["discard_pile"], b["exhaust_pile"]]
        if b.get("card_select"):
            sig["select_task"] = b.get("card_select_task")
        if b.get("card_select") and b.get("card_select_task") in ("DISCOVERY", "CODEX"):
            sig["offer"] = [str(x) for x in env.select_cards()]  # (cm_signature) its three cards, by game id
        if b.get("card_select") and b.get("card_select_task") == "EXHUME":  # (cm_signature "piles_aside")
            ex = next((l for l in env.text().splitlines() if "exhaustPile" in l), "")
            names = re.findall(r"\(([^,()]+),", ex.split("{", 1)[-1]) if "{" in ex else []
            names = [_norm(n.strip()) for n in names]
            sig["sim_exhumes"] = [n for n in names if n.rstrip("+") == "exhume"]
            sig["sim_rest"] = [n for n in names if n.rstrip("+") != "exhume"]
            sig["aside"] = len(sig["sim_exhumes"])
        # the monsters still in the fight: alive and not escaped. A thief that has run keeps its HP; the game lists it
        # as gone (real game 5600155, floor 11: a Looter left while an Acid Slime was alive)
        here = [m for m in b["monsters"] if m["hp"] > 0 and m.get("targetable", True)]
        sig["monsters"] = [(m["hp"], m["block"]) for m in here]
        sig["monster_debuffs"] = [(int(m["weak"]), int(m["vulnerable"])) for m in here]
        sig["monster_stasis"] = [_norm(m["stasis"][0] + ("+" if m["stasis"][1] else "")) if m.get("stasis") else None
                                 for m in here]
    return sig


def escape_hp_ok(before, escaped, potion_heals: bool, game) -> bool:
    """The game's HP on a state that shows a fight just after Smoke Bomb was used: the HP before the bomb, or, when
    using a potion heals (potion_heals), anything from there up to the simulator's HP after the escape."""
    if not isinstance(game, int) or not isinstance(before, int):
        return game == before
    if game == before:
        return True
    return bool(potion_heals) and isinstance(escaped, int) and before < game <= escaped


def fits(sim: dict, want: dict) -> bool:
    """The simulator's signature agrees with the game's on every field the game's signature has (the game leaves a
    field out when it cannot be compared at that moment, e.g. HP at the opened boss chest)."""
    for k, v in want.items():
        if k in ("exhume_pile", "exhume_rest", "exhume_current", "aside_expected"):
            continue  # (cm_signature) used by piles_aside below
        if k == "piles_aside":
            # (cm_signature) the game's piles on an in-fight grid. On Exhume's grid the game's current exhaust pile
            # has the Exhumes it still holds, and the simulator's has those plus the ones the game set aside (the
            # last ready state's Exhumes the current pile no longer has): compare the non-Exhume cards card for card
            # and the Exhumes as that multiset. A size-only check could pass a branch missing a non-Exhume card and
            # holding an extra Exhume.
            sp = sim.get("piles")
            sim_ex = sim.get("sim_exhumes")
            if sim_ex is None:  # not an Exhume pick: the piles must agree exactly
                if sp is None or sp != v:
                    return False
            else:
                if sp is None or sp[:2] != v[:2]:
                    return False
                game_rest = want.get("exhume_rest")
                sim_rest = sim.get("sim_rest")
                if game_rest is None or sim_rest is None or Counter(sim_rest) != Counter(game_rest):
                    return False
                cur = want.get("exhume_current")
                aside = want.get("aside_expected") or []
                if cur is not None:
                    if Counter(sim_ex) != Counter(cur) + Counter(aside):
                        return False
                elif len(sim_ex) != (want.get("exhume_pile") or 0) + len(aside):
                    return False
        elif k == "left_floor":
            # (a run won at the Heart has ended in the simulator, still on the Heart's floor; the game shows the
            # COMPLETE screen first, then the victory room)
            if sim.get("heart_won") and sim.get("floor") == v:
                continue
            if not isinstance(v, int) or not isinstance(sim.get("floor"), int) or sim["floor"] <= v:
                return False
        elif k == "hand_sub":
            # (cm_signature) the game's hand on a hand pick screen: the simulator's, less some of the cards its pick
            # cannot take (the game may set them aside or show them); a branch that has not made a pick yet keeps a
            # pickable card more and does not fit (Gambling Chip)
            h = sim.get("hand")
            if h is None or Counter(v) - Counter(h) or Counter(h) - Counter(v) - Counter(sim.get("hand_fixed") or []):
                return False
        elif k == "hp_door":
            # (cm_signature) the Spire Heart event: the HP after act 4's heal, or as it is when the run ends here
            if sim.get("hp") != v[0 if sim.get("done") else 1]:
                return False
        elif k == "left_rest":
            if sim.get("screen") in (None, "rest"):  # (cm_signature) the campfire's choice has been made
                return False
        elif k == "left_event":
            if sim.get("screen") in (None, "event"):  # (cm_signature) the event's choice has been made
                return False
        elif k == "deck_no_parasite":
            if [c for c in sim.get("deck") or [] if c != "parasite"] != v:
                return False
        elif sim.get(k) != v:
            return False
    return True


def diff(a: dict, b: dict) -> dict:
    out = {}
    for k in set(a) | set(b):
        if a.get(k) != b.get(k):
            if isinstance(a.get(k), list) and isinstance(b.get(k), list):
                ca, cb = Counter(a[k]), Counter(b[k])
                out[k] = {"game_only": list((ca - cb).elements()), "sim_only": list((cb - ca).elements())}
            else:
                out[k] = {"game": a.get(k), "sim": b.get(k)}
    return out


# ---------- the network, one decision at a time ----------

# the network, with the last-chance key rule; in elite and boss fights the fair search (search.py) unless turned off
SEARCH, KEY_RULE = (f"fair_elite_boss_{CFG.SEARCH_SCORE}" if CFG.SEARCH else "none"), "last"
RUBY_HP = CFG.RUBY_HP
# Which way the player faces with Surrounded (Spire Shield and Spear). "mouse": a card or potion aimed at a
# monster turns the player toward it, as a person plays and as the simulator does by default; the game does this only
# when the Spire Bot mod (spirebot.jar) is loaded with its facing part, since CommunicationMod's commands do not turn
# the player. "cm": no turning, CommunicationMod alone. CM_FACING_MOD=1/0 says so (tests/fake_game.py); otherwise
# main() looks at the game that started the bridge (game_facing).
FACING = {"1": "mouse", "0": "cm"}.get(os.environ.get("CM_FACING_MOD", ""), "cm")


def use_facing(facing: str):
    from spire_sim import _core
    _core.set_cm_facing(facing == "cm")


def record_facing(record_dir) -> str:
    """the facing a record was played with: its config event's "facing"; older records have none and
    were all played without the mod's turning ("cm")"""
    try:
        for line in open(Path(record_dir) / "events.jsonl", encoding="utf-8"):
            if '"config"' in line:
                return json.loads(line).get("facing", "cm")
    except OSError:
        pass
    return "cm"


def use_record_facing(record_dir) -> str:
    """for a replay: set the simulator's facing to the record's"""
    f = record_facing(record_dir)
    use_facing(f)
    return f


from .policy import Policy as System  # noqa: E402


# ---------- lockstep ----------

class Lockstep:
    def __init__(self, system: System | None):
        self.system, self.env, self.seed, self.synced = system, None, None, False
        self.cands = []
        self.capped = 0
        # A discovery-style choice inside a fight (Attack/Skill/Power/Colorless Potion, Discovery): the game redraws
        # its three cards every frame, so the card random stream drifts by an amount we cannot know. From then
        # until the fight ends a mismatch in that fight is resynced from the game's state
        # (set_battle_from_cm) instead of ending the comparison; every resync is logged.
        self.rng_drift_floor = None
        self.prefer = None  # bits of the action auto mode last sent (match tries it first)
        from .cm_state import PlayedCounter, load_moves
        self.played, self.moves = PlayedCounter(), load_moves()
        # A curse on a transform grid's confirm screen: the game's preview redraws a random curse every 0.1 s from the
        # seeded card stream, so it drifts by an amount we cannot know. Until the next room's autosave (written on entering it, with the game's RNG counters)
        # the drift stands; then the simulator is rebuilt from that save, if the rebuilt one fits the game.
        self.card_rng_drift_floor = None
        self.latest_save = None  # the newest decoded autosave (AutosaveSnapshots), set by the caller
        self.used_save = None
        # CM_RESYNC=all (the default): a mismatch the simulator cannot follow is taken as a
        # rule miss and resynced from the game instead of stopping the game. In a fight: from the game's state
        # (set_battle_from_cm, as after a Discovery). Out of fights: from this floor's autosave, else the next floor's.
        # Each one is logged in rule_miss.jsonl; a failed resync stops the game as before
        self.resync_all = CFG.RESYNC == "all"
        self.out_miss = None  # (floor, signature) of the last unfollowed state out of fights
        self.out_miss_since = 0.0  # when it was first seen
        self.now = None  # a replay sets the record's time of each state here; None: the wall clock

    def new_run(self, gs: dict):
        from spire_sim import Env, _core
        locked = locked_relics()
        unknown = _core.set_locked_relics(locked) if hasattr(_core, "set_locked_relics") else locked
        log("events.jsonl", {"t": time.time(), "event": "locked_relics", "locked": locked, "unknown": unknown})
        self.seed = int(gs["seed"]) & (2**64 - 1)
        self.env = Env(self.seed, int(gs.get("ascension_level", 0)))
        # CM_PACE=bot (the fake game's bot-pace games): the new run's simulator at the bot's pace (no Secret Portal), as the
        # fake plays it. In the real game each room's autosave sets the pace from the game's playtime anyway
        self.env.speedrun_pace = CFG.PACE == "bot"
        self.cands = [self.env]
        self.synced = True
        log("events.jsonl", {"t": time.time(), "event": "new_run", "seed": self.seed, "asc": gs.get("ascension_level"),
                             "class": gs.get("class")})
        advise(f"New run: seed {self.seed}, A{gs.get('ascension_level')}, {gs.get('class')}")

    def _want(self, gs: dict) -> dict:
        """cm_signature with the last ready fight on the same floor: Exhume's grid needs the Exhumes the game holds
        aside (the last state's exhaust, before the card was played)."""
        last = getattr(self, "last_ready_fight", None)
        return cm_signature(gs, last[0] if last and last[0].get("floor") == gs.get("floor") else None)

    def resync_battle(self, gs: dict, played: dict) -> bool:
        from .cm_state import to_set_json
        e = self.env.clone()
        closed_select = 0
        if str(gs.get("screen_type", "")).upper() == "NONE":
            # the game has no choice open, but the clone can still wait for a drifted Discovery's pick:
            # close it with any pick first; hand and piles are then overwritten from the game anyway
            while (e.state().get("battle") or {}).get("card_select") and closed_select < 5:
                sel = [a for a in e.legal_actions() if json.loads(e.action_info_json(a)).get("type") in
                       ("select", "multi_select")]
                if not sel:
                    break
                e.step(sel[0])
                closed_select += 1
        if not (e.state().get("battle") and not e.done):
            # closing the choice ended the fight (its pick ran the monsters' turn): the game state itself closes it
            # (set_battle_from_cm on a NONE screen)
            e, closed_select = self.env.clone(), -closed_select
        skipped = e.set_battle_from_cm(to_set_json(gs, self.moves, played))
        want = self._want(gs)
        # (a choice of three rebuilt here keeps whatever three cards the simulator drew: they are not compared, the
        # game's three are given to it right after, _take_game_cards)
        want.pop("offer", None)
        if str(gs.get("screen_type", "")).upper() == "NONE":
            want["select_closed"] = True  # and the simulator must not be left on a choice screen
        sig = lambda c: sim_signature(c, True, "screen" in want or "left_rest" in want or "left_event" in want, "map_xy" in want, want_closed="select_closed" in want,
                                      want_open="select_open" in want)
        ok = fits(sig(e), want)
        replayed = None
        last = getattr(self, "last_ready_fight", None)
        if not ok and "select_open" in want and last and last[0].get("floor") == gs.get("floor"):
            # a choice open in the game (Exhume's grid, Armaments, ...): the played card has left the game's hand, so
            # the open screen cannot be rebuilt from this state. Rebuild the last ready state before it from the game
            # and replay the bot's actions into the open screen
            p = self.env.clone()
            while (p.state().get("battle") or {}).get("card_select"):
                sel = [a for a in p.legal_actions() if json.loads(p.action_info_json(a)).get("type") in
                       ("select", "multi_select")]
                if not sel:
                    break
                p.step(sel[0])
            p.set_battle_from_cm(to_set_json(last[0], self.moves, last[1]))
            for a in sorted(p.legal_actions(), key=lambda a: a != self.prefer):
                c = p.clone()
                c.step(a)
                if fits(sig(c), want):
                    ok, e, replayed = True, c, a
                    break
        opened = None
        if not ok and "select_open" in want and hasattr(e, "open_select_from_cm"):
            # No replay reached the game's open choice (no ready state before it on this floor, or the bot's
            # action there differs): open it from the game's state itself, the card in play and the choice's cards
            from .cm_state import open_select_spec
            spec, why = open_select_spec(gs, last[0] if last and last[0].get("floor") == gs.get("floor") else None)
            if spec is None:
                opened = why
            else:
                c = e.clone()
                problems = c.open_select_from_cm(json.dumps(spec))
                opened = problems or spec["task"]
                if not problems and fits(sig(c), want):
                    ok, e = True, c
        log("events.jsonl", {"t": time.time(), "event": "resync_from_game", "floor": gs.get("floor"),
                             "turn": (gs.get("combat_state") or {}).get("turn"), "ok": ok, "skipped": skipped,
                             "closed_select": closed_select, "replayed_into_select": replayed,
                             "opened_select": opened})
        if ok:
            self.cands, self.env = [e], e
        return ok

    def resync_from_save(self, gs: dict, ready: bool = False, same_floor: bool = False) -> bool:
        """After a card-stream drift: the first autosave of a later floor rebuilds the simulator (game's RNG counters).
        same_floor (CM_RESYNC=all, a rule miss out of a fight): this floor's newest save, then up to two steps to the
        game's state; the save is used up only when that fits."""
        sv = self.latest_save
        if sv is None or sv is self.used_save or not isinstance(gs.get("floor"), int) \
                or sv.get("floor_num") != gs.get("floor") \
                or (not same_floor and gs["floor"] <= self.card_rng_drift_floor) \
                or (int(sv.get("seed", -1)) & (2**64 - 1)) != self.seed:  # (a save left from another run)
            return False
        if not same_floor:
            self.used_save = sv
        from spire_sim import _core
        old = (self.cands, self.env)
        try:
            e = _core.env_from_save(json.dumps(sv))
        except Exception as ex:  # a save the simulator cannot load: keep following as before
            log("events.jsonl", {"t": time.time(), "event": "resync_from_autosave", "floor": gs.get("floor"),
                                 "ok": False, "error": repr(ex)})
            return False
        # a rebuild that cannot be played on is no match, whatever the comparison says (it looks only at the fields the
        # game's state has): a finished run (an event room whose event the simulator could not set up: two clean-copy
        # games, floor 38), or a fight with no encounter (a "?" room that was a fight, floor 37)
        problem = None
        if e.done and gs.get("screen_type") not in ("GAME_OVER", "COMPLETE"):
            problem = "the save rebuilds into a finished run (no event, no legal action)"
        elif ((e.state().get("battle") or {}).get("encounter")) == "INVALID":
            problem = "the save rebuilds into a fight with no encounter"
        if problem is not None:
            log("events.jsonl", {"t": time.time(), "event": "resync_from_autosave", "floor": gs.get("floor"),
                                 "ok": False, "problem": problem, "room": str(sv.get("current_room", "")).split(".")[-1],
                                 "card_seed_count": sv.get("card_seed_count")})
            advise(f"The game's autosave of floor {gs.get('floor')} could not be used: {problem}.")
            return False
        if hasattr(e, "copy_event_lists"):
            e.copy_event_lists(old[1])  # (the save has no shrine list: keep this run's)
        self.cands, self.env = [e], e
        ok = self.match(gs, ready)
        log("events.jsonl", {"t": time.time(), "event": "resync_from_autosave", "floor": gs.get("floor"), "ok": ok,
                             "card_seed_count": sv.get("card_seed_count")})
        if ok:
            self.card_rng_drift_floor = None
            self.used_save = sv
        else:
            self.cands, self.env = old
        return ok

    def load_save(self, save: dict):
        """The game is about to load this save (Continue); start the simulator from the same JSON."""
        from spire_sim import _core
        locked = locked_relics()
        _core.set_locked_relics(locked)
        self.seed = int(save["seed"]) & (2**64 - 1)
        self.env = _core.env_from_save(json.dumps(save))
        self.cands = [self.env]
        self.synced = True
        self.from_save = True
        log("events.jsonl", {"t": time.time(), "event": "load_save", "seed": self.seed, "floor": save.get("floor_num")})

    def match(self, gs: dict, ready: bool = False) -> bool:
        """Advance the simulator to the game's state; True if found.

        Several simulator actions can look the same in the signature (two Neow options whose effect shows later,
        Strike on either of two equal monsters), so every simulator state consistent with what the game has shown
        so far is kept (at most MAX_CANDS); later states rule the wrong ones out. A candidate may stay put (the game
        repeated a state) or take one step; if none fits, two steps."""
        self.prev_cands = list(self.cands)  # (_rng_search steps them again with the card random stream moved on)
        self.prev_paths = getattr(self, "paths", None) or {}  # (and replays their paths from the last fit)
        want = self._want(gs)
        battle = "energy" in want  # (not "hand": a hand screen with every card picked leaves hand and piles out)
        scr = "screen" in want or "left_rest" in want or "left_event" in want
        xy = "map_xy" in want
        shop = "shop_prices" in want
        rew = "rewards" in want
        restw = "rest_done" in want
        mkw = "mk_down" in want
        clw = "select_closed" in want
        opw = "select_open" in want
        new, seen, origins = [], set(), []
        picks_left = grid_picks_left(gs)  # (None except on a several-card grid in a fight)

        def keep(e, origin=None):
            if picks_left is not None and (e.done or sim_picks_left(e) != picks_left):
                return  # (grid_picks_left) not the state with this many cards still to pick from
            # Merge only branches that agree on everything we can read: the full text (piles in order), the legal
            # actions and every feature array. Merging on the visible state alone dropped the true branch.
            f = e.features()
            k = (e.text(), json.dumps(e.state(), sort_keys=True, default=str), tuple(e.legal_actions()), e.steps,
                 hashlib.sha1(b"".join(np.ascontiguousarray(v).tobytes() for _, v in sorted(f.items())
                                       if hasattr(v, "tobytes"))).hexdigest())
            if e.screen == "card_select":  # a card chosen on a grid before its confirm step can hide in state we
                k = (k, id(e))              # cannot read: never merge there. Elsewhere the full key above is enough
                                            # (giving away one of five identical Strikes made 5 copies)
            if k not in seen:
                seen.add(k)
                if len(new) < MAX_CANDS:
                    new.append(e)
                    origins.append(origin)  # (parent, actions) for _rng_search's replay; None: not a plain step
                else:
                    self.capped += 1

        for e in self.cands:
            if fits(sim_signature(e, battle, scr, xy, shop, rew, restw, mkw, clw, opw), want):
                keep(e, (e, ()))
            if not e.done:
                # the action the bot sent first: branches that differ only in what we cannot read (which of the
                # identical cards Bottled Flame took) are merged, and the first one kept must be the bot's
                for a in self._one_step_actions(e):
                    c = e.clone()
                    c.step(a)
                    if fits(sim_signature(c, battle, scr, xy, shop, rew, restw, mkw, clw, opw), want):
                        keep(c, (e, (a,)))
        if not new:
            for e in self.cands:
                if e.done:
                    continue
                for a in self._grid_pick_or_all(e):  # (as in the one-step search: on the bot's grid pick, only it)
                    c = e.clone()
                    c.step(a)
                    if c.done:
                        continue
                    for b in c.legal_actions():
                        d = c.clone()
                        d.step(b)
                        if fits(sim_signature(d, battle, scr, xy, shop, rew, restw, mkw, clw, opw), want):
                            keep(d, (e, (a, b)))
        if not new and battle and not ready:
            # (only on a state the game has not marked ready: a ready state is never part way through a draw, and
            # accepting one there hid a real difference, e.g. Dead Branch giving another card in the drifted simulator)
            # Mid-draw: CommunicationMod can report a state while the new turn's cards are still being drawn
            # (hand 1 card, draw 10, the simulator already has the full hand). Accept a candidate
            # (or its one-step child) that agrees on everything else, whose hand holds the game's hand, and with the same
            # total number of cards in hand and piles. The next state is then compared in full.
            def mid_draw(c):
                sg = sim_signature(c, battle, scr, xy, shop, rew, restw, mkw, clw, opw)
                rest = {k: v for k, v in want.items() if k not in ("hand", "piles")}
                if {k: sg.get(k) for k in rest} != rest or "hand" not in sg:
                    return False
                gh, sh = Counter(want["hand"]), Counter(sg["hand"])
                return not (gh - sh) and len(want["hand"]) + sum(want["piles"]) == len(sg["hand"]) + sum(sg["piles"])
            for e in self.cands:
                for a, c in [(None, e)] + ([] if e.done else [(a, self._child(e, a)) for a in e.legal_actions()]):
                    if mid_draw(c):
                        # (a path through it replays the step without checking it: the game had not drawn yet)
                        keep(c, (e, () if a is None else (a,), "mid_draw"))
            if new:
                log("events.jsonl", {"t": time.time(), "event": "mid_draw_accepted", "floor": gs.get("floor"),
                                     "game_hand": want["hand"], "game_piles": want["piles"]})
        if not new and battle:
            # Smoke Bomb: the simulator leaves the fight at once; the game shows the fight a few more states with the
            # bomb gone while the escape plays (no candidate fitted, and a resync that copied the potion
            # slots left the simulator in the fight the game had left). Accept the escaped branch when the game's
            # state is the fight as it was with only the bomb gone, then every state of the fight on that floor until
            # the game leaves it (the last frames still change: the hand and energy of the turn's end)
            esc = getattr(self, "escaping", None)
            if esc is not None and gs.get("floor") == esc[0]:
                for c in esc[1]:
                    keep(c, None)
            for e in ([] if new else self.cands):
                if e.done:
                    continue
                for a in e.legal_actions():
                    i = json.loads(e.action_info_json(a))
                    if i.get("type") != "potion" or i.get("potion") != "SmokeBomb" or i.get("discard"):
                        continue
                    c = self._child(e, a)
                    if (c.state() or {}).get("battle"):
                        continue
                    se = sim_signature(e, battle, scr, xy, shop, rew, restw, mkw, clw, opw)
                    left = Counter(se.get("potions") or [])
                    left["smokebomb"] -= 1
                    # Using the potion can itself heal (Toy Ornithopter), and the game shows that at once, while the
                    # fight is still on screen: the fight as it was, the bomb gone, and the HP changed by that heal.
                    # The simulator's escaped branch has the HP after everything the escape gives (the potion's heal
                    # and the end-of-fight heal, each as the other relics change it: half as much again with Magic
                    # Flower, none with Mark of the Bloom), so the game's HP must lie between the HP before the bomb
                    # and that; without Toy Ornithopter it must be the HP before the bomb
                    if left["smokebomb"] >= 0 and sorted(left.elements()) == sorted(want.get("potions") or []) \
                            and escape_hp_ok(se.get("hp"), c.hp, "toyornithopter" in (se.get("relics") or []), want.get("hp")) \
                            and fits(dict(se, potions=want.get("potions"), hp=want.get("hp")), want):
                        keep(c, None)
            if new:
                self.escaping = (gs.get("floor"), list(new))
                log("events.jsonl", {"t": time.time(), "event": "smoke_bomb_escape_accepted", "floor": gs.get("floor")})
        if not battle:
            self.escaping = None
        if not new and not battle and "deck" in want and hasattr(self.env, "reorder_deck"):
            # the same cards in another order: identical copies picked differently (Neow's War Paint
            # upgraded the 1st and 4th Defend in the game, the 2nd and 3rd in the simulator). The order decides the
            # draw pile at each fight's start, so put the simulator's deck in the game's order
            gdeck = [(c.get("id", ""), bool(c.get("upgrades"))) for c in gs.get("deck", [])]
            for e in self.cands:
                for c in [e] + ([] if e.done else [self._child(e, a) for a in self._grid_pick_or_all(e)]):
                    sg = sim_signature(c, battle, scr, xy, shop, rew, restw, mkw, clw, opw)
                    if sorted(sg.get("deck") or []) != sorted(want["deck"]) or not fits(dict(sg, deck=want["deck"]), want):
                        continue
                    d = c.clone()
                    if d.reorder_deck(gdeck) and fits(sim_signature(d, battle, scr, xy, shop, rew, restw, mkw, clw, opw), want):
                        keep(d)
            if new:
                log("events.jsonl", {"t": time.time(), "event": "deck_reordered", "floor": gs.get("floor")})
        if new:
            ev = (gs.get("screen_state") or {}).get("event_id") if str(gs.get("screen_type")).upper() == "EVENT" else None
            if ev and gs.get("floor", 0) > 0 and new[0].screen == "event" and hasattr(new[0], "set_event"):
                # the game's event decides (the simulator can roll another one: Match and Keep! is disabled there)
                res = [c.set_event(ev) for c in new]
                if "set" in res:
                    log("events.jsonl", {"t": time.time(), "event": "set_event", "game_event": ev, "results": dict(Counter(res))})
            self._record_paths(gs, want, ready, new, origins)
            self.cands = new
            self.env = new[0]
            self._grid_pick_waited(gs, True)
            return True
        self._grid_pick_waited(gs, False)
        log("mismatch.jsonl", {"t": time.time(), "floor": gs.get("floor"), "screen": gs.get("screen_type"),
                               "candidates": len(self.cands), "capped": self.capped, "diff": diff(want, sim_signature(self.env, battle, scr, xy, shop, rew, restw, mkw, clw, opw))})
        return False

    def _mark_choice_drift(self, gs: dict):
        """A choice of three in a fight that redraws on every frame: this floor's card random stream is off."""
        if self.rng_drift_floor != gs.get("floor"):
            log("events.jsonl", {"t": time.time(), "event": "rng_drift", "floor": gs.get("floor"),
                                 "why": "a fight's card choice (the game redraws on each frame of its "
                                        "duration: the card random stream is off for the rest of the floor)"})
        self.rng_drift_floor = gs.get("floor")

    # extra card-stream draws tried. A count, not a time: with a time limit how far the search got depended on the
    # machine's speed at that moment (release game 5600083 replayed twice: 86 and 88 tries). The game draws three
    # cards for every frame the choice's action lasts, so the count grows with the frames: at five times the speed
    # (SuperFastMode, the 500 release games) 833 searches found counts up to 35; at normal speed and 60 frames a
    # second clean-copy games had 40 to 49; a 144 Hz display should be near 100 to 120 (not measured). The smallest
    # count that fits is taken, so a search that finds one costs the same whatever this bound is; only a search that
    # finds none tries them all
    RNG_SEARCH_MAX = 300

    @staticmethod
    def _sig_for(e, want: dict) -> dict:
        """the simulator's signature with the parts match compares for this game signature"""
        return sim_signature(e, "energy" in want, "screen" in want or "left_rest" in want or "left_event" in want,
                             "map_xy" in want, "shop_prices" in want, "rewards" in want, "rest_done" in want,
                             "mk_down" in want, "select_closed" in want, "select_open" in want)

    @staticmethod
    def _hand_costs(gs: dict) -> list:
        return [max(int(c.get("cost", -1)), -1) for c in (gs.get("combat_state") or {}).get("hand", [])]

    @staticmethod
    def _sim_costs(e) -> list:
        return [max(int(c[2]), -1) for c in (e.state().get("battle") or {}).get("hand") or []]

    def _record_paths(self, gs: dict, want: dict, ready: bool, new: list, origins: list):
        """On a drifted fight floor, each candidate's path since the last fit: the state to move the card random
        stream on from (its anchor) and every step since then, (actions, game signature, hand costs on a ready state).
        _rng_search replays it. A mid-draw state (see match) is replayed without being compared. A draw made before the step that shows it (Wild Strike's Wound put in the draw pile
        at a card-random spot, seen at the next turn's draw) cannot be found by stepping the last state again."""
        if not ("energy" in want and self.rng_drift_floor == gs.get("floor")):
            self.paths = {}
            return
        old, self.paths = getattr(self, "paths", None) or {}, {}
        # (a hand pick that shows only part of the hand, "hand_sub": the game's hand is shorter than the simulator's,
        # so the two lists of costs cannot be compared; real game 5700048, floor 46, Armaments after a Discovery)
        step = (want, self._hand_costs(gs) if ready and "hand_sub" not in want else None)
        for c, origin in zip(new, origins):
            if origin is None or self._redrawing_choice(gs, c):
                # the Discovery's own offer (its first frame's draws) or a state not reached by plain steps: the
                # extra draws come after it, so it is the anchor. The Codex's choice draws once: the path goes on
                # through it, and its three cards then hold the count found before it
                self.paths[id(c)] = (c, c.clone(), [])
                continue
            p, acts = origin[:2]
            st = (acts, None, None) if len(origin) > 2 else (acts,) + step  # (mid-draw: not checked)
            base = old.get(id(p))
            if base is not None and base[0] is p:
                self.paths[id(c)] = (c, base[1], base[2] + [st])
            else:  # (the last fit: a found search, a resync, a new floor) step it again
                self.paths[id(c)] = (c, p.clone(), [st])

    @staticmethod
    def _redrawing_choice(gs: dict, e) -> bool:
        """the game shows a fight's choice of three that redraws its cards on every frame after the first (Discovery,
        the Attack / Skill / Power / Colorless potions; not the Codex, not Toolbox's opening choice): the card
        random stream moves on by an unknown count after the three cards shown"""
        return (str(gs.get("screen_type", "")).upper() == "CARD_REWARD" and gs.get("room_phase") == "COMBAT"
                and not fight_start_choice(gs)
                and ((e.state().get("battle") or {}).get("card_select_task") == "DISCOVERY"))

    # CM_COUNT_K=1 (offline replays): after the first count that fits, the rest are tried as well, for at most
    # RNG_COUNT_SECONDS, only to say how many fit (fitting_k, other_k, counted_to in the event). The count kept is
    # the smallest either way. Off in a game: it would make the game wait half a second at every search
    RNG_COUNT_ALL = os.environ.get("CM_COUNT_K", "") == "1"
    RNG_COUNT_SECONDS = 0.5

    def _rng_search(self, gs: dict) -> bool:
        """On a floor where a Discovery-type choice left the card random stream off (the game redraws the cards on
        every frame until the action's duration has run), find how many extra draws the game made: step the branches
        from before this state again with the stream moved on by k = 1, 2, ... until one fits the game exactly, hand
        costs included (Snecko Eye's costs, Mummified Hand's pick, a Bronze Orb's Stasis card all come from it), and on
        a choice of three its three cards as well (they are the stream's next draws: the sharpest check there is).
        The smallest k up to RNG_SEARCH_MAX that fits is kept (with CM_COUNT_K=1 the event also says how many
        fitted); a wrong one shows at the next draw or the next choice and is searched again from there."""
        prev = [e for e in (getattr(self, "prev_cands", None) or []) if not e.done]
        if not prev:
            return False
        paths = getattr(self, "prev_paths", None) or {}
        want = self._want(gs)
        battle = "energy" in want
        cs = gs.get("combat_state") or {}
        gc = [max(int(c.get("cost", -1)), -1) for c in cs.get("hand", [])]

        def sig(e):
            return sim_signature(e, battle, "screen" in want, "map_xy" in want, "shop_prices" in want,
                                 "rewards" in want, "rest_done" in want, "mk_down" in want, "select_closed" in want,
                                 "select_open" in want)

        def costs_ok(e):
            sh = (e.state().get("battle") or {}).get("hand") or []
            return len(sh) == len(gc) and [max(int(c[2]), -1) for c in sh] == gc

        # from each candidate's anchor (the last fit, see _record_paths): the card stream moved on there, then its
        # steps since replayed, each still fitting what the game showed then; without a path, the candidate itself
        tries = []
        for e in prev:
            path = paths.get(id(e))
            tries.append((path[1], path[2]) if path is not None and path[0] is e else (e, []))

        def replay(anchor, steps, k):
            c = anchor.clone()
            c.advance_card_rng(k)
            for acts, w, wc in steps:
                for a in acts:
                    if c.done or a not in c.legal_actions():
                        return None
                    c.step(a)
                if w is not None and (not fits(self._sig_for(c, w), w) or (wc is not None and self._sim_costs(c) != wc)):
                    return None
            return c

        t0 = time.time()
        first, fitting, t_first = None, [], None
        for k in range(1, self.RNG_SEARCH_MAX + 1):
            if first is not None and (not self.RNG_COUNT_ALL or time.time() - t_first > self.RNG_COUNT_SECONDS):
                k -= 1
                break
            found = []
            for anchor, steps in tries:
                r = replay(anchor, steps, k)
                if r is None:
                    continue
                # the last step: the bot's action first; then every action (in a replay the bot's is not known), none
                # for a state the game repeated
                acts = [] if r.done else sorted(r.legal_actions(), key=lambda a: a != self.prefer)
                for a in [None] + acts:
                    c = r.clone()
                    if a is not None:
                        c.step(a)
                    if fits(sig(c), want) and costs_ok(c):
                        # the path keeps its anchor: the first k that fits can be wrong where we cannot see (the
                        # draw pile's order), and the next search then tries every k against every state since the
                        # anchor again (a k can fit one turn and the next turn not follow)
                        if self._redrawing_choice(gs, c):
                            found.append((c, (c, c.clone(), [])))  # (_record_paths: a new count starts here)
                        else:
                            found.append((c, (c, anchor, steps + [(() if a is None else (a,), want, gc)])))
                        break
            if found:
                fitting.append(k)
                if first is None:
                    first, t_first = (k, found), time.time()
        counted_to = k
        if first is not None:
            # every candidate that fits with this k is kept, not only the first: candidates the fight cannot tell
            # apart (act 2's first nodes all lead to the same fight) part only later (the search could
            # keep x = 1 while the game was at x = 5, and the next map screen would fit nothing)
            k, found = first
            found = found[:MAX_CANDS]
            self.cands = [c for c, _ in found]
            self.env = self.cands[0]
            self.paths = {id(c): p for c, p in found}
            log("events.jsonl", {"t": time.time(), "event": "card_rng_found", "floor": gs.get("floor"),
                                 "turn": cs.get("turn"), "extra_draws": k, "with_offer": "offer" in want,
                                 **({"fitting_k": len(fitting), "other_k": fitting[1:6], "counted_to": counted_to}
                                    if self.RNG_COUNT_ALL else {}),
                                 "replayed_steps": max(len(found[0][1][2]) - 1, 0),
                                 "candidates": len(found), "seconds": round(time.time() - t0, 2)})
            return True
        log("events.jsonl", {"t": time.time(), "event": "card_rng_not_found", "floor": gs.get("floor"),
                             "turn": cs.get("turn"), "tried": k, "with_offer": "offer" in want,
                             "seconds": round(time.time() - t0, 2)})
        return False

    def _offer_differs(self, gs: dict):
        """On a choice of three in a fight that nothing fitted: the branches that fit the game in everything but
        the three cards (each with its own three), stayed put or one step on."""
        want = self._want(gs)
        rest = {k: v for k, v in want.items() if k != "offer"}
        out = []
        for e in self.cands:
            for c in [e] + ([] if e.done else [self._child(e, a) for a in self._one_step_actions(e)]):
                sg = self._sig_for(c, want)
                if fits(sg, rest) and sg.get("offer") is not None:
                    out.append((c, sg["offer"]))
        return out[:MAX_CANDS]

    def _card_taken_back(self, gs: dict, cs) -> bool:
        """a Liquid Memories potion used in a fight (see on_state). The card it takes back from the discard pile may
        still be flying there; its landing resets its cost for the turn, and the flight time depends on frame time
        and an unseeded random. Iron Wave can come back at 1 in one run and at 0 in another with the same moves.
        Card grids (Headbutt, Hologram and the like) do not count: a cost that differs there is a mismatch to look
        at, not timing"""
        lm = sum(1 for q in gs.get("potions", []) if q.get("id") == "LiquidMemories")
        was, self.liquid_memories = getattr(self, "liquid_memories", lm), lm
        return bool(cs) and lm < was

    def _miss_diff(self, gs: dict) -> dict:
        want = self._want(gs)
        return diff(want, sim_signature(self.env, "energy" in want, "screen" in want, "map_xy" in want,
                                        "shop_prices" in want, "rewards" in want, "rest_done" in want, "mk_down" in want,
                                        "select_closed" in want, "select_open" in want))

    def _rule_miss(self, gs: dict, where: str, how: str, what: dict, ok: bool):
        log("rule_miss.jsonl", {"t": time.time(), "floor": gs.get("floor"), "room": gs.get("room_type"),
                                "screen": gs.get("screen_type"), "where": where, "resync": how, "ok": ok,
                                "diff": what})

    def _bot_grid_pick(self, e):
        """The action the bot last sent, if this simulator state is a card grid out of fights and that action is one
        of its picks; else None."""
        if self.prefer is None or e.done or e.screen != "card_select" or (e.state() or {}).get("battle"):
            return None
        if self.prefer == self.grid_pick_given_up:
            return None  # (waited for it long enough: every action is tried again, see _grid_pick_waited)
        if self.prefer not in e.legal_actions():
            return None
        return self.prefer if json.loads(e.action_info_json(self.prefer)).get("type") == "card_select" else None

    GRID_PICK_STATES = 5     # the bot's grid pick is waited for over this many game states in a row that fit nothing,
    GRID_PICK_SECONDS = 5.0  # or this long, whichever comes first (in the recorded game its card took one state, 0.2 s)
    grid_pick_given_up = None  # the pick (action bits) that was waited for and never fitted
    grid_pick_wait = None      # (the pick, states that fitted nothing so far, time of the first)

    def _grid_pick_waited(self, gs: dict, found: bool):
        """Called at the end of match. While some branch is on the bot's grid pick and the game's states fit nothing,
        count them; past the limit give the pick up, so that the next state is matched as before this rule (every
        action, then a resync), and say so in the events."""
        if found:
            self.grid_pick_wait = self.grid_pick_given_up = None
            return
        if not any(self._bot_grid_pick(e) is not None for e in self.cands):
            return  # (also the searches a resync runs on other branches: they neither count nor start the count again)
        w = self.grid_pick_wait
        w = (self.prefer, w[1] + 1, w[2]) if w and w[0] == self.prefer else (self.prefer, 1, time.time())
        self.grid_pick_wait = w
        if w[1] >= self.GRID_PICK_STATES or time.time() - w[2] >= self.GRID_PICK_SECONDS:
            self.grid_pick_given_up, self.grid_pick_wait = self.prefer, None
            log("events.jsonl", {"t": time.time(), "event": "grid_pick_wait_given_up", "floor": gs.get("floor"),
                                 "states": w[1], "seconds": round(time.time() - w[2], 2)})

    def _grid_pick_or_all(self, e):
        """The bot's own pick alone on its card grid out of fights (see _one_step_actions), else every legal action
        in the simulator's order."""
        pick = self._bot_grid_pick(e)
        return [pick] if pick is not None else e.legal_actions()

    def _one_step_actions(self, e):
        """The actions match tries as one step from e, the bot's own first. On a card grid out of fights (Dolly's
        Mirror, Duplicator, a transform or removal) where the bot has sent its pick, only that pick: the game takes
        exactly that card, and the card it gives can join the deck a moment after the game is back on the earlier
        screen. Trying the other picks there took a branch that happened to look like that moment (copying a curse
        that Omamori then negated left the deck as it was) and lost the game when the real card arrived."""
        pick = self._bot_grid_pick(e)
        if pick is not None:
            return [pick]
        return sorted(e.legal_actions(), key=lambda a: a != self.prefer)

    def _bot_pick_pending(self, gs: dict) -> bool:
        """The game is on the state between the bot's grid pick and its card joining the deck: some branch is still
        on the grid with the bot's pick, and after that pick the simulator differs from the game only by cards the
        game's deck does not have yet. Nothing to resync: the next state has the card."""
        want = self._want(gs)
        for e in self.cands:
            pick = self._bot_grid_pick(e)
            if pick is None:
                continue
            c = self._child(e, pick)
            d = diff(want, sim_signature(c, "energy" in want, "screen" in want, "map_xy" in want, "shop_prices" in want,
                                         "rewards" in want, "rest_done" in want, "mk_down" in want,
                                         "select_closed" in want, "select_open" in want))
            if d and set(d) <= {"deck"} and not d["deck"].get("game_only") and d["deck"].get("sim_only"):
                return True
        return False

    OUT_SETTLE_SECONDS = 3.0  # (the game's own card effects run 0.5 to 2 s at normal speed)

    def _resync_out_of_fight(self, gs: dict, ready: bool, at_once: bool = False) -> bool:
        """CM_RESYNC=all, a ready state out of fights the simulator cannot follow. Only once the same state has
        stood for OUT_SETTLE_SECONDS: the game says "ready" while an effect is still playing (a card joining the
        deck: a curse from an event or Cursed Key's chest, Astrolabe's cards), and such a state fits on its own a
        moment later. Without SuperFastMode that moment is 0.5 to 0.7 s (clean-copy games), longer than the bot's
        first short waits, and the rule this replaces ("the same state twice in a row") wrote the floor off.
        Then this floor's autosave plus up to two steps; if that does not fit,
        the next floor's autosave (as after a curse transform) and the game stops until then, as today."""
        key = (gs.get("floor"), json.dumps(self._want(gs), sort_keys=True, default=str))
        now = self.now if self.now is not None else time.time()
        if not at_once:
            if self.out_miss != key:
                self.out_miss, self.out_miss_since = key, now
            if now - self.out_miss_since < self.OUT_SETTLE_SECONDS:
                return False
        token = (id(self.latest_save), gs.get("floor"))
        if token == getattr(self, "failed_token", None):
            return False  # this save did not fit already: wait for the next one (one rule_miss line, not one a state)
        what = self._miss_diff(gs)
        ok = self.resync_from_save(gs, ready, same_floor=True)
        if not ok:
            self.failed_token = token
        self._rule_miss(gs, "fight the simulator is not in" if at_once else "out of fights", "this floor's autosave",
                        what, ok)
        if not ok and isinstance(gs.get("floor"), int) and self.card_rng_drift_floor is None:
            self.card_rng_drift_floor = gs["floor"]  # the next floor's autosave rebuilds it (resync_from_save)
        return ok

    @staticmethod
    def _child(e, a):
        c = e.clone()
        c.step(a)
        return c

    def on_state(self, msg: dict):
        self._on_state(msg)
        self._take_game_cards(msg)

    def _take_game_cards(self, msg: dict):
        """A Discovery-style choice in a fight (Discovery, the Attack / Skill / Power / Colorless potions, Toolbox,
        Nilry's Codex): the game draws its three cards again every frame, so the simulator's three are another set
        (rng_drift below). Give each branch on that choice the game's three cards before the system picks, so both
        take the same card (otherwise index 0 could be Strike in the simulator and Offering in the game)"""
        gs = msg.get("game_state") or {}
        if not (msg.get("ready_for_command") and self.synced and gs.get("combat_state")
                and str(gs.get("screen_type")).upper() == "CARD_REWARD" and gs.get("room_phase") == "COMBAT"):
            return
        ids = [c.get("id") for c in (gs.get("screen_state") or {}).get("cards") or []]
        for e in self.cands or ([self.env] if self.env is not None else []):
            b = e.state().get("battle") or {}
            if b.get("card_select_task") in ("DISCOVERY", "CODEX"):
                bad = e.set_select_cards(ids)
                if bad and self.game_cards_logged != (gs.get("floor"), tuple(ids)):
                    self.game_cards_logged = (gs.get("floor"), tuple(ids))
                    log("events.jsonl", {"t": time.time(), "event": "game_cards_not_set", "floor": gs.get("floor"),
                                         "cards": ids, "why": bad})

    game_cards_logged = None

    def _on_state(self, msg: dict):
        gs = msg.get("game_state")
        if not msg.get("in_game") or not gs:
            return
        if str(gs.get("class", "")).upper() != "IRONCLAD":
            if self.synced or self.env is None:
                advise("Only Ironclad is supported: no advice for this run.")
            self.synced, self.env = False, None
            return
        if gs.get("floor") == 0 and (self.env is None or self.seed != (int(gs["seed"]) & (2**64 - 1))):
            self.new_run(gs)
        if self.env is None:
            advise("This run did not start from the beginning (loaded save): the simulator cannot follow; start a new run.")
            self.synced = False
            return
        cs = gs.get("combat_state")
        played = self.played.update(gs) if cs else {}
        toolbox = fight_start_choice(gs)
        codex_choice = False
        drift_before = self.rng_drift_floor == gs.get("floor")  # (before this state's own choice marks it)
        if cs and str(gs.get("screen_type")).upper() == "CARD_REWARD" and gs.get("room_phase") == "COMBAT" and toolbox:
            # Toolbox's choice (at battle start before the first draw: turn 1, nothing in hand or in the discard and
            # exhaust piles) draws its three cards once, so it is no drift. As drift it would make every fight of a
            # Toolbox run resync silently on a mismatch, which would hide simulator misses
            pass
        elif cs and str(gs.get("screen_type")).upper() == "CARD_REWARD" and gs.get("room_phase") == "COMBAT":
            # Discovery (and the Attack / Skill / Power / Colorless potions) draws its three cards again on every
            # frame until its duration has run: its own offer comes from the first frame, but the card random stream
            # is off by a frame-dependent count for the rest of the floor (the next room reseeds it). Two runs with
            # the same moves differ there. Nilry's Codex draws its three cards once (CodexAction generates them in
            # its first frame only; three real games had the simulator's cards, spot and next hand), so on a floor
            # that has not drifted yet its choice is no drift: whether this choice is the Codex's is decided after
            # the match below, from the simulator (codex_choice). On a floor already drifted (a Discovery before it)
            # the Codex's offer comes from the drifted stream and nothing changes
            if self.rng_drift_floor != gs.get("floor") and any(r.get("id") == "Nilry's Codex" for r in gs.get("relics", [])):
                codex_choice = True
            else:
                self._mark_choice_drift(gs)
        elif not cs:
            self.rng_drift_floor = None
        if self._card_taken_back(gs, cs):
            # a card taken back from the discard pile in a fight (Liquid Memories, Hologram): the game puts a played
            # card in the discard pile at once, but its flight there is an animation that resets the card's cost for
            # the turn when it lands. Taken back before it lands, the card keeps its cost reduction until then and
            # loses it after (Iron Wave back at 0, then 1). Its cost now depends on
            # timing, so the rest of the fight is resynced from the game on a mismatch, as after a Discovery
            if self.rng_drift_floor != gs.get("floor"):
                log("events.jsonl", {"t": time.time(), "event": "rng_drift", "floor": gs.get("floor"),
                                     "why": "card taken back from the discard pile (its cost depends on timing)"})
            self.rng_drift_floor = gs.get("floor")
        sv = self.latest_save
        if sv is not None and sv is not getattr(self, "pace_save", None) and "play_time" in sv \
                and (int(sv.get("seed", -1)) & (2**64 - 1)) == self.seed:
            # Secret Portal is offered only after 800 s of real play (the game's playtime); a new simulator run
            # assumes human pace. The bot plays act 3 within 9 minutes, so its act 3 event rolls differed (a Lab
            # in the simulator, a Duplicator in the game). Each room's autosave carries the time
            self.pace_save = sv
            pace = float(sv["play_time"]) < 800
            for c in self.cands:
                if hasattr(c, "speedrun_pace"):
                    c.speedrun_pace = pace
        curses = Counter(c.get("id") for c in gs.get("deck", []) if str(c.get("type", "")).upper() == "CURSE")
        ss = gs.get("screen_state") or {}
        if not cs and str(gs.get("screen_type", "")).upper() == "GRID" and ss.get("for_transform") and ss.get("confirm_up"):
            self.transform_curses = (gs.get("floor"), curses)  # the curses before the transform
        elif getattr(self, "transform_curses", None) and str(gs.get("screen_type", "")).upper() != "GRID":
            fl, before = self.transform_curses
            self.transform_curses = None
            if before - curses and fl == gs.get("floor"):  # a curse left the deck: it was the one transformed
                log("events.jsonl", {"t": time.time(), "event": "card_rng_drift", "floor": fl,
                                     "why": "a curse transformed (the confirm screen's preview draws from the card stream)"})
                self.card_rng_drift_floor = fl
        ready = bool(msg.get("ready_for_command"))
        if self.card_rng_drift_floor is not None and self.resync_from_save(gs, ready):
            ok = True
        else:
            ok = self.match(gs, ready)
        if codex_choice and not (ok and self.env is not None
                                 and (self.env.state().get("battle") or {}).get("card_select_task") == "CODEX"):
            # a choice of three on a Codex owner's floor that the simulator does not have as the Codex's: one of the
            # redrawing ones (or nothing fitted): drift, as before
            self._mark_choice_drift(gs)
        others = []
        if not ok and cs and ready and "offer" in self._want(gs):
            others = self._offer_differs(gs)
            if others:
                # only the choice's three cards differ. On a floor already drifted the search below finds the
                # stream's place from them. On a floor not drifted before this choice (Toolbox, the Codex, the
                # floor's first Discovery: each drawn once from a stream that should be exact) no count of extra
                # draws can be the reason: a miss of the simulator. It is logged as one, not searched; the branch
                # goes on with the game's three (_take_game_cards) and the floor counts as drifted from here
                game_offer = self._want(gs)["offer"]
                log("events.jsonl", {"t": time.time(), "event": "offer_differs", "floor": gs.get("floor"),
                                     "turn": cs.get("turn"), "drifted_before": drift_before,
                                     "game": game_offer, "sim": others[0][1]})
                if not drift_before:
                    self.cands = [c for c, _ in others]
                    self.env = self.cands[0]
                    self.paths = {}
                    ok = True
                    self._rule_miss(gs, "fight", "the game's three cards", {"offer": {"game": game_offer, "sim": others[0][1]}}, True)
                    if self.rng_drift_floor != gs.get("floor"):
                        log("events.jsonl", {"t": time.time(), "event": "rng_drift", "floor": gs.get("floor"),
                                             "why": "a choice's three cards differed on a floor not drifted (logged "
                                                    "as a miss): where the card random stream is, is not known"})
                    self.rng_drift_floor = gs.get("floor")
        drift = self.rng_drift_floor == gs.get("floor")
        costs_differ = False
        if ok and cs and ready:
            # hand costs on every ready fight state: the signature compares card
            # names only, so a fight could "match" with other costs. A drifted card stream rolls Snecko Eye's costs,
            # and a rule slip shows here first (e.g. Corruption before Mummified Hand: Headbutt 0 in the
            # game, 1 in the simulator). Costs that differ are a mismatch
            sh = (self.env.state().get("battle") or {}).get("hand") or []
            gc = [max(int(c.get("cost", -1)), -1) for c in cs.get("hand", [])]
            costs_differ = len(sh) == len(gc) and [max(int(c[2]), -1) for c in sh] != gc
            if costs_differ:
                # the first branch only: the others fit the game's cards too, and one of them may have its costs (two
                # copies of a card with different costs for the turn and a pick that brings one back: real game
                # 5600343, floor 51, 512 branches, half of them right). Keep those; only if none has them is it a
                # mismatch
                right = [c for c in self.cands if self._sim_costs(c) == gc]
                if right:
                    self.cands, self.env, costs_differ = right, right[0], False
                else:
                    ok = False
        in_fight = bool(cs) and self.env is not None and bool(self.env.state().get("battle"))
        if not ok and cs and ready and not in_fight and self.resync_all and self.env is not None and not self.env.done \
                and self.env.screen == "event" and self.env.state().get("floor") == gs.get("floor"):
            # (only from an event on the game's floor: from the map, the first state of a new fight often does not fit
            # for a moment and the next one does; taking a map step there picked any node and broke the run)
            # the game is in a fight the simulator has not entered: its step into it did not fit (e.g. the second
            # Colosseum fight's opening hand, one card off). Take the step that enters a fight on this
            # floor and resync that fight from the game; the rule miss is logged as usual
            for a in self.env.legal_actions():
                c = self._child(self.env, a)
                if c.state().get("battle") and c.state().get("floor") == gs.get("floor"):
                    info = json.loads(self.env.action_info_json(a))
                    self.cands, self.env, in_fight = [c], c, True
                    log("events.jsonl", {"t": time.time(), "event": "entered_fight_for_resync", "floor": gs.get("floor"),
                                         "action": info})
                    break
        if not ok and cs and ready and in_fight and drift and self._rng_search(gs):
            ok = True
        elif not ok and cs and ready and in_fight and drift and others:
            # no count of extra draws gives the game's three cards, and nothing else differs: the branch goes on with
            # the game's three (_take_game_cards), as it did before the three were compared; the fight is not rebuilt
            self.cands = [c for c, _ in others]
            self.env = self.cands[0]
            self.paths = {}
            ok = True
            log("events.jsonl", {"t": time.time(), "event": "offer_taken_from_game", "floor": gs.get("floor"),
                                 "turn": cs.get("turn")})
        if not ok and cs and ready and in_fight and (drift or self.resync_all):
            what = {"hand_costs": "differ"} if costs_differ else self._miss_diff(gs)
            try:
                ok = self.resync_battle(gs, played)
            except Exception as ex:
                # the simulator refused the game's state. Left to the bridge's main loop this was only logged, the
                # lockstep still counted as in step and the bot played on from the state before the miss (the fake
                # game's stress run: three of 25 games stopped on a card the game no longer held). Now: logged, then
                # the usual fallback, this floor's autosave; if that does not fit either, out of step.
                # card_rng_drift_floor then asks for the rebuild from the next floor's autosave. Its name is from its
                # first use (a curse transform leaves the card random stream off), but what it holds is "from this
                # floor on the simulator cannot be trusted: rebuild it from the first autosave of a later floor"
                # (resync_from_save), and _resync_out_of_fight already sets it for a miss out of fights. The same
                # meaning here, so no second field
                log("errors.jsonl", {"t": time.time(), "error": repr(ex), "where": "resync_battle"})
                log("events.jsonl", {"t": time.time(), "event": "resync_from_game", "floor": gs.get("floor"),
                                     "turn": cs.get("turn"), "ok": False, "error": repr(ex)[:200]})
                ok = self.resync_from_save(gs, ready, same_floor=True)
                if not ok and isinstance(gs.get("floor"), int) and self.card_rng_drift_floor is None:
                    self.card_rng_drift_floor = gs["floor"]
            if not drift:
                self._rule_miss(gs, "fight", "game state", what, ok)
        elif not ok and ready and self.resync_all and self.env is not None and not in_fight \
                and str(gs.get("screen_type", "")).upper() == "COMPLETE" and self.env.state().get("battle"):
            # the game has won the fight (COMPLETE) and the simulator is still in it: no step of the simulator fits.
            # Proceed (the bot sends it, blind_proceed) and rebuild from the next room's autosave
            if getattr(self, "blind_proceed", None) != gs.get("floor"):
                self._rule_miss(gs, "fight won in the game only", "proceed, then the next floor's autosave",
                                self._miss_diff(gs), True)
                self.blind_proceed = gs.get("floor")
                if isinstance(gs.get("floor"), int):
                    self.card_rng_drift_floor = gs["floor"]
        elif not ok and ready and self.resync_all and self.env is not None and not in_fight \
                and not self._bot_pick_pending(gs):
            # (also a fight the simulator is not in: a dead branch that fitted the COMPLETE screen leaves the next fight
            # with nothing to resync; that floor's autosave has the fight)
            ok = self._resync_out_of_fight(gs, ready, at_once=bool(cs) and gs.get("room_phase") == "COMBAT")
        if ok:
            self.out_miss = None
            if cs and ready and str(gs.get("screen_type", "")).upper() == "NONE" and gs.get("room_phase") == "COMBAT":
                self.last_ready_fight = (gs, dict(played))  # (resync_battle: replay into an open choice)
        if ok and not self.synced:
            advise("In sync again.")
        if not ok:
            if self.synced:
                advise("The simulator and the game no longer match; the difference is logged; no advice for now.")
            self.synced = False
            return
        self.synced = True
        if self.env.done or self.system is None or len(self.env.legal_actions()) < 2:
            return
        c, p, label, how = self.system.decide(self.env, self.seed)
        advise(f"Floor {gs.get('floor')} -> {label} (confidence {100 * p:.0f}%, {how})")


class AutosaveSnapshots:
    """Keep a decoded copy of the game's autosave each time it changes (the game rewrites it at every room entry), so
    out-of-battle positions can be rebuilt from it (Env from a save) and checked offline."""

    def __init__(self):
        self.mtime = None
        self.n = 0
        self.latest = None
        try:
            from . import autosave
        except ImportError:  # an older installed package: no snapshots
            self.autosave = None
            return
        self.autosave = autosave
        # CM_SAVES_DIR: the fake game points it at an empty folder, so a fake run never reads the real game's autosave
        # The saves are read from the game's own folder (CFG.SAVES_DIR)
        saves = CFG.SAVES_DIR
        self.path = saves / "IRONCLAD.autosave"

    def poll(self, t_state: float):
        if self.autosave is None:
            return
        try:
            mt = self.path.stat().st_mtime
        except OSError:
            return
        if mt == self.mtime:
            return
        self.mtime = mt
        try:
            d = self.autosave.decode(self.path.read_bytes())
        except Exception as ex:  # half-written file: try at the next state
            self.mtime = None
            log("errors.jsonl", {"t": time.time(), "error": f"autosave: {ex!r}"})
            return
        self.n += 1
        self.latest = d
        log("autosaves.jsonl", {"t": time.time(), "t_state": t_state, "mtime": mt, "save": d})


class Probe:
    """The simplest auto player, the base of Auto. It starts one run with
    `start ironclad ASC SEED` and then sends, from a fixed-seed RNG, one command the state says is available. It stops
    (sends nothing, logs why) on anything it does not recognize, on 5 errors in a row, on a state repeated 30 times,
    after MAX_COMMANDS commands, or when the run is over."""

    SEED = CFG.seed_string()  # the game seed string of the run this bridge starts
    MAX_COMMANDS = 4000

    def __init__(self):
        self.started = False
        self.stopped = None
        self.errors = 0
        self.sent = 0
        self.repeat = Counter()
        self.last = None
        self.tried = {}

    def stop(self, why: str, msg: dict):
        if getattr(self, "pause_tamper", False) and why != RUN_OVER:
            # the game no longer fitted the simulator when the player resumed the bot: say that, keep the cause
            log("probe.jsonl", {"t": time.time(), "stop_cause_after_pause": why})
            why = M.Msg("R11")
        self.stopped = why
        log("probe.jsonl", {"t": time.time(), "stop": why, "available": msg.get("available_commands")})
        advise(f"Auto play stopped: {why}")
        if why == RUN_OVER:
            gs = (self.last or {}).get("game_state") or {}
            won = bool(self.lock.env is not None and self.lock.env.done and self.lock.env.heart_won) if hasattr(self, "lock") else False
            panel_show("S_FINISHED", lines=[M.text("WON", PANEL.lang) if won else
                                            M.text("LOST", PANEL.lang, n=getattr(self, "last_floor", gs.get("floor", "?")))] if PANEL else [])
        else:
            panel_show("S_STOPPED", lines=[M.shown(why, PANEL.lang)] if PANEL else [])

    def next(self, msg: dict):
        if self.stopped:
            return None
        if "error" in msg:
            self.errors += 1
            log("probe.jsonl", {"t": time.time(), "error": msg["error"]})
            if self.errors >= 5:
                self.stop(M.Msg("R01"), msg)
            # an error message carries no game state: pick again on the last state, avoiding what failed
            return None if (self.errors >= 5 or self.last is None) else self._pick(self.last, after_error=True)
        self.errors = 0
        self.last = msg
        if msg.get("in_game") and (msg.get("game_state") or {}).get("floor") is not None:
            self.last_floor = msg["game_state"]["floor"]
        return self._pick(msg)

    def _pick(self, msg: dict, after_error: bool = False):
        if not msg.get("ready_for_command", True):
            return None
        av = [a.lower() for a in msg.get("available_commands", [])]
        if not msg.get("in_game"):
            if self.started:
                self.stop(RUN_OVER, msg)
                return None
            if "start" in av:
                self.started = True
                return f"start ironclad {CFG.ASCENSION} {self.SEED}"
            self.stop(M.Msg("R02"), msg)
            return None
        gs = msg.get("game_state") or {}
        # the whole state: a key cut short would leave out the potions, which sort after the deck and the map, and
        # states that differ only in the potions would count as one
        key = hashlib.sha1(json.dumps(gs, sort_keys=True).encode()).hexdigest()
        if (gs.get("screen_state") or {}).get("event_id") == "Match and Keep!":
            key += str(self.sent)  # after a failed pair the grid looks the same again: not a loop
        self.repeat[key] += 1
        if self.repeat[key] > 30:
            self.stop(M.Msg("R03"), msg)
            return None
        if self.sent >= self.MAX_COMMANDS:
            self.stop(M.Msg("R04", n=self.MAX_COMMANDS), msg)
            return None
        tried = self.tried.setdefault(key, Counter())
        options = self._choose(av, gs, after_error)
        for cmd in options:
            if tried[cmd] < 2:  # the same command twice on the same state without a change: try another one
                tried[cmd] += 1
                return cmd
        if options and "wait" in av and tried["wait"] < 5:
            # the game can repeat a ready state while it changes screens (an event fading into its fight): wait first
            tried["wait"] += 1
            return f"wait {60 * tried['wait']}"
        if options:
            self.stop(M.Msg("R05", options=options[:6]), msg)
        else:
            self.stop(getattr(self, "stop_reason", None) or M.Msg("R06", screen=gs.get("screen_type"), av=av), msg)
        return None

    def _choose(self, av, gs, after_error):
        """Commands to try on this state, best first (Auto)."""
        raise NotImplementedError


class Auto(Probe):
    """The network plays the real game. Start, waits, repeats and stops are the probe's; inside a run every command
    comes from the network's decision on the lockstep simulator (see auto.py)."""

    def __init__(self, lock, system):
        super().__init__()
        from . import auto as cm_auto
        self.lock, self.system, self.translate = lock, system, cm_auto.translate
        self.add_target_rank = cm_auto.add_target_rank
        self.add_choice_context = cm_auto.add_choice_context
        # CM_WAITS=fixed: fixed waits. Default: short waits, each twice the last (10, 20, 40, ...
        # frames), until the game shows what the wait was for, never longer in total than the fixed one. At 5x game
        # speed the fixed waits take a large share of the play time
        self.fixed_waits = os.environ.get("CM_WAITS", "") == "fixed"
        self.waited = Counter()  # frames waited so far, per kind of wait

    def _panel_top(self, e, acts, gs, av):
        """Show the decision just taken on the panel: the network's three best options with their shares, named as
        the game names them. Reads only (the simulator, the scores already computed); any failure is logged and
        ignored."""
        try:
            if PANEL is None or not P.ENABLED:
                return
            f, lines, named, cmds = self.system.last_f, [], {}, {}

            def name(i):
                if i not in named:
                    info = self.add_target_rank(json.loads(e.action_info_json(int(f["actions"][i]))), e)
                    info = self.add_choice_context(info, e, acts, gs)
                    cmd, _ = self.translate(info, self.last or {"game_state": gs, "available_commands": av})
                    named[i], cmds[i] = P.option_label(cmd, gs, PANEL.lang, info) or self.system.label(e, f, i), cmd
                return named[i]
            v = getattr(self.system, "last_viz", None)
            # every option shown gets its name first, so the ones still alike can be numbered the same way everywhere
            shown = list(dict.fromkeys([i for i, _ in self.system.last_top]
                                       + ([i for i, _ in v["top"]] if P.VIZ and v is not None else [])))
            named.update(zip(shown, P.distinct([name(i) for i in shown])))
            for k, (i, pr) in enumerate(self.system.last_top, 1):
                lines.append(f"{k}. {name(i)}   {100 * pr:.0f}%")
            if self.system.last_restricted and lines:
                lines[0] += " " + M.text("KEY_RULE", PANEL.lang)
            if P.VIZ and v is not None:
                rows = [(name(i), q) for i, q in v["top"]]
                chosen = next(k for k, (i, _) in enumerate(v["top"]) if i == v["chosen"])
                if v["how"] == "key rule":
                    rows[chosen] = (rows[chosen][0] + " " + M.text("KEY_RULE", PANEL.lang), rows[chosen][1])
                PANEL.think(rows, chosen, v["pmax"], v["n"], v["ms"], getattr(self.system, "params", None),
                            v["value"], gs.get("floor"), bits=int(f["actions"][v["chosen"]]), cmd=cmds.get(v["chosen"]))
            PANEL.show("S_PLAYING", gs.get("floor"), lines)
        except Exception as ex:
            log("errors.jsonl", {"t": time.time(), "error": f"panel: {ex!r}"})

    def _short_wait(self, kind: str, cap: int):
        """the next short wait of this kind, or None once the fixed wait's total (cap frames) is used up"""
        left = cap - self.waited[kind]
        if left <= 0:
            return None
        n = min(left, max(10, self.waited[kind]))  # 10, 10, 20, 40, 80, ...: the total doubles each time
        self.waited[kind] += n
        return f"wait {n}"

    # frames waited in all for intents that do not show (5 s at 60 frames a second), and the one wait with Runic Dome
    INTENT_WAIT_CAP, DOME_WAIT = 300, 30

    def _intent_wait(self, gs: dict, av: list):
        """A wait command while the monsters' intents do not exist yet, else None.

        Without SuperFastMode the game can call a fight's first state ready before it has created the intents (the
        state says intent DEBUG; seen on turn 1 only, in 67 fights of seven clean-copy games). What a card does can
        depend on them: Spot Weakness gives its Strength only if the target's intent damage is set, and played on
        that state it gave none (a clean-copy game, floors 20 and 28), while the simulator gave 3. So nothing is sent
        until every living monster has an intent: short waits (5, 5, 10, 20, ... frames), INTENT_WAIT_CAP frames in
        all, then the bot plays on and the event intents_not_shown is logged.
        With Runic Dome the game state says NONE for every intent, always, so there is nothing to see: on a fight's
        first state (turn 1, nothing discarded or exhausted) one wait of DOME_WAIT frames."""
        cs = gs.get("combat_state")
        if not cs or "wait" not in av or gs.get("room_phase") != "COMBAT":
            self.intent_frames = self.dome_waited = None
            return None
        living = [m for m in cs.get("monsters", []) if not m.get("is_gone") and m.get("current_hp", 0) > 0]
        key = (gs.get("floor"), cs.get("turn"))
        if any(m.get("intent") == "DEBUG" for m in living):
            if getattr(self, "intent_frames", None) is None or self.intent_frames[0] != key:
                self.intent_frames = [key, 0, False]
            left = self.INTENT_WAIT_CAP - self.intent_frames[1]
            if left > 0:
                n = min(left, max(5, self.intent_frames[1]))
                self.intent_frames[1] += n
                log("auto.jsonl", {"t": time.time(), "floor": gs.get("floor"), "screen": gs.get("screen_type"),
                                   "cmd": f"wait {n}", "note": "the monsters' intents are not there yet"})
                return f"wait {n}"
            if not self.intent_frames[2]:
                self.intent_frames[2] = True
                log("events.jsonl", {"t": time.time(), "event": "intents_not_shown", "floor": gs.get("floor"),
                                     "turn": cs.get("turn"), "frames": self.intent_frames[1],
                                     "monsters": [m.get("id") for m in living if m.get("intent") == "DEBUG"]})
            return None
        self.intent_frames = None
        if any(_norm(str(r.get("id") or r.get("name") or "")) == "runicdome" for r in gs.get("relics", [])) \
                and cs.get("turn") == 1 \
                and not cs.get("discard_pile") and not cs.get("exhaust_pile") \
                and getattr(self, "dome_waited", None) != gs.get("floor"):
            self.dome_waited = gs.get("floor")
            log("auto.jsonl", {"t": time.time(), "floor": gs.get("floor"), "screen": gs.get("screen_type"),
                               "cmd": f"wait {self.DOME_WAIT}", "note": "Runic Dome: the intents cannot be seen"})
            return f"wait {self.DOME_WAIT}"
        return None

    def _arrived(self) -> bool:
        """after a pick whose effect shows late (a card joining the deck): every branch the lockstep keeps has taken
        the bot's action and fits the game. Before that, a branch that had not taken it fitted the game's
        unchanged state, and the bot would have decided again on it"""
        lock = self.lock
        return bool(lock.synced and lock.cands and lock.env is not None
                    and all(c.steps > getattr(self, "sent_steps", -1) for c in lock.cands))

    def _choose(self, av, gs, after_error):
        if getattr(self, "after_neow", False) and "wait" in av:
            if self.fixed_waits:
                self.after_neow = False
                return ["wait 180"]
            w = None if self._arrived() else self._short_wait("pick", 180)
            if w is not None:
                return [w]
            self.after_neow = False
            self.waited["pick"] = 0
        lock = self.lock
        if str(gs.get("screen_type")).upper() == "COMBAT_REWARD" and not (gs.get("screen_state") or {}).get("rewards") \
                and "proceed" in av:
            return ["proceed"]  # nothing to take (Smoke Bomb): the simulator is already past this screen
        ss_ = gs.get("screen_state") or {}
        if ss_.get("event_id") == "Spire Heart" and lock.synced and lock.env is not None and lock.env.done \
                and len(ss_.get("options") or []) == 1 and "choose" in av:
            # without the three keys the run ends at the door: the simulator's run is over, the game shows the
            # Heart's screens up to its death screen
            return [f"choose {ss_['options'][0].get('choice_index', 0)}"]
        if str(gs.get("screen_type")).upper() == "GAME_OVER" and "proceed" in av:
            return ["proceed"]  # death or the Heart: leave the score screen; at the menu the run counts as finished
        if str(gs.get("screen_type")).upper() == "COMPLETE" and "proceed" in av and (
                gs.get("floor") == 50 or getattr(lock, "blind_proceed", None) == gs.get("floor")
                or (lock.synced and lock.env is not None and isinstance(gs.get("floor"), int)
                    and lock.env.state()["floor"] > gs["floor"])
                or (lock.synced and lock.env is not None and lock.env.done and lock.env.heart_won)):
            # A20 act 3: on to the second boss (the simulator enters it at once); after the last boss on to the door
            # (the simulator is already on act 4's map); or a fight won in the game only (blind_proceed);
            # or the Heart beaten: the simulator's run is over, the game's goes on to the victory room
            return ["proceed"]
        if lock.synced:
            self.unsynced_waits = 0
            self.waited["unsynced"] = 0
        elif lock.env is not None and not lock.env.done and "wait" in av and not self.fixed_waits:
            # (short waits, at most the fixed ones' 60+120+...+360 frames in total)
            w = self._short_wait("unsynced", 1260)
            if w is not None:
                return [w]
        elif lock.env is not None and not lock.env.done and getattr(self, "unsynced_waits", 0) < 6 and "wait" in av:
            # the game can say "ready" while an effect is still playing (a card joining the deck after an event or a
            # shop buy, Smoke Bomb's escape): wait a little and look again before giving up
            self.unsynced_waits = getattr(self, "unsynced_waits", 0) + 1
            return [f"wait {60 * self.unsynced_waits}"]
        if not lock.synced or lock.env is None or lock.env.done:
            self.stop_reason = M.Msg("R07")
            return []
        esc = getattr(lock, "escaping", None)
        if esc is not None and gs.get("floor") == esc[0] and gs.get("combat_state") \
                and not lock.env.state().get("battle") and "wait" in av:
            # Smoke Bomb's escape still playing in the game (the simulator has left the fight, see match): nothing to
            # do until the game leaves it too; the simulator's next action is on the reward screen or the map
            self.escape_waits = getattr(self, "escape_waits", 0) + 1
            if self.escape_waits <= 20:
                log("auto.jsonl", {"t": time.time(), "floor": gs.get("floor"), "screen": gs.get("screen_type"),
                                   "cmd": "wait 30", "note": "Smoke Bomb's escape playing"})
                return ["wait 30"]
        else:
            self.escape_waits = 0
        w = self._intent_wait(gs, av)
        if w is not None:
            return [w]
        b = lock.env.state().get("battle") or {}
        if str(gs.get("screen_type")).upper() == "HAND_SELECT" and "confirm" in av and b and not b.get("card_select"):
            # the simulator closed a multi-pick hand screen by itself (hand empty after the picks, or the pick limit);
            # the game's screen only closes on its confirm button
            log("auto.jsonl", {"t": time.time(), "floor": gs.get("floor"), "screen": gs.get("screen_type"),
                               "cmd": "confirm", "note": "the simulator's screen closed by itself"})
            return ["confirm"]
        if calling_bell_grid(gs) and "confirm" in av and lock.env.screen != "card_select":
            # the simulator is already on the three relics: the game's curse grid closes on confirm
            log("auto.jsonl", {"t": time.time(), "floor": gs.get("floor"), "screen": gs.get("screen_type"),
                               "cmd": "confirm", "note": "Calling Bell's curse (the simulator has it already)"})
            return ["confirm"]
        e = lock.env
        acts = e.legal_actions()
        take = getattr(self, "swap_take", None)
        self.swap_take = None
        if take is not None and take in acts:
            bits, how = take, "potion swap: the pick after the discard"
        elif len(acts) == 1:
            bits = acts[0]
            how = "only action"
            if PANEL is not None and P.ENABLED:
                self.system.last_f, self.system.last_top, self.system.last_restricted = e.features(), [(0, 1.0)], False
                v = getattr(self.system, "last_viz", None)  # (the curve keeps the last value the network gave)
                self.system.last_viz = {"top": [(0, 1.0)], "chosen": 0, "pmax": 1.0, "n": 1, "ms": 0.0,
                                        "value": None, "how": how}
                self._panel_top(e, acts, gs, av)
                self.system.last_viz = v
        else:
            c, p, label, how = self.system.decide(e, lock.seed)
            fs = getattr(self.system, "search", None)
            if fs is not None and fs.last is not None:  # a fair search ran (elite or boss fight): what and how long
                log("events.jsonl", {"t": time.time(), "event": "search", "floor": gs.get("floor"),
                                     "room": e.state().get("room"), **fs.last})
            self._panel_top(e, acts, gs, av)
            bits = int(e.features()["actions"][c])
        info = self.add_target_rank(json.loads(e.action_info_json(bits)), e)
        info = self.add_choice_context(info, e, acts, gs)
        if info.get("match_and_keep"):
            # the game lists the face-down cards in grid order: the pick's place among the positions that can
            # still be flipped is its option index
            down = sorted(json.loads(e.action_info_json(a)).get("grid_idx", -1) for a in acts)
            info["mk_rank"] = down.index(info["grid_idx"])
        if info.get("swap_slot") is not None:
            # a full-belt potion swap: in the game, discard the held potion, then pick (next command).
            # The simulator branches take the same discard now (an out-of-combat potion discard), so they match the
            # game's next state; the pick is then the plain action (idx2 cleared), legal once the slot is free.
            k = int(info["swap_slot"])
            disc = 0xC0000000 | k
            for cand in lock.cands:
                cand.step(disc)
            lock.env = lock.cands[0]
            self.swap_take = bits & ~0xFF00
            log("auto.jsonl", {"t": time.time(), "floor": gs.get("floor"), "screen": gs.get("screen_type"), "info": info,
                               "cmd": f"potion discard {k}", "note": "potion swap: discard first", "how": how})
            return [f"potion discard {k}"]
        lock.prefer = bits
        if info.get("type") == "select" and b.get("card_select_task") == "EXHUME" and info.get("select") is not None:
            # Exhume's grid in the game leaves the other Exhumes out of its list; the simulator numbers the whole
            # exhaust pile and skips them. The game's index is the simulator's less the Exhumes before it
            ex = next((l for l in e.text().splitlines() if "exhaustPile" in l), "")
            cards = re.findall(r"\(([^,()]+),", ex.split("{", 1)[-1])
            info["select"] -= sum(1 for c in cards[:info["select"]] if c.rstrip("+") == "Exhume")
        cmd, note = self.translate(info, self.last or {"game_state": gs, "available_commands": av})
        if note == "open the shop":
            # once per shop: a second "choose 0" sent before the shop screen shows lands on its first item (card
            # removal)
            if getattr(self, "shop_opened", None) == gs.get("floor"):
                return ["wait 30"]
            self.shop_opened = gs.get("floor")
        log("auto.jsonl", {"t": time.time(), "floor": gs.get("floor"), "screen": gs.get("screen_type"), "info": info,
                           "cmd": cmd, "note": note, "how": how})
        if cmd is None and "not playable in the game hand" in str(note) and "wait" in av and not self.fixed_waits:
            w = self._short_wait("draw", 630)  # (at most the fixed ones' 30+60+...+180 frames)
            if w is not None:
                return [w]
        self.waited["draw"] = 0
        if cmd is None and "not playable in the game hand" in str(note) and "wait" in av \
                and getattr(self, "draw_waits", 0) < 6 and self.fixed_waits:
            # the game said ready in the middle of a draw (the lockstep accepts it): the card is still on its way
            self.draw_waits = getattr(self, "draw_waits", 0) + 1
            return [f"wait {30 * self.draw_waits}"]
        self.draw_waits = 0
        if cmd is None and note == auto_already_marked() and "wait" in av:
            # the card this pick names is already marked in the game (sending the pick again would unmark it): the
            # game is ahead of what the lockstep has followed; look again in a moment
            log("auto.jsonl", {"t": time.time(), "floor": gs.get("floor"), "screen": gs.get("screen_type"),
                               "cmd": "wait 30", "note": "the pick is already marked in the game"})
            return ["wait 30"]
        if cmd is None:
            self.stop_reason = M.Msg("R08", note=note)
            return []
        if info.get("type") in ("neow", "shop_card") or cmd == "confirm" or \
                (info.get("type") == "reward_card" and gs.get("screen_type") == "CARD_REWARD") or \
                (info.get("type") == "card_select" and gs.get("screen_type") == "GRID" and not gs.get("combat_state")):
            # (a grid pick outside a fight too: Duplicator's copy joins the deck after its show-card effect)
            # a card obtained or upgraded this way changes the deck only after a 2 s effect: wait before deciding again
            self.after_neow = True
            self.sent_steps = e.steps  # (the branches that took this action have more steps: _arrived)
        return [cmd]


class DuplicateReplies:
    """A \"state\" poll gets the current state as its reply; when the game's own update arrives at the same moment the
    two are identical, and answering both sent the command twice ("play 5" twice, the second out of
    bounds; a second valid play would have played a card the network did not choose). A ready state identical to the
    one just answered, arriving within WINDOW seconds while a poll is unanswered, is that poll's reply: skip it.
    Later identical states (a refused command, the next poll 5 s on) are answered as before."""
    WINDOW = 0.5

    def __init__(self):
        self.polls = 0
        self.last_key, self.last_t = None, 0.0

    @staticmethod
    def _key(msg: dict):
        return json.dumps(msg.get("game_state"), sort_keys=True) if msg.get("in_game") else None

    def polled(self):
        self.polls += 1

    def skip(self, msg: dict, t: float) -> bool:
        if not self.polls:
            return False
        if not msg.get("ready_for_command", True) or "error" in msg:
            self.polls -= 1  # a reply that is not ready (the main loop keeps asking)
            return False
        if self.last_key is not None and self._key(msg) == self.last_key and t - self.last_t < self.WINDOW:
            self.polls -= 1
            return True
        return False

    def answered(self, msg: dict, t: float):
        self.last_key, self.last_t = self._key(msg), t

    def waited(self):
        # The reply to "wait N" may equal the state just answered; it is the game's answer, not a duplicate.
        # Polls sent before are answered by then (a poll's reply comes within a frame)
        self.polls = 0
        self.last_key = None


def close_own_game():
    """Stop the game that started this bridge (batch runs only, CFG.CLOSE_GAME): our parent process, if it is a java."""
    if os.name != "posix":
        return
    pid = os.getppid()
    for _ in range(6):
        if pid <= 1:
            return
        out = subprocess.run(["ps", "-o", "ppid=,command=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        if not out:
            return
        ppid, cmd = out.split(None, 1)
        if "ModTheSpire.jar" in cmd and "java" in cmd:
            os.kill(pid, signal.SIGTERM)
            return
        pid = int(ppid)


def stdin_lines():
    """The game's lines as a queue, read on a thread (a pipe cannot be polled with select on Windows); None at the end."""
    import queue
    import threading
    q = queue.Queue()

    def put(raw: bytes):
        # bytes, decoded here: the game writes UTF-8 on some systems and the system's code page on others (names
        # in the game's language). Ids are ASCII, so a name that cannot be decoded must not stop the bridge
        try:
            q.put(raw.decode("utf-8"))
        except UnicodeDecodeError:
            q.put(raw.decode("mbcs" if os.name == "nt" else "latin-1", errors="replace"))

    def pump():
        # The file descriptor itself, split into lines here, not sys.stdin.buffer: main() returns when the
        # run is over while this thread still waits for the game's next line, and a thread waiting inside
        # sys.stdin.buffer holds that BufferedReader's lock, so the interpreter's shutdown cannot close stdin:
        # "Fatal Python error: _enter_buffered_busy ... <stdin> ... at interpreter shutdown" (an abort on the Mac;
        # on Windows CPython 3.12 then crashes in fatal_output_debug, 0xc0000005, and Windows shows the player an
        # error report). os.read takes no lock of the io objects.
        fd, rest = sys.stdin.fileno(), b""
        while True:
            try:
                chunk = os.read(fd, 1 << 16)
            except OSError:
                chunk = b""
            if not chunk:
                break
            rest += chunk
            *whole, rest = rest.split(b"\n")
            for raw in whole:
                put(raw + b"\n")
        if rest:
            put(rest)
        q.put(None)

    threading.Thread(target=pump, daemon=True).start()
    return q


def refuse(problems: list):
    """The bot does not play: say why in the log the player reads, and keep the game's side of the pipe open."""
    for p in problems:
        advise("NOT PLAYING: " + p)
    panel_show("S_REFUSED", lines=[M.shown(problems[0], PANEL.lang)] if PANEL and problems else [])
    log("events.jsonl", {"t": time.time(), "event": "refused", "problems": problems})
    (LOG_DIR / "FINISHED").write_text(time.strftime("%Y-%m-%d %H:%M:%S") + " refused: " + " | ".join(problems) + "\n")


def main():
    import queue
    started = time.time()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    print("ready", flush=True)  # CommunicationMod waits for a first line
    advise(f"bridge started (records in {LOG_DIR})")
    global FACING, PANEL
    try:
        PANEL = P.Panel()
        PANEL.write()
    except Exception as ex:
        log("errors.jsonl", {"t": time.time(), "error": f"panel: {ex!r}"})
    forced = os.environ.get("CM_FACING_MOD") in ("0", "1")
    problems = preflight.profile_problems() if CFG.PREFLIGHT else []
    system = lock = probe = None
    if not problems:
        try:
            system = System(MODEL, RUBY_HP, search=CFG.SEARCH, search_threads=CFG.SEARCH_THREADS,
                            search_asc=CFG.ASCENSION, search_score=CFG.SEARCH_SCORE)
        except ImportError as ex:
            problems.append(M.Msg("P07", error=str(ex)[:300]))
        except Exception as ex:
            problems.append(M.Msg("P06", path=MODEL, error=str(ex)[:300]))
    if problems:
        refuse(problems)
    else:
        lock = Lockstep(None)  # auto decides itself
        probe = Auto(lock, system)
    saves = AutosaveSnapshots()
    lines = stdin_lines()
    waiting_since, asked = None, 0
    last_play = 0.0
    dup = DuplicateReplies()
    stuck = StuckChoice()  # (a stuck fight choice: StuckChoice, stuck_choice)
    configured = False
    paused, held = False, None  # paused by the player; the last ready state left unanswered meanwhile
    RESUME = object()  # in place of a line: answer the held state now
    quiet_since = time.time()
    while True:
        line = False
        if probe is not None and PANEL is not None:
            now_paused = PANEL.paused()
            if now_paused != paused:
                paused = now_paused
                log("events.jsonl", {"t": time.time(), "event": "paused" if paused else "resumed"})
                advise("paused by the player" if paused else "resumed by the player")
                if not probe.stopped:
                    panel_show("S_PAUSED" if paused else "S_PLAYING")
                if not paused:
                    # go on from the game's state as it is now. If the player changed the game meanwhile the
                    # simulator may not fit it: the usual resync is tried, and a stop from here on is reported as
                    # caused by that (Probe.stop)
                    probe.tried.clear()
                    probe.repeat.clear()
                    asked = 0
                    waiting_since = None if held is not None else time.time()  # nothing held: ask for the state in 5 s
                    if held is not None:
                        line = RESUME
        if line is False:
            try:
                line = lines.get(timeout=0.2)
                quiet_since = time.time()
            except queue.Empty:
                if paused or time.time() - quiet_since < 5.0:
                    continue
                quiet_since = time.time()
                line = False
        if line is False:
            # sent a command and no new state for 5 s (the game can refuse a command without a state change, e.g.
            # a potion with full slots): ask for the state once; the bot then tries another command
            if probe is not None and waiting_since is not None:
                if asked < 12:  # every 5 s for a minute
                    asked += 1
                    log("commands.jsonl", {"t": time.time(), "n": probe.sent, "cmd": "state", "why": "no ready state for 5 s"})
                    print("state", flush=True)
                    dup.polled()
                elif not probe.stopped:
                    probe.stop(M.Msg("R09"), probe.last or {})
            continue
        if line is None:
            break
        heartbeat()  # activity = a state received or a command sent, not the 5-second tick
        resumed = line is RESUME
        if resumed:
            msg, held = held, None
        else:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                log("bad_lines.txt", line[:2000])
                continue
        t_now = time.time()
        if not resumed:
            log("states.jsonl", {"t": t_now, "msg": msg})
        if probe is None:
            continue  # refused: read on, send nothing
        if not configured:
            # the first state: by now the game has loaded its mods. Decide the facing rule, and whether to start
            configured = True
            if msg.get("in_game"):
                problems = [M.Msg("P08")]
            elif CFG.PREFLIGHT and preflight.run_in_progress():
                problems = [M.Msg("P09")]
            if not forced:
                if preflight.mod_loaded(started):
                    FACING = "mouse"
                elif CFG.PREFLIGHT:
                    problems.append(M.Msg("P10"))
            use_facing(FACING)
            log("events.jsonl", {"t": time.time(), "event": "config", "mode": "auto", "model": MODEL, "search": SEARCH,
                                 "key_rule": KEY_RULE, "ruby_hp": RUBY_HP, "seed": Probe.SEED, "facing": FACING,
                                 "facing_why": "CM_FACING_MOD" if forced else "mod marker",
                                 "ascension": CFG.ASCENSION, "pace": CFG.PACE, "model_meta": getattr(system, "meta", None)})
            if problems:
                refuse(problems)
                probe = None
                continue
            advise(f"starting a run: start ironclad {CFG.ASCENSION} {Probe.SEED}")
        if resumed:
            log("commands.jsonl", {"t": t_now, "n": probe.sent, "cmd": None, "why": "resumed: answering the state held during the pause"})
        saves.poll(t_now)
        lock.latest_save = getattr(saves, "latest", None)
        # A state that is ready for a command ends the wait. A reply with ready_for_command false (the answer to
        # "state" after a refused potion pick) does not: CommunicationMod sends nothing more when the state does not
        # change, so keep asking.
        if not resumed and probe is not None and stuck.see(msg) and waiting_since is not None:
            # (a card choice in a fight that CommunicationMod never calls ready: stuck_choice; answered as ready)
            log("events.jsonl", {"t": time.time(), "event": "not_ready_choice_answered",
                                 "floor": (msg.get("game_state") or {}).get("floor"), "polls": stuck.count})
            msg = dict(msg, ready_for_command=True)
        if msg.get("ready_for_command", True) or "error" in msg:
            waiting_since, asked = None, 0
        elif waiting_since is None:
            waiting_since = time.time()
        if not resumed:
            try:
                lock.on_state(msg)
            except Exception as ex:  # never let one state kill the bridge
                log("errors.jsonl", {"t": time.time(), "error": repr(ex)})
                advise(f"error (logged): {ex!r}"[:200])
        if paused and configured and not probe.stopped:
            # paused by the player: the simulator still follows the game, but no command is sent. The newest state
            # is kept and answered when the player resumes
            if msg.get("ready_for_command", True) and "error" not in msg:
                held = msg
            waiting_since, asked = None, 0
            continue
        if resumed:
            probe.pause_tamper = probe.started and not lock.synced
        elif getattr(probe, "pause_tamper", False) and lock.synced:
            probe.pause_tamper = False  # the simulator fits the game again: later stops have their own causes
        if not resumed and dup.skip(msg, t_now):
            log("commands.jsonl", {"t": time.time(), "n": probe.sent, "cmd": None,
                                   "why": "the same state again right after its answer (reply to a state poll)"})
            # and keep waiting: if nothing else comes, the 5-second poll asks again and its reply is answered
            waiting_since = waiting_since or time.time()
            continue
        try:
            cmd = probe.next(msg)
        except Exception as ex:  # a translation bug must stop the run with a reason, not kill the bridge
            log("errors.jsonl", {"t": time.time(), "error": f"auto: {ex!r}"})
            if not probe.stopped:
                probe.stop(M.Msg("R10", error=repr(ex)[:200]), msg)
            cmd = None
        if cmd:
            if cmd.startswith("potion use") and time.time() - last_play < PLAY_SETTLE:
                # a card just played is still flying to the discard pile; when it lands the game resets its
                # cost for the turn, even if a potion (Liquid Memories) has moved it to the hand meanwhile.
                # The simulator models the settled game: let it land first
                time.sleep(PLAY_SETTLE - (time.time() - last_play))
            if cmd.startswith("play"):
                last_play = time.time()
            probe.sent += 1
            log("commands.jsonl", {"t": time.time(), "n": probe.sent, "cmd": cmd})
            print(cmd, flush=True)
            if cmd == "state":
                dup.polled()
            elif cmd.startswith("wait"):
                dup.waited()  # a wait's reply is the same state when nothing changed: never a poll's reply
                stuck.waited()  # (and the game is not ready while it runs: not a stuck choice)
                if FRAME_CLOCK:
                    # (the fake game: a wait comes back at once, so the frames waited are the only clock there is)
                    w = cmd.split()
                    lock.now = (lock.now or 0.0) + (int(w[1]) if len(w) > 1 and w[1].isdigit() else 1) / 60.0
            else:
                dup.answered(msg, t_now)
            waiting_since = time.time()
            heartbeat()
        if probe.stopped == RUN_OVER:
            # a finished run is the planned end: mark it and stop playing
            (LOG_DIR / "FINISHED").write_text(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
            log("events.jsonl", {"t": time.time(), "event": "finished"})
            advise("the run is over")
            if CFG.CLOSE_GAME:
                close_own_game()
            return


if __name__ == "__main__":
    main()
