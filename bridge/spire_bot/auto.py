"""Auto mode for the real game: the network picks a simulator action, and this turns it into the next
CommunicationMod command for the screen the game is on.

Stateless: on every state the decision is computed again on the lockstep simulator (the network and the fair search
are deterministic for a given position), so a choice that takes several game screens (open the card reward, then pick
the card) needs no memory: on each screen the same decision gives the next command. Anything it cannot translate
returns None with a reason; the bridge then stops and logs instead of guessing.
"""
from __future__ import annotations

import json


ALREADY_MARKED = "grid pick already marked in the game"

# a fight's grid picks from a pile (the simulator's card select task -> the pile its index counts in)
GRID_PILE = {"SECRET_WEAPON": "draw", "SECRET_TECHNIQUE": "draw", "SEEK": "draw", "EXHUME": "exhaust",
             "HEADBUTT": "discard", "HOLOGRAM": "discard", "MEDITATE": "discard", "LIQUID_MEMORIES_POTION": "discard"}

# events whose game buttons are in another order than the simulator's option codes: sim code -> game button (its place
# in the game's option list). Knowing Skull: the simulator has 0 gold, 1 card, 2 potion, 3 leave; the game's buttons
# are potion, gold, card, leave. Sent by rank, a "gold" pick would take a potion in the game
EVENT_BUTTON = {"Knowing Skull": {0: 1, 1: 2, 2: 0, 3: 3}}


def _norm(s) -> str:
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


# the only card whose game id differs from its name (the simulator's): Apparition.java, ID = "Ghostly"
CARD_ID_ALIAS = {"ghostly": "apparition"}


def _game_card_name(cid) -> str:
    """a game card id as the simulator's card name, normalized ("Ghostly" -> apparition, "Strike_R" -> striker)"""
    k = _norm(cid)
    return CARD_ID_ALIAS.get(k, k)


def _card_match(c: dict, want: dict) -> bool:
    return _norm(c.get("id", "")).rstrip("rgbp") == _norm(want["id"]).rstrip("rgbp") and \
        bool(c.get("upgrades", 0)) == bool(want.get("upgrades"))


def _find(seq, pred):
    return next((i for i, x in enumerate(seq) if pred(x)), None)


def add_target_rank(info: dict, env) -> dict:
    """The target's place among the simulator's living monsters: the game's list can hold dead entries the simulator
    has reused (a split slime), so targets are matched by that rank, not by slot. A monster that has escaped (a
    Looter or Mugger: HP left, not targetable) is not among them: the game lists it as gone (real game 5600132,
    floor 19: with the escaped Looter counted, the Mugger was the second living monster of one)."""
    tgt = info.get("target", -1)
    b = (env.state().get("battle") or {}) if info.get("space") == "battle" else {}
    mons = b.get("monsters") or []
    here = lambda m: m["hp"] > 0 and m.get("targetable", True)
    if 0 <= tgt < len(mons) and here(mons[tgt]):
        info["target_rank"] = sum(1 for m in mons[:tgt] if here(m))
    return info


def add_choice_context(info: dict, env, acts, gs: dict) -> dict:
    """What translate needs beyond the action itself, read from the simulator before the action is taken:
    - an event pick's rank among the simulator's distinct event options and their number (idx1 is an internal
      code, 5 and 6 on the Cursed Tome's last page);
    - on the game's open card reward, the simulator's whole chosen reward, to compare with the game's;
    - for a card played in a fight, whether the simulator's copy is upgraded."""
    if info.get("space") == "battle" and info.get("type") == "card":
        hand = (env.state().get("battle") or {}).get("hand") or []
        if 0 <= info.get("hand_idx", -1) < len(hand):
            info["upgraded"] = bool(hand[info["hand_idx"]][1])  # Strike or Strike+
            info["cost"] = int(hand[info["hand_idx"]][2])  # this turn's cost: a 0-cost copy is not the 1-cost one
    if info.get("space") == "battle" and info.get("type") == "select" and \
            str(gs.get("screen_type")).upper() == "HAND_SELECT":
        # a hand pick: the simulator numbers the whole hand, the game only the cards it shows (Dual Wield sets the
        # others aside). Name the card, and its rank among the same cards the simulator could pick
        hand = (env.state().get("battle") or {}).get("hand") or []
        k = info.get("select", -1)
        picks = sorted(i["select"] for i in (json.loads(env.action_info_json(a)) for a in acts)
                       if i.get("type") == "select" and 0 <= i.get("select", -1) < len(hand))
        # only when some hand card cannot be picked (the game sets those aside); otherwise the lists are the same and
        # the index is the game's (Gambling Chip, multi-pick screens: as before)
        if 0 <= k < len(hand) and len(picks) < len(hand):
            key = lambda c: (_norm(c[3]).rstrip("rgbp"), bool(c[1]), int(c[2]))
            info["select_card"] = {"name": hand[k][3], "upgraded": bool(hand[k][1]), "cost": int(hand[k][2]),
                                   "rank": sum(1 for j in picks if j < k and key(hand[j]) == key(hand[k]))}
    task = (env.state().get("battle") or {}).get("card_select_task") or ""
    if info.get("space") == "battle" and info.get("type") == "select" and task in GRID_PILE and \
            str(gs.get("screen_type")).upper() == "GRID":
        # a pick from a pile on a grid: the simulator's index counts in the whole pile, the game lists only the cards
        # it offers, in its own order (Secret Weapon over a 24-card draw pile shows only the 13 attacks).
        # Name the card and its rank among the same cards the simulator offers
        pile = env.pile_cards(GRID_PILE[task])
        k = info.get("select", -1)
        picks = sorted(i["select"] for i in (json.loads(env.action_info_json(a)) for a in acts)
                       if i.get("type") == "select" and 0 <= i.get("select", -1) < len(pile))
        if 0 <= k < len(pile):
            key = lambda c: (_norm(c[0]).rstrip("rgbp"), bool(c[1]))
            info["grid_card"] = {"name": pile[k][0], "upgraded": bool(pile[k][1]),
                                 "rank": sum(1 for j in picks if j < k and key(pile[j]) == key(pile[k]))}
    if info.get("type") in ("neow", "event"):
        codes = sorted({i["idx1"] for i in (json.loads(env.action_info_json(a)) for a in acts)
                        if i.get("type") in ("neow", "event")})
        if info["idx1"] in codes:
            info["event_rank"], info["event_n"] = codes.index(info["idx1"]), len(codes)
    if info.get("type") == "reward_card" and str(gs.get("screen_type")).upper() == "CARD_REWARD":
        cr = (json.loads(env.room_json()).get("rewards") or {}).get("cards") or []
        if 0 <= info.get("idx1", -1) < len(cr):
            info["reward_cards"] = cr[info["idx1"]]
    return info


def _game_target(info: dict, cs: dict) -> int:
    """The game's monster index for the action's target (see add_target_rank); the slot itself without a rank."""
    rank = info.get("target_rank")
    if rank is None:
        return info.get("target", -1)
    living = [i for i, m in enumerate(cs.get("monsters", [])) if not m.get("is_gone") and m.get("current_hp", 0) > 0]
    return living[rank] if rank < len(living) else -1


def translate(info: dict, msg: dict) -> tuple[str | None, str]:
    """(command, note) for the simulator action described by info (Env.action_info_json) on the game state msg."""
    gs = msg.get("game_state") or {}
    av = [a.lower() for a in msg.get("available_commands", [])]
    screen = str(gs.get("screen_type", "")).upper()
    ss = gs.get("screen_state") or {}
    t = info.get("type")

    # ---------------- battle
    if info.get("space") == "battle":
        cs = gs.get("combat_state") or {}
        if t == "end":
            return ("end", "") if "end" in av else (None, "end not available")
        if t == "card":
            want = {"id": info["card"], "upgrades": None}
            hand = cs.get("hand", [])
            # the lockstep simulator may hold the hand in another order: find the card by id, playable
            same = lambda c: _norm(c.get("id")) == _norm(info["card"]) and c.get("is_playable")
            up, cost = info.get("upgraded"), info.get("cost")
            # the same version first: with Perfected Strike and Perfected Strike+ in hand, or a Strike made free this
            # turn next to a 1-cost one, the id alone would play the wrong one
            same_up = lambda c: same(c) and (up is None or bool(c.get("upgrades", 0)) == up)
            i = _find(hand, lambda c: same_up(c) and (cost is None or "cost" not in c or int(c["cost"]) == cost))
            if i is None:
                i = _find(hand, same_up)
            if i is None:
                i = _find(hand, same)
            if i is None or "play" not in av:
                return None, f"card {info['card']} not playable in the game hand"
            if info.get("requires_target"):
                tgt = _game_target(info, cs)
                return (f"play {i + 1} {tgt}", "") if tgt >= 0 else (None, "target not found in the game")
            return f"play {i + 1}", ""
        if t == "potion":
            if info.get("discard"):
                return f"potion discard {info['slot']}", ""
            tgt = _game_target(info, cs) if info.get("target", -1) >= 0 else -1
            # (any index the game's list has: dead and escaped entries stay in it, so a Gremlin Leader's fight has 7)
            return (f"potion use {info['slot']} {tgt}" if tgt >= 0 else f"potion use {info['slot']}"), ""
        if t == "select" and info.get("select_card") and screen == "HAND_SELECT":
            # by card, not by index: the game's list can leave cards out (add_choice_context)
            sc = info["select_card"]
            shown = ss.get("hand") or cs.get("hand") or []
            def same(c):
                if "id" not in c:  # tests/fake_game.py sends simulator names, "+" when upgraded
                    return _norm(c.get("name", "")) == _norm(sc["name"]) and c.get("name", "").endswith("+") == sc["upgraded"]
                return _game_card_name(c["id"]).rstrip("rgbp") == _norm(sc["name"]).rstrip("rgbp") and \
                    bool(c.get("upgrades", 0)) == sc["upgraded"]
            idx = [i for i, c in enumerate(shown) if same(c) and ("cost" not in c or int(c["cost"]) == sc["cost"])] \
                or [i for i, c in enumerate(shown) if same(c)]
            if not idx or "choose" not in av:
                return None, f"hand pick {sc['name']} not in the game's list"
            return f"choose {idx[min(sc['rank'], len(idx) - 1)]}", "hand pick by card"
        if t == "select" and info.get("grid_card") and screen == "GRID":
            gc = info["grid_card"]
            shown = ss.get("cards") or []
            def same_g(c):
                if "id" not in c:  # tests/fake_game.py
                    return _norm(c.get("name", "")).rstrip("rgbp") == _norm(gc["name"]).rstrip("rgbp") and \
                        c.get("name", "").endswith("+") == gc["upgraded"]
                return _game_card_name(c["id"]).rstrip("rgbp") == _norm(gc["name"]).rstrip("rgbp") and \
                    bool(c.get("upgrades", 0)) == gc["upgraded"]
            # cards already marked on a grid that takes several stay in the game's list (choosing one again unmarks
            # it); the simulator has taken them out of its pile, so they are not among the cards it offers
            marked = [c.get("uuid") for c in (ss.get("selected_cards") or []) if isinstance(c, dict)] \
                if isinstance(ss.get("selected_cards"), list) else []
            idx = [i for i, c in enumerate(shown) if same_g(c) and not (c.get("uuid") and c.get("uuid") in marked)]
            if not idx and any(same_g(c) for c in shown):
                return None, ALREADY_MARKED  # every such card is marked already: choosing it again would unmark it
            if not idx or "choose" not in av:
                return None, f"grid pick {gc['name']} not in the game's list"
            return f"choose {idx[min(gc['rank'], len(idx) - 1)]}", "grid pick by card"
        if t == "select" and screen == "CARD_REWARD" and info.get("select", 0) >= len(ss.get("cards") or []) > 0:
            # Nilry's Codex at the turn's end: the simulator's last option (3) is "skip", the game's is its skip
            # command; "choose 3" would be out of bounds
            return ("skip", "codex skip") if "skip" in av else (None, "codex skip: no skip")
        if t == "select":  # a card chosen inside a battle (Armaments, Headbutt, ...): same order as the game's list
            return (f"choose {info['select']}", "battle select by index") if "choose" in av else (None, "no choose")
        if t == "multi_select":
            # multi-pick screens (Forethought+, Elixir, Gambler's Brew): the simulator's picks were sent one by one
            # ("select"); its "done" is the game's confirm button
            if info.get("selected"):
                return None, "multi select with cards: the simulator now picks one card per action"
            return ("confirm", "done") if "confirm" in av else (None, "done: no confirm")
        return None, f"battle action {t}"

    # ---------------- out of battle
    # the game is still on a one-button screen (an event's "leave" / "continue") that the simulator has already passed
    opts = ss.get("options") or []
    if screen == "EVENT" and t not in ("neow", "event") and len(opts) == 1 and opts[0].get("choice_index") is not None:
        return f"choose {opts[0]['choice_index']}", "one-button event screen"
    if screen == "COMBAT_REWARD" and not (ss.get("rewards") or []) and t and t.startswith("reward_") and t != "reward_skip":
        return ("state", "rewards not listed yet") if "state" in av else (None, "rewards empty")
    if t in ("potion_drink", "potion_discard"):
        return (f"potion {'use' if t == 'potion_drink' else 'discard'} {info['slot']}", "")
    if t in ("neow", "event"):
        opts = ss.get("options") or []
        k = info["idx1"]
        enabled = [o for o in opts if not o.get("disabled") and o.get("choice_index") is not None]
        rank, n = info.get("event_rank"), info.get("event_n")
        button = EVENT_BUTTON.get(ss.get("event_id"), {}).get(k)
        if button is not None and len(opts) > 1:
            if button < len(opts) and not opts[button].get("disabled") and opts[button].get("choice_index") is not None:
                return f"choose {opts[button]['choice_index']}", "event button table"
            return None, f"{ss.get('event_id')}: option {k}'s button {button} not enabled"
        if rank is not None and n == len(enabled) and rank < n:
            # idx1 is the simulator's internal option code (the Cursed Tome's last page offers codes 5 and 6 for the
            # game's two options): when the simulator offers as many options as the game enables,
            # the pick's rank among the simulator's options is its place among the game's enabled ones
            return f"choose {enabled[rank]['choice_index']}", "event by rank"
        if opts:  # the simulator numbers the options as listed; the game's choose index skips disabled ones
            if k < len(opts) and not opts[k].get("disabled") and opts[k].get("choice_index") is not None:
                return f"choose {opts[k]['choice_index']}", ""
            if len(opts) == 1 and opts[0].get("choice_index") is not None:  # a one-button screen (talk, leave)
                return f"choose {opts[0]['choice_index']}", "single option"
        n_game = len(enabled) if opts else len(gs.get("choice_list") or [])
        if "choose" not in av:
            return None, "event: no choose"
        if k >= n_game:  # never send a code the game's list does not have (it refuses it, and we would loop)
            return None, f"event option {k} (rank {rank} of {n}) not among the game's {n_game} options"
        return f"choose {k}", "event by index"
    if t == "map":
        if screen in ("REST", "CHEST") and "proceed" in av:
            # after resting / taking the boss relic the game waits for proceed; the simulator is on the map
            return "proceed", "leave the room first"
        nodes = ss.get("next_nodes") or []
        if not nodes and ss.get("boss_available") and "boss" in (gs.get("choice_list") or []):
            return f"choose {gs['choice_list'].index('boss')}", "the boss"  # floor 15: the only way on is the boss
        i = _find(nodes, lambda n: n.get("x") == info["x"])
        if i is None:
            return None, f"map x={info['x']} not among {nodes}"
        return f"choose {i}", ""
    if t == "boss_relic" and screen == "CHEST":
        return ("choose 0", "open the boss chest") if "choose" in av else (None, "boss chest: no choose")
    if t == "boss_relic" and not info.get("relic"):  # the simulator skips the boss relic
        return ("skip", "skip the boss relic") if "skip" in av else (None, "boss relic skip: no skip")
    if t == "boss_relic":
        relics = ss.get("relics") or []
        i = _find(relics, lambda r: _norm(r.get("id")) == _norm(info.get("relic", "")))
        return (f"choose {i}", "") if i is not None else (None, "boss relic not found")
    if t == "card_select" and info.get("match_and_keep"):
        # Match and Keep!: the game's options are the face-down cards in grid order (mk_rank is the flip's place among them)
        k = info.get("mk_rank")
        if screen != "EVENT" or k is None or k >= len(opts) or len(opts) < 2:
            return None, f"match and keep: flip {k} not among {len(opts)} options"
        return f"choose {opts[k]['choice_index']}", "match and keep flip"
    if t == "card_select":
        cards = ss.get("cards") or []
        # upgrade grids (campfire Smith) leave selected_cards empty and set confirm_up instead
        if screen == "GRID" and (ss.get("selected_cards") or ss.get("confirm_up")) and "confirm" in av:
            return "confirm", ""
        # a multi-pick grid (Astrolabe, Empty Cage) shows the cards picked so far as selected, and choosing one again
        # unselects it: with identical cards (three Defends) take one not selected yet
        taken = {c.get("uuid") for c in ss.get("selected_cards") or [] if c.get("uuid")}
        # the same copy as the simulator's: the game's grid is not in deck order, and with identical copies (three
        # Perfected Strike+) the first match is another copy. The deck's order decides every fight's draw pile. The
        # game's deck is in the simulator's order
        deck = gs.get("deck") or []
        k = info.get("deck_idx")
        want = deck[k].get("uuid") if isinstance(k, int) and 0 <= k < len(deck) and _card_match(deck[k], info["card"]) \
            else None
        i = _find(cards, lambda c: want is not None and c.get("uuid") == want and c.get("uuid") not in taken)
        if i is None:
            i = _find(cards, lambda c: _card_match(c, info["card"]) and c.get("uuid") not in taken)
        if i is None:
            return None, f"card {info.get('card')} not on the grid"
        return f"choose {i}", ""
    if t in ("open_chest", "skip_chest"):
        if t == "open_chest" and "choose" in av:
            return "choose 0", ""
        return ("proceed", "") if "proceed" in av else (None, "chest: no proceed")
    if t == "rest":
        opt = info.get("option")
        if opt == "skip":
            return ("proceed", "") if "proceed" in av else (None, "rest: no proceed")
        rest_opts = [_norm(x) for x in ss.get("rest_options", []) or gs.get("choice_list", [])]
        i = _find(rest_opts, lambda x: x == opt)
        return (f"choose {i}", "") if i is not None else (None, f"rest option {opt} not in {rest_opts}")
    if t and t.startswith("reward_"):
        if screen == "CARD_REWARD":
            if t != "reward_card":  # we only opened the card reward to take a card
                return ("skip", "") if "skip" in av else ("return", "")
            if info.get("singing_bowl"):  # +2 max HP instead of a card: the game's "bowl" choice
                cl = [str(x).lower() for x in gs.get("choice_list") or []]
                return (f"choose {cl.index('bowl')}", "singing bowl") if "bowl" in cl else (None, "singing bowl: no bowl choice")
            cards = ss.get("cards") or []
            note = ""
            want = info.get("reward_cards")  # the simulator's whole chosen reward (Auto._choose), if known
            if want is not None:
                key = lambda c: (_norm(c.get("id", "")).rstrip("rgbp"), bool(c.get("upgrades")))
                if sorted(map(key, cards)) != sorted(map(key, want)):
                    note = f"card reward differs: game {[c.get('id') for c in cards]} vs simulator {[c.get('id') for c in want]}"
            i = _find(cards, lambda c: _card_match(c, info["card"]))
            return (f"choose {i}", note) if i is not None else (None, note or f"card {info.get('card')} not in the card reward")
        if screen != "COMBAT_REWARD":
            return None, f"reward on screen {screen}"
        rewards = ss.get("rewards") or []
        kind = t[len("reward_"):]
        if kind == "skip":
            return ("proceed", "") if "proceed" in av else (None, "rewards: no proceed")
        if kind == "card":
            # idx1 is the simulator's card reward index, and both lists drop a taken reward the same way: open the
            # idx1-th CARD entry, not the first (Sensory Stone gives three colorless card rewards)
            cards_at = [j for j, r in enumerate(rewards) if r.get("reward_type") == "CARD"]
            k = info.get("idx1", 0)
            i = cards_at[k] if 0 <= k < len(cards_at) else None
        elif kind == "gold":
            i = _find(rewards, lambda r: r.get("reward_type") in ("GOLD", "STOLEN_GOLD"))
        elif kind == "potion":
            i = _find(rewards, lambda r: r.get("reward_type") == "POTION" and
                      _norm((r.get("potion") or {}).get("id")) == _norm(info.get("potion", "")))
        elif kind == "relic":
            i = _find(rewards, lambda r: r.get("reward_type") == "RELIC" and
                      _norm((r.get("relic") or {}).get("id")) == _norm(info.get("relic", "")))
        elif kind == "key":
            want = "SAPPHIRE_KEY" if info.get("key") == "sapphire" else "EMERALD_KEY"
            i = _find(rewards, lambda r: r.get("reward_type") == want)
        else:
            i = None
        return (f"choose {i}", "") if i is not None else (None, f"reward {kind} not found in {[r.get('reward_type') for r in rewards]}")
    if t and t.startswith("shop_"):
        kind = t[len("shop_"):]
        if screen == "SHOP_ROOM":
            if kind == "skip":
                return ("proceed", "") if "proceed" in av else (None, "shop room: no proceed")
            return ("choose 0", "open the shop") if "choose" in av else (None, "shop room: no choose")
        if screen != "SHOP_SCREEN":
            if screen == "GRID" and kind == "card_remove":
                return None, "remove grid: decided by the next simulator step"
            return None, f"shop on screen {screen}"
        if kind == "skip":
            return ("leave", "") if "leave" in av else ("return", "")
        # the shop screen's choice list (as the real game lists it): the removal service first ("purge"), then
        # cards, relics, potions, and only what the gold can pay for
        gold = gs.get("gold", 0)
        cl = gs.get("choice_list") or []
        order = []
        if ss.get("purge_available") and ss.get("purge_cost", 10 ** 9) <= gold:
            order.append(("card_remove", None))
        for k, items in (("card", ss.get("cards") or []), ("relic", ss.get("relics") or []), ("potion", ss.get("potions") or [])):
            order += [(k, x) for x in items if x.get("price", 10 ** 9) <= gold]
        if len(order) != len(cl):
            return None, f"shop list: {len(order)} items affordable, the game offers {len(cl)}"
        match = {"card": lambda x: _card_match(x, info["card"]),
                 "relic": lambda x: _norm(x.get("id")) == _norm(info.get("relic", "")),
                 "potion": lambda x: _norm(x.get("id")) == _norm(info.get("potion", "")),
                 "card_remove": lambda x: True}.get(kind)
        if match is None:
            return None, f"unknown shop action {kind}"
        i = next((j for j, (k, x) in enumerate(order) if k == kind and match(x)), None)
        return (f"choose {i}", "") if i is not None else (None, f"shop {kind} not on offer")
    return None, f"unknown action {json.dumps(info)} on {screen}"
