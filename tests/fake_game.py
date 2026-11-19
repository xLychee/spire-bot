"""Fake CommunicationMod for testing the bridge (spire_bot.bridge) without the game. The simulator plays the game's part.

Two modes.
- One-way: plays one simulator game with random legal actions and prints a
  CommunicationMod-shaped state after every step.
    python tests/fake_game.py SEED [ASC] [STEPS]
- --auto: starts the bridge itself, as CommunicationMod does (its stdin and stdout are ours), in auto mode, and
  answers its commands: the main menu takes `start ironclad ASC <seed string>`, and from then on every command is
  read the way the game reads it (choose i on the list shown, play i t on the hand shown, potion use / discard,
  end, proceed, skip, leave, confirm, wait, state) and becomes the simulator action it names. Each screen is shown in
  CommunicationMod's shape, with the lists built from the simulator (the order of the game's own lists is what the
  bridge's translator assumes; the real game checks that, these runs do not). Writes the game's end as one JSON
  line (--result) and leaves the bridge's logs in --log-dir.
    python tests/fake_game.py --auto SEED --result out.jsonl --log-dir DIR
  The bridge's settings come from the environment (CM_MODEL, CM_SEARCH, CM_KEY_RULE, CM_PROFILE). Locked relics:
  the fake locks what bridge.locked_relics() reads from the profile, as the bridge does (--no-lock: none, and
  the bridge gets CM_PROFILE=/nonexistent).
A main-menu message comes first (in_game false), as in the real game."""
from collections import Counter
import argparse
import json
import os
import random
import select
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from spire_sim import Env, Space, _core
from spire_bot.seed_string import to_number

HERE = Path(__file__).resolve().parent


def event_codes(infos, room):
    """the simulator's event option codes in the game's button order (auto.EVENT_BUTTON; else code order)"""
    from spire_bot.auto import EVENT_BUTTON
    order = EVENT_BUTTON.get(room.get("event") or "", {})
    return sorted({i["idx1"] for _, i in infos if i.get("type") in ("neow", "event")}, key=lambda c: (order.get(c, c), c))


def debuffs(m):
    """a monster's Weak and Vulnerable as the game shows them, and a Bronze Orb's Stasis card: the fight signature
    compares them"""
    out = [{"id": i, "amount": int(m[k])} for i, k in (("Weakened", "weak"), ("Vulnerable", "vulnerable")) if m[k]]
    if m.get("stasis"):
        out.append({"id": "Stasis", "amount": -1, "card": {"name": m["stasis"][0] + ("+" if m["stasis"][1] else "")}})
    return out


def cm_state(env, seed, asc):
    st = env.state()
    gs = {"class": "IRONCLAD", "ascension_level": asc, "seed": seed if seed < 2**63 else seed - 2**64,
          "floor": st["floor"], "act": st["act"], "current_hp": env.hp, "max_hp": env.max_hp, "gold": st["gold"],
          "deck": [{"name": name + ("+" if up else "")} for _, up, name in st["deck"]],
          "relics": [{"name": r[1]} for r in st["relics"]],
          "potions": [{"name": "Potion Slot", "id": "Potion Slot"} if p[1] == "EMPTY_POTION_SLOT" else {"name": p[1]}
                      for p in st["potions"]],
          "screen_type": st["screen"].upper(), "room_phase": "COMBAT" if env.space == Space.BATTLE else "COMPLETE"}
    if st["screen"] == "map" and st["map_xy"][1] >= 0:
        gs["screen_state"] = {"current_node": {"x": st["map_xy"][0], "y": st["map_xy"][1]}}
    b = st.get("battle")
    if env.space == Space.BATTLE and b:
        gs["combat_state"] = {"hand": [{"name": c[3] + ("+" if c[1] else "")} for c in b["hand"]],
                              "player": {"energy": b["energy"], "block": b["block"]},
                              "draw_pile": [{}] * b["draw_pile"], "discard_pile": [{}] * b["discard_pile"],
                              "exhaust_pile": [{}] * b["exhaust_pile"],
                              "monsters": [{"current_hp": m["hp"], "block": m["block"], "is_gone": m["hp"] <= 0 or not m.get("targetable", True),
                                            "powers": debuffs(m)} for m in b["monsters"]]}
    return {"in_game": True, "ready_for_command": True, "available_commands": [], "game_state": gs}


# ---------------------------------------------------------------- --auto: the game's side of the conversation

def _plain(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


LATE_FRAMES = 40  # (0.67 s at 60 frames a second: the longest seen in clean-copy games was 0.71 s)


class FakeGame:
    def __init__(self, asc: int):
        self.asc, self.env, self.seed = asc, None, None
        self.view = None  # ("card", k): card reward k opened; "shop": the shop screen open
        self.over = False  # the score screen was left: back at the menu
        self.stepped = []  # every simulator action taken, in order
        # the real game without SuperFastMode can show a fight's first state before the monsters' intents exist
        # (intent DEBUG); with Runic Dome every intent reads NONE. Here: DEBUG on a fight's states until the first
        # "wait" (the intents come while it waits); any other command before that counts as an early play
        self.intents_floor = None  # the floor whose fight has its intents
        self.early_plays = 0
        # a card that joins the deck out of fights arrives late in the real game (a reward pick, a curse from an event
        # or Cursed Key's chest, Astrolabe's cards: 0.5 to 0.7 s at normal speed, while the rest of the step shows at
        # once). Here: the new cards are left out of the deck shown until the bridge has waited LATE_FRAMES frames
        # in all; a command other than wait or state before that counts as sent before the screen had settled
        self.late = None  # [the cards not shown yet, frames still to wait]
        self.unsettled_commands = 0
        self.unsettled = []  # where: floor, the command, the simulator's screen, the cards still on their way
        self.redraws = []  # every redrawing choice of three: floor and the extra draws made
        self.redraw = True  # --no-redraw: the card random stream is left alone (the run is then the simulator's own)
        self.late_frames = LATE_FRAMES  # --late-frames N

    # ---- what the game shows
    def infos(self):
        return [(a, json.loads(self.env.action_info_json(a))) for a in self.env.legal_actions()]

    def menu(self):
        return {"in_game": False, "ready_for_command": True, "available_commands": ["start", "state"]}

    def state(self):
        if self.env is None or self.over:
            return self.menu()
        e = self.env
        msg = cm_state(e, self.seed, self.asc)
        gs = msg["game_state"]
        if self.late is not None:
            hide = Counter(self.late[0])
            for k in range(len(gs["deck"]) - 1, -1, -1):  # the newest copies are the ones still on their way
                if hide[gs["deck"][k]["name"]] > 0:
                    hide[gs["deck"][k]["name"]] -= 1
                    del gs["deck"][k]
        av = ["wait", "state"]
        infos = self.infos()
        types = {i.get("type") for _, i in infos}
        if e.done or e.steps > 5000:
            gs["screen_type"] = "GAME_OVER"
            gs["screen_state"] = {"victory": bool(e.heart_won)}
            msg["available_commands"] = ["proceed"] + av
            return msg
        if e.space == Space.BATTLE:
            b = e.state()["battle"]
            cs = gs["combat_state"]
            ids = {i["hand_idx"]: i["card"] for _, i in infos if i.get("type") == "card"}
            for k, c in enumerate(b["hand"]):
                cs["hand"][k] = {"id": ids.get(k, c[3]), "upgrades": int(bool(c[1])), "cost": c[2],
                                 "is_playable": k in ids}
            cs["monsters"] = [{"id": m["name"], "current_hp": max(m["hp"], 0), "max_hp": m["max_hp"],
                               "block": m["block"], "is_gone": m["hp"] <= 0 or not m.get("targetable", True), "powers": debuffs(m)}
                              for m in b["monsters"]]
            cs["turn"] = 1
            dome = any("".join(ch for ch in r["name"].lower() if ch.isalnum()) == "runicdome" for r in gs["relics"])
            for m in cs["monsters"]:
                m["intent"] = "NONE" if dome else "DEBUG" if self.intents_floor != e.floor else "ATTACK"
            from spire_bot.auto import GRID_PILE
            if b.get("card_select") and b.get("card_select_task") in GRID_PILE:
                # a pick from a pile: the game shows a grid of only the cards on offer
                pile = e.pile_cards(GRID_PILE[b["card_select_task"]])
                gs["screen_type"] = "GRID"
                gs.setdefault("screen_state", {}).update(
                    cards=[{"name": pile[k][0] + ("+" if pile[k][1] else "")} for k in self.grid_picks()],
                    selected_cards=[], confirm_up=False)
                av += ["choose"]
            elif b.get("card_select") and b.get("card_select_task") in ("DISCOVERY", "CODEX"):
                # a choice of three (Discovery, the choice potions, Toolbox, Nilry's Codex): the game's card screen
                # with the three cards. The real game goes on drawing three cards from the card random stream on
                # every frame the choice's action lasts (not for the Codex, not for Toolbox's opening choice):
                # here the stream is moved on when the pick comes (apply)
                gs["screen_type"] = "CARD_REWARD"
                gs.setdefault("screen_state", {}).update(cards=[{"id": str(x), "name": str(x)} for x in e.select_cards()])
                if self.opening_choice(b):
                    for r in gs["relics"]:
                        if _plain(r["name"]) == "toolbox":
                            r["id"] = "Toolbox"  # (the bridge tells Toolbox's opening choice by this id)
                av += ["choose"]
                if any(i.get("type") == "select" and i["select"] >= len(e.select_cards()) for _, i in infos):
                    av += ["skip"]  # Nilry's Codex can be skipped: the simulator's option after the three cards
            elif b.get("card_select"):
                gs["screen_type"] = "HAND_SELECT"
                av += ["choose"] + (["confirm"] if "multi_select" in types else [])
            else:
                gs["screen_type"] = "NONE"
                av += [c for c, t in (("play", "card"), ("end", "end"), ("potion", "potion")) if t in types]
            msg["available_commands"] = av
            return msg
        scr = e.state()["screen"]
        room = json.loads(e.room_json())
        ss = gs.setdefault("screen_state", {})
        if "potion_drink" in types or "potion_discard" in types:
            av.append("potion")
        if scr == "event":
            codes = event_codes(infos, room)
            gs["screen_type"] = "EVENT"
            ss.update(event_id=room.get("event") or "Neow Event",
                      options=[{"choice_index": r, "disabled": False, "label": f"option {c}", "text": f"option {c}"}
                               for r, c in enumerate(codes)])
            gs["choice_list"] = [f"option {c}" for c in codes]
            av.append("choose")
        elif scr == "card_select" and any(i.get("match_and_keep") for _, i in infos):
            down = sorted((i for _, i in infos), key=lambda i: i["grid_idx"])
            gs["screen_type"] = "EVENT"
            ss.update(event_id="Match and Keep!",
                      options=[{"choice_index": r, "disabled": False,
                                "label": i["card"]["id"] if i.get("card") else f"card{i['grid_idx']}"}
                               for r, i in enumerate(down)])
            av.append("choose")
        elif scr == "card_select":
            cards = sorted((i for _, i in infos if i.get("card")), key=lambda i: i["idx1"])
            gs["screen_type"] = "GRID"
            # the cards already picked on a several-card grid (Astrolabe, a removal of two): the real game lists them
            # under selected_cards, and the lockstep reads its picks so far from there. Left empty, a pick could not
            # be told from no pick, the bot sent the same pick again and stopped ("not on the grid")
            picked = [{"name": name + ("+" if up else "")} for up, name in (e.state().get("select_picked") or [])]
            ss.update(cards=[dict(i["card"], upgrades=int(bool(i["card"]["upgrades"]))) for i in cards],
                      selected_cards=picked, confirm_up=False)
            if picked:
                # (the simulator does not say how many cards the grid asks for; with picks made it is at least one
                # more, which is all the lockstep needs to know that this is a several-card grid part way)
                ss["num_cards"] = len(picked) + 1
            av.append("choose")
        elif scr == "map":
            self.view = None
            xs = sorted({i["x"] for _, i in infos if i.get("type") == "map"})
            ss["next_nodes"] = [{"x": x, "y": e.state()["map_xy"][1] + 1} for x in xs]
            gs["screen_type"] = "MAP"
            gs["choice_list"] = [f"x={x}" for x in xs]
            av.append("choose")
        elif scr == "rewards" and isinstance(self.view, tuple):
            k = self.view[1]
            gs["screen_type"] = "CARD_REWARD"
            ss["cards"] = [dict(c, upgrades=int(bool(c["upgrades"]))) for c in room["rewards"]["cards"][k]]
            gs["choice_list"] = [c["id"] for c in ss["cards"]] + \
                (["bowl"] if any(i.get("singing_bowl") and i["idx1"] == k for _, i in infos) else [])
            av += ["choose", "skip"]
        elif scr == "rewards":
            gs["screen_type"] = "COMBAT_REWARD"
            ss["rewards"] = [r for r, _ in self.reward_list(room)]
            gs["choice_list"] = [r["reward_type"].lower() for r in ss["rewards"]]
            av += ["choose", "proceed"]
        elif scr == "treasure":
            gs["screen_type"] = "CHEST"
            ss.update(chest_type="SmallChest", chest_open=False)
            av += ["choose", "proceed"]
        elif scr == "boss_relic":
            gs["screen_type"] = "BOSS_REWARD"
            ss["relics"] = [{"id": r} for r in room.get("boss_relics", [])]
            gs["choice_list"] = room.get("boss_relics", [])
            av += ["choose", "skip"]
        elif scr == "rest":
            gs["screen_type"] = "REST"
            ss.update(rest_options=[i["option"] for _, i in sorted(infos, key=lambda x: x[1]["idx1"])
                                    if i.get("type") == "rest" and i["option"] != "skip"], has_rested=False)
            gs["choice_list"] = ss["rest_options"]
            av += ["choose", "proceed"]
        elif scr == "shop" and self.view == "shop":
            gs["screen_type"] = "SHOP_SCREEN"
            order = self.shop_list(room, gs["gold"])
            sh = room["shop"]
            ss.update(cards=[dict(c["card"], upgrades=int(bool(c["card"]["upgrades"])), price=c["price"])
                             for c in sh["cards"]],
                      relics=[dict(x) for x in sh["relics"]], potions=[dict(x) for x in sh["potions"]],
                      # the service as the game shows it: still on offer (the simulator lets it be bought) or
                      # too dear; translate leaves out what the gold cannot pay
                      purge_available="shop_card_remove" in types or sh["remove_cost"] > gs["gold"],
                      purge_cost=sh["remove_cost"])
            gs["choice_list"] = [k if x is None else (x.get("id") or x["card"]["id"]) for k, x in order]
            av += ["choose", "leave"]
        elif scr == "shop":
            gs["screen_type"] = "SHOP_ROOM"
            av += ["choose", "proceed"]
        msg["available_commands"] = av
        return msg

    @staticmethod
    def reward_list(room):
        r = room["rewards"]
        out = [({"reward_type": "GOLD", "gold": g}, ("gold", j)) for j, g in enumerate(r["gold"])]
        out += [({"reward_type": "RELIC", "relic": {"id": x}}, ("relic", j)) for j, x in enumerate(r["relics"])]
        out += [({"reward_type": "POTION", "potion": {"id": x}}, ("potion", j)) for j, x in enumerate(r["potions"])]
        out += [({"reward_type": "CARD"}, ("card", j)) for j in range(len(r["cards"]))]
        if r.get("emerald_key"):
            out.append(({"reward_type": "EMERALD_KEY"}, ("key", 0)))
        if r.get("sapphire_key"):
            out.append(({"reward_type": "SAPPHIRE_KEY"}, ("key", 0)))
        return out

    def shop_list(self, room, gold):
        """What the shop screen's choice list holds (auto.translate's order): the removal service if it can be
        paid, then cards, relics and potions that can be paid."""
        sh = room["shop"]
        types = {i.get("type") for _, i in self.infos()}
        order = [("purge", None)] if "shop_card_remove" in types and sh["remove_cost"] <= gold else []
        for k in ("cards", "relics", "potions"):
            order += [(k, x) for x in sh[k] if x["price"] <= gold]
        return order

    # ---- what a command does
    def find(self, pred):
        for a, i in self.infos():
            if pred(i):
                return a
        return None

    def apply(self, cmd: str):
        """Carry out one command; an error text if the game would refuse it."""
        w = cmd.split()
        if not w:
            return "empty command"
        c = w[0].lower()
        if self.env is not None and not self.over and not self.env.done and self.env.space == Space.BATTLE \
                and self.intents_floor != self.env.floor and c != "state":
            self.intents_floor = self.env.floor
            if c != "wait":
                self.early_plays += 1
        if self.late is not None and c == "wait":
            self.late[1] -= int(w[1]) if len(w) > 1 and w[1].isdigit() else 1
            if self.late[1] <= 0:
                self.late = None
        elif self.late is not None and c != "state":
            self.unsettled_commands += 1
            self.unsettled.append({"floor": self.env.floor if self.env else None, "cmd": cmd,
                                   "screen": self.env.state()["screen"] if self.env else None,
                                   "cards": sorted(self.late[0].elements())})
            self.late = None
        if c in ("wait", "state"):
            return None
        if self.env is None or self.over:
            if c == "start" and len(w) >= 4:
                self.seed, self.asc = to_number(w[3]), int(w[2])
                self.env, self.view, self.over = Env(self.seed, self.asc), None, False
                self.env.speedrun_pace = os.environ.get("CM_PACE") == "bot"
                return None
            return f"command {cmd!r} at the main menu"
        e = self.env
        if e.done or e.steps > 5000:
            if c == "proceed":
                self.over = True
                return None
            return f"{cmd!r} on the score screen"
        n = [int(x) for x in w[1:] if x.lstrip("-").isdigit()]
        a = None
        if c == "potion" and len(w) >= 3:
            slot = int(w[2])
            if w[1] == "use":
                tgt = int(w[3]) if len(w) > 3 else None
                a = self.find(lambda i: i.get("type") in ("potion", "potion_drink") and i.get("slot") == slot
                              and not i.get("discard") and (tgt is None or i.get("target") == tgt))
            else:
                a = self.find(lambda i: (i.get("type") == "potion" and i.get("discard") or
                                         i.get("type") == "potion_discard") and i.get("slot") == slot)
                if a is None and e.space != Space.BATTLE:
                    a = 0xC0000000 | slot  # out of a fight: the bridge's own swap discard
        elif e.space == Space.BATTLE:
            if c == "end":
                a = self.find(lambda i: i.get("type") == "end")
            elif c == "play" and n:
                h, tgt = n[0] - 1, (n[1] if len(n) > 1 else None)
                a = self.find(lambda i: i.get("type") == "card" and i["hand_idx"] == h and
                              (not i["requires_target"] or i["target"] == tgt))
            elif c == "choose" and n and e.state()["battle"].get("card_select_task") in __import__("spire_bot.auto", fromlist=["GRID_PILE"]).GRID_PILE:
                picks = self.grid_picks()
                a = self.find(lambda i: i.get("type") == "select" and i["select"] == picks[n[0]]) \
                    if n[0] < len(picks) else None
            elif c == "choose" and n:
                a = self.find(lambda i: i.get("type") == "select" and i["select"] == n[0])
                b = e.state()["battle"]
                if a is not None and self.redraw and b.get("card_select_task") == "DISCOVERY" and not self.opening_choice(b):
                    # the frames the choice lasted: 3 extra draws each. 2 to 15 frames, fixed by the seed and the step
                    frames = random.Random(self.seed * 1000003 + e.steps).randint(2, 15)
                    e.advance_card_rng(3 * frames)
                    self.redraws.append({"floor": e.floor, "extra_draws": 3 * frames})
            elif c == "skip" and e.state()["battle"].get("card_select_task") in ("DISCOVERY", "CODEX"):
                a = self.find(lambda i: i.get("type") == "select" and i["select"] >= len(e.select_cards()))
            elif c == "confirm":
                a = self.find(lambda i: i.get("type") == "multi_select")
        else:
            a = self.game_action(c, n)
            if isinstance(a, str) or a == "view":
                return None if a == "view" else a
        if a is None:
            return f"command {cmd!r} not possible here"
        self.stepped.append(int(a))
        before = Counter(name + ("+" if up else "") for _, up, name in e.state()["deck"]) if e.space != Space.BATTLE else None
        reward_pick = json.loads(e.action_info_json(a)).get("type") == "reward_card"
        e.step(a)
        if before is not None and not e.done and e.space != Space.BATTLE:
            after = Counter(name + ("+" if up else "") for _, up, name in e.state()["deck"])
            new, gone = after - before, before - after
            for name in list(new):  # a card upgraded in place is not a card on its way
                if name.endswith("+") and gone[name[:-1]] > 0:
                    k = min(new[name], gone[name[:-1]])
                    new[name] -= k
                    gone[name[:-1]] -= k
            new = +new
            if new:
                # --late-frames is for cards that join the deck by anything but a reward pick (an event's curse,
                # Cursed Key's chest, a transform): the bridge waits for its own reward pick to arrive for at most
                # 180 frames and then sends the pick again, so a reward card later than that would test that wait,
                # not the time an unmatched state has to stand
                self.late = [new, LATE_FRAMES if reward_pick else self.late_frames]
        return None

    def opening_choice(self, b) -> bool:
        """Toolbox's choice before the fight's first draw (drawn once in the real game)"""
        return (not b["hand"] and not b["discard_pile"] and not b["exhaust_pile"]
                and any(_plain(r[1]) == "toolbox" for r in self.env.state()["relics"]))

    def grid_picks(self):
        """the simulator's pick indexes on a pile grid, in the pile's order (the fake's grid order)"""
        return sorted(i["select"] for _, i in self.infos() if i.get("type") == "select")

    def game_action(self, c, n):
        e = self.env
        infos = self.infos()
        scr = e.state()["screen"]
        room = json.loads(e.room_json())
        k = n[0] if n else None
        first = lambda pred: next((a for a, i in infos if pred(i)), None)
        if scr == "event" and c == "choose":
            codes = event_codes(infos, room)
            if k is None or k >= len(codes):
                return f"Index {k} out of bounds in command \"choose {k}\""
            return first(lambda i: i.get("idx1") == codes[k] and i.get("idx2", 0) == 0 and
                         i.get("type") in ("neow", "event")) or \
                first(lambda i: i.get("idx1") == codes[k] and i.get("type") in ("neow", "event"))
        if scr == "card_select" and c == "choose":
            if any(i.get("match_and_keep") for _, i in infos):
                down = sorted(infos, key=lambda x: x[1]["grid_idx"])
                return down[k][0] if k is not None and k < len(down) else "flip out of bounds"
            cards = sorted(((a, i) for a, i in infos if i.get("card")), key=lambda x: x[1]["idx1"])
            return cards[k][0] if k is not None and k < len(cards) else "grid pick out of bounds"
        if scr == "map" and c == "choose":
            xs = sorted({i["x"] for _, i in infos if i.get("type") == "map"})
            return first(lambda i: i.get("type") == "map" and i["x"] == xs[k]) if k is not None and k < len(xs) \
                else "map choice out of bounds"
        if scr == "rewards" and isinstance(self.view, tuple):
            j = self.view[1]
            if c == "skip":
                self.view = None
                return "view"
            if c == "choose" and k is not None:
                ncards = len(room["rewards"]["cards"][j])
                a = first(lambda i: i.get("type") == "reward_card" and i["idx1"] == j and
                          (i["idx2"] == k if k < ncards else i.get("singing_bowl")))
                if a is not None:
                    self.view = None
                return a
            return None
        if scr == "rewards":
            if c == "proceed":
                return first(lambda i: i.get("type") == "reward_skip")
            lst = self.reward_list(room)
            if c != "choose" or k is None or k >= len(lst):
                return "reward choice out of bounds"
            kind, j = lst[k][1]
            if kind == "card":
                self.view = ("card", j)
                return "view"
            if kind == "potion":
                a = first(lambda i: i.get("type") == "reward_potion" and i["idx1"] == j and i["idx2"] == 0)
                return a if a is not None else "NOT ENOUGH POTION SLOTS"
            return first(lambda i: i.get("type") == f"reward_{kind}" and (kind == "key" or i["idx1"] == j))
        if scr == "treasure":
            return first(lambda i: i.get("type") == ("open_chest" if c == "choose" else "skip_chest"))
        if scr == "boss_relic":
            if c == "skip":
                return first(lambda i: i.get("type") == "boss_relic" and "relic" not in i)
            return first(lambda i: i.get("type") == "boss_relic" and i["idx1"] == k)
        if scr == "rest":
            if c == "proceed":
                return first(lambda i: i.get("type") == "rest" and i["option"] == "skip")
            opts = [i["option"] for _, i in sorted(infos, key=lambda x: x[1]["idx1"])
                    if i.get("type") == "rest" and i["option"] != "skip"]
            return first(lambda i: i.get("type") == "rest" and i["option"] == opts[k]) \
                if k is not None and k < len(opts) else "rest option out of bounds"
        if scr == "shop":
            if self.view != "shop":
                if c == "proceed":
                    return first(lambda i: i.get("type") == "shop_skip")
                if c == "choose":
                    self.view = "shop"
                    return "view"
                return None
            if c == "leave":
                self.view = None
                return "view"
            order = self.shop_list(room, e.state()["gold"])
            if c != "choose" or k is None or k >= len(order):
                return "shop choice out of bounds"
            kind, x = order[k]
            if kind == "purge":
                return first(lambda i: i.get("type") == "shop_card_remove")
            same = [y for kk, y in order[:k] if kk == kind and y == x]  # earlier identical items
            t = {"cards": "shop_card", "relics": "shop_relic", "potions": "shop_potion"}[kind]
            key = (lambda i: i.get("card") == x["card"]) if kind == "cards" else \
                (lambda i: i.get(kind[:-1]) == x["id"])
            cands = sorted((i["idx1"], a) for a, i in infos if i.get("type") == t and i.get("idx2", 0) == 0 and key(i))
            if len(cands) <= len(same):
                return "NOT ENOUGH POTION SLOTS" if kind == "potions" else "cannot buy"
            return cands[len(same)][1]
        return None


def run_auto(a):
    sys.path.insert(0, str(HERE))
    out = Path(a.log_dir or tempfile.mkdtemp(prefix="fake_game_"))
    out.mkdir(parents=True, exist_ok=True)
    mode = out / "mode"
    mode.write_text("auto\n")
    env = dict(os.environ, CM_MODE_FILE=str(mode), CM_LOG_DIR=str(out), CM_ADVICE=str(out / "advice.txt"),
               CM_SIM_SEED=str(a.seed), PYTHONUNBUFFERED="1", OMP_NUM_THREADS="1", CM_SAVES_DIR=str(out / "no_saves"),
               CM_FACING_MOD="1", CM_PREFLIGHT="0", SPIRE_GAME_DIR=str(out),
               CM_FRAME_CLOCK="1")  # the fake game is the simulator: it turns the player as the mouse does
    env.pop("CM_PROBE_SEED", None)
    if a.no_lock:
        env["CM_PROFILE"] = "/nonexistent"
        _core.set_locked_relics([])
    else:
        os.environ.update({k: v for k, v in env.items() if k == "CM_PROFILE"})
        from spire_bot import bridge as cm_bridge
        _core.set_locked_relics(cm_bridge.locked_relics())
    log = open(out / "fake_conversation.jsonl", "w")
    p = subprocess.Popen([sys.executable, "-m", "spire_bot.bridge"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=open(out / "bridge_stderr.log", "w"), env=env, text=True, bufsize=1)
    g = FakeGame(a.asc)
    g.redraw = not a.no_redraw
    g.late_frames = a.late_frames
    t0 = time.time()
    sent = errors = 0
    stop = None
    panel_trace, pause_info = [], {}

    extra_gold = [0]

    def send(msg):
        if extra_gold[0] and msg.get("game_state"):
            msg["game_state"]["gold"] = msg["game_state"].get("gold", 0) + extra_gold[0]
        p.stdin.write(json.dumps(msg) + "\n")
        p.stdin.flush()

    first = p.stdout.readline().strip()  # CommunicationMod waits for the bridge's first line ("ready")
    if first != "ready":
        stop = f"bridge said {first!r} first"
    else:
        send(g.state())
    last_real = time.time()
    while stop is None:
        r, _, _ = select.select([p.stdout], [], [], 5.0)
        if not r:
            if p.poll() is not None:
                stop = "bridge exited"
            elif time.time() - last_real > a.idle:
                stop = f"no command but state for {a.idle} s"
            continue
        line = p.stdout.readline()
        if not line:
            stop = "bridge exited" if (g.over or g.env is None) else "bridge closed its output"
            break
        cmd = line.strip()
        if not cmd:
            continue
        if cmd.split()[0] != "state":
            last_real = time.time()
        sent += 1
        err = g.apply(cmd)
        log.write(json.dumps({"cmd": cmd, "error": err, "floor": g.env.floor if g.env else None,
                              "steps": len(g.stepped)}) + "\n")
        status = out / "spirebot_status.json"
        if a.panel_trace and status.exists():
            try:
                st_ = json.loads(status.read_text(encoding="utf-8"))
                panel_trace.append({"cmd": cmd, "n": st_.get("n"), "state": st_.get("state"), "title": st_.get("title"),
                                    "lines": st_.get("lines"), "viz": st_.get("viz")})
            except ValueError:
                panel_trace.append({"cmd": cmd, "unreadable": True})
        pausing = a.pause_at and sent == a.pause_at and not err
        if pausing:  # the player presses the pause key: the mod writes the control file (before the next state goes out)
            (out / "spirebot_control.json").write_text(json.dumps({"paused": True, "n": 1}))
            time.sleep(0.5)
        if err:
            errors += 1
            send({"error": err, "ready_for_command": True, "in_game": not g.over})
        else:
            send(g.state())
        if pausing:
            t_end, during = time.time() + a.pause_seconds, []
            tampered = None
            while time.time() < t_end:
                if a.tamper and tampered is None and time.time() > t_end - a.pause_seconds / 2:
                    # the player plays a step by hand while the bot is paused: the game sends its new state
                    if a.tamper == "bad":
                        tampered, extra_gold[0] = "gold +777", 777
                    else:
                        acts = g.env.legal_actions()
                        tampered = acts[len(acts) // 2]
                        g.env.step(tampered)
                    send(g.state())
                r, _, _ = select.select([p.stdout], [], [], 0.2)
                if r:
                    during.append(p.stdout.readline().strip())
            pause_info.update(commands_during_pause=during, tampered=tampered,
                              panel_state_paused=json.loads(status.read_text(encoding="utf-8")).get("state") if status.exists() else None)
            (out / "spirebot_control.json").write_text(json.dumps({"paused": False, "n": 2}))
            last_real = time.time()
        if time.time() - t0 > a.max_seconds:
            stop = f"over {a.max_seconds} s"
    if p.poll() is None:
        try:
            p.stdin.close()
        except BrokenPipeError:
            pass
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
    e = g.env
    why = None
    for pj in out.rglob("probe.jsonl"):
        rows = [json.loads(x) for x in open(pj)]
        why = next((r["stop"] for r in reversed(rows) if "stop" in r), None)
    res = {"seed": a.seed, "seed_string": None, "floor": e.floor if e else None, "act": e.act if e else None,
           "won": bool(e.won) if e else False, "heart_won": bool(e.heart_won) if e else False,
           "steps": e.steps if e else 0, "done": bool(e.done) if e else False, "capped": bool(e and e.steps > 5000),
           "finished": any(out.rglob("FINISHED")), "bridge_stop": why, "fake_stop": stop, "commands": sent,
           "refused": errors, "early_plays": g.early_plays, "unsettled_commands": g.unsettled_commands, "unsettled": g.unsettled,
           "redraws": g.redraws,
           "rule_misses": sum(1 for pj in out.rglob("rule_miss.jsonl") for _ in open(pj)), "seconds": round(time.time() - t0, 1), "log_dir": str(out)}
    if pause_info:
        res["pause"] = pause_info
    if a.panel_trace:
        (out / "panel_trace.json").write_text(json.dumps(panel_trace, ensure_ascii=False))
    (out / "fake_actions.json").write_text(json.dumps(g.stepped))
    from spire_bot.seed_string import to_string
    res["seed_string"] = to_string(a.seed)
    if a.result:
        with open(a.result, "a") as f:
            f.write(json.dumps(res) + "\n")
    print(json.dumps(res), flush=True)


def main():
    if "--auto" in sys.argv:
        ap = argparse.ArgumentParser()
        ap.add_argument("--auto", dest="seed", type=int, required=True, help="simulator seed")
        ap.add_argument("--asc", type=int, default=20)
        ap.add_argument("--result", help="append the game's end here (JSON line)")
        ap.add_argument("--log-dir", help="the bridge's log folder (default: a new temp folder)")
        ap.add_argument("--no-lock", action="store_true", help="no locked relics on either side")
        ap.add_argument("--no-redraw", action="store_true",
                        help="a redrawing choice of three does not move the card random stream on")
        ap.add_argument("--late-frames", type=int, default=LATE_FRAMES,
                        help="frames the bridge has to wait before a card that joined the deck out of fights shows "
                             "(default %(default)s; above 180 the bridge takes it for a miss: 3 s at 60 frames)")
        ap.add_argument("--pace", choices=("bot", "human"), default="bot",
                        help="the game's pace: bot (playtime under 800 s, no Secret Portal in act 3; default, as the "
                             "bot's real games from scratch) or human. Both sides get it (CM_PACE)")
        ap.add_argument("--idle", type=float, default=90, help="stop when the bridge sends only 'state' this long")
        ap.add_argument("--max-seconds", type=float, default=3600)
        ap.add_argument("--panel-trace", action="store_true", help="after each command keep what the panel's status file shows (panel_trace.json)")
        ap.add_argument("--pause-at", type=int, default=0, help="after this many commands the player pauses the bot")
        ap.add_argument("--pause-seconds", type=float, default=4.0)
        ap.add_argument("--tamper", choices=("step", "bad"), help="during the pause the player changes the game: step = "
                        "one legal step by hand; bad = a change the simulator cannot follow (gold appears, and stays)")
        a = ap.parse_args()
        os.environ["CM_PACE"] = a.pace  # this fake and the bridge it starts
        run_auto(a)
        return
    seed = int(sys.argv[1])
    asc = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    steps = int(sys.argv[3]) if len(sys.argv) > 3 else 400
    rng = random.Random(seed)
    print(json.dumps({"in_game": False, "ready_for_command": True, "available_commands": ["start"]}), flush=True)
    env = Env(seed, asc)
    print(json.dumps(cm_state(env, seed, asc)), flush=True)
    for _ in range(steps):
        if env.done:
            break
        env.step(rng.choice(env.legal_actions()))
        print(json.dumps(cm_state(env, seed, asc)), flush=True)


if __name__ == "__main__":
    main()
