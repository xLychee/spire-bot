"""The bridge's side of the in-game panel: two small files in the game's folder.

- spirebot_status.json, written here: what the panel shows (all text ready in the game's language; the mod only draws
  it). Written to a temporary file and renamed over the old one, so the mod never reads half a file.
- spirebot_control.json, written by the mod when the player presses the pause key: {"paused": true|false}.

Nothing here may hold the bot up or change what it does: every failure is swallowed. On Windows the rename can fail
while the mod has the status file open for reading; the status is then simply written again at the next step, so the
panel can be one step late (this cannot be seen on a Mac, where the rename always succeeds).
"""
from __future__ import annotations

import json
import re
import os

from . import config as CFG
from . import messages as M

STATUS = CFG.GAME_DIR / "spirebot_status.json"
CONTROL = CFG.GAME_DIR / "spirebot_control.json"
ENABLED = str(CFG._get("CM_PANEL", "panel", "1")) not in ("0", "false", "False")
# the thinking view: the network's five best options as bars, how sure it is, and the curve of its value head
VIZ = str(CFG._get("CM_VIZ", "viz", "1")) not in ("0", "false", "False")
CURVE_POINTS = 20  # the curve: the value at the last this many decisions


def sure_code(pmax: float, n: int) -> str:
    """how sure the network is, from the largest share among the options it chose from"""
    if n <= 1:
        return "ONLY_OPTION"
    return "SURE" if pmax >= 0.9 else "FAIRLY_SURE" if pmax >= 0.6 else "TORN"


def params_text(n: int | None, lang: str) -> str | None:
    if not n:
        return None
    if lang == "zh":
        return f"{n / 1e4:.0f} 万" if n < 1e8 else f"{n / 1e8:.1f} 亿"
    return f"{n / 1e6:.1f}M"


class Panel:
    def __init__(self):
        self.lang = M.game_language()
        self.n = 0
        self.state, self.floor, self.lines = "S_STARTING", None, []
        self.failed = 0
        self.viz = None
        self.curve = []  # [floor, value] at the last CURVE_POINTS decisions of this run (the network's value head)

    def think(self, rows, chosen: int, pmax: float, n: int, ms: float, params, value, floor, bits=None, cmd=None):
        """The thinking view of the decision just taken: rows = [(label, share)] (highest share first), chosen = the
        row of the option the bot takes; value = the value head's output (None: the model file has none). bits and
        cmd (the chosen action, and the game command it translates to) are only written for the tests to check
        against what was sent; the mod does not show them."""
        if isinstance(floor, int) and self.curve and floor < self.curve[-1][0]:
            self.curve = []  # a new run
        if value is not None and isinstance(floor, int):
            self.curve = (self.curve + [[floor, round(float(value), 4)]])[-CURVE_POINTS:]
        p = params_text(params, self.lang)
        ms_ = f"{ms:.0f}" if ms >= 1 else f"{ms:.1f}"
        self.viz = {"rows": [[str(t), round(float(q), 4)] for t, q in rows], "chosen": chosen,
                    "sure": M.text(sure_code(pmax, n), self.lang),
                    "model": (M.text("MODEL_PARAMS", self.lang, params=p) if p else "") if n <= 1
                    else M.text("MODEL_LINE", self.lang, params=p, ms=ms_) if p
                    else M.text("MODEL_LINE_NOPARAMS", self.lang, ms=ms_),
                    "curve": list(self.curve) or None,
                    "curve_label": M.text("FEELING", self.lang), "bits": bits, "cmd": cmd,
                    "value": None if value is None else round(float(value), 4)}

    def show(self, state: str | None = None, floor=None, lines=None):
        """Set what the panel shows (only what is given changes) and write it."""
        if state is not None:
            self.state = state
        if floor is not None:
            self.floor = floor
        if lines is not None:
            self.lines = [str(x) for x in lines][:3]
        self.write()

    def write(self):
        if not ENABLED:
            return
        self.n += 1
        title = M.text(self.state, self.lang)
        if self.floor is not None and self.state in ("S_PLAYING", "S_PAUSED", "S_STOPPED"):
            title += "   " + M.text("FLOOR", self.lang, n=self.floor)
        viz = self.viz if VIZ and self.state in ("S_PLAYING", "S_PAUSED") else None
        hint = ("HINT_PAUSED" if self.state == "S_PAUSED" else "HINT") + ("_VIZ" if viz else "")
        d = {"n": self.n, "state": self.state, "title": title, "lines": self.lines, "hint": M.text(hint, self.lang)}
        if viz:
            d["viz"] = viz
        try:
            tmp = STATUS.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, STATUS)
        except OSError:
            self.failed += 1  # (Windows: the mod is reading it) the next step writes again

    def paused(self) -> bool:
        """Has the player paused the bot? No file, or one that cannot be read, means no."""
        if not ENABLED:
            return False
        try:
            return bool(json.loads(CONTROL.read_text(encoding="utf-8")).get("paused"))
        except (OSError, ValueError, AttributeError):
            return False


# A name in a label is a marker: MARK_A + kind|keys...|fallback + MARK_B. The mod (Panel.java) puts the game's own name,
# in the game's language, in its place, found by the id: on a Windows set to English CommunicationMod sends the
# state in the system's charset and Chinese names reach the bridge as question marks, the ids intact. The
# fallback is the name as the bridge got it (shown when the mod cannot find the id).
#   c|card id|upgrades|name    m|index|monster id|number (0: none)|name    p|potion id|name    r|relic id|name
#   x|place in the game's choice list|name (an option without an id: an event's button, say)
MARK_A, MARK_B = "\x01", "\x02"
_MARK = re.compile("\x01[^\x02]*?([^|\x02]*)\x02")


def _mark(kind: str, *keys, fallback) -> str:
    return MARK_A + "|".join([kind, *(str(k).replace("|", "/") for k in keys), str(fallback).replace("|", "/")]) + MARK_B


def plain(s: str | None) -> str | None:
    """a label with the names as the bridge got them (the markers' fallbacks)"""
    return None if s is None else _MARK.sub(r"\1", s)


def _card(c: dict) -> str:
    if "id" not in c:  # tests/fake_game.py: the simulator's names
        return c.get("name", "")
    return _mark("c", c["id"], int(c.get("upgrades") or 0), fallback=c.get("name", c["id"]))


def _monster(cs: dict, k: int) -> str:
    """a monster's name as the game shows it, numbered left to right when another one is the same monster (the same
    id; by name when there are no ids)"""
    mons = cs["monsters"]
    key = (lambda m: m.get("id")) if all("id" in m for m in mons) else (lambda m: m.get("name"))
    same = [i for i, m in enumerate(mons) if key(m) == key(mons[k])]
    num = 0 if len(same) < 2 else same.index(k) + 1
    name = mons[k].get("name", "")
    shown = name if not num else f"{name} {num}"
    if "id" not in mons[k]:
        return shown
    return _mark("m", k, mons[k]["id"], num, fallback=shown)


def _potion(p: dict) -> str:
    return _mark("p", p["id"], fallback=p.get("name", p["id"])) if "id" in p else p.get("name", "")


def _relic(r: dict) -> str:
    return _mark("r", r["id"], fallback=r.get("name", r["id"])) if "id" in r else r.get("name", "")


# the simulator's options on the card reward that leave the cards for another reward (the game's command is skip;
# the reward screen comes next): which reward
SKIP_FOR = {"reward_gold": "C_gold", "reward_relic": "C_relic", "reward_potion": "C_potion"}


def option_label(cmd: str | None, gs: dict, lang: str, info: dict | None = None) -> str | None:
    """A game command as the player would say it, with the names the game itself shows (so in the game's language).
    info: the simulator's action the command comes from, when known (tells apart options that are the same command)."""
    if not cmd:
        return None
    w = cmd.split()
    cs = gs.get("combat_state") or {}
    try:
        if w[0] == "play":
            s = M.text("PLAY", lang, card=_card(cs["hand"][int(w[1]) - 1]))
            if len(w) > 2:
                s += " -> " + _monster(cs, int(w[2]))
            return s
        if w[0] == "end":
            return M.text("END_TURN", lang)
        if w[0] == "potion":
            s = M.text("POTION_USE" if w[1] == "use" else "POTION_DISCARD", lang, potion=_potion(gs["potions"][int(w[2])]))
            if len(w) > 3:
                s += " -> " + _monster(cs, int(w[3]))
            return s
        if w[0] == "choose":
            k = int(w[1])
            cl = gs.get("choice_list") or []
            what = str(cl[k])
            if what.startswith("x=") and what[2:].isdigit():
                return M.text("MAP_NODE", lang, x=int(what[2:]) + 1)
            if "C_" + what in M.TABLE:
                return M.text("C_" + what, lang)
            ss = gs.get("screen_state") or {}
            # the thing chosen, by id: the simulator's action names it (rewards, the shop, the boss relic) ...
            it = info or {}
            if isinstance(it.get("card"), dict) and it["card"].get("id"):
                c = it["card"]
                seen = next((x for x in ss.get("cards") or [] if isinstance(x, dict) and x.get("id") == c["id"]), {})
                return M.text("CHOOSE", lang, what=_card({"id": c["id"], "upgrades": int(bool(c.get("upgrades"))),
                                                          "name": seen.get("name", what)}))
            for kind, key, mk in (("relic", "relics", _relic), ("potion", "potions", _potion)):
                if isinstance(it.get(kind), str) and it.get("type", "").endswith(kind):
                    seen = next((x for x in ss.get(key) or [] if isinstance(x, dict) and x.get("id") == it[kind]), {})
                    return M.text("CHOOSE", lang, what=mk({"id": it[kind], "name": seen.get("name", what)}))
            # ... else the screen's own list in the choice list's order (a card reward, a grid, a hand pick)
            for key, mk in (("cards", _card), ("hand", _card), ("relics", _relic), ("potions", _potion)):
                items = [x for x in ss.get(key) or [] if isinstance(x, dict)]
                if items and len(items) == len(cl) and "id" in items[k]:
                    return M.text("CHOOSE", lang, what=mk(items[k]))
            # ... else its place in the choice list (an event's button): the mod has the list in the right characters
            return M.text("CHOOSE", lang, what=_mark("x", k, fallback=what))
        if w[0] == "skip" and info and info.get("type") in SKIP_FOR:
            return M.text("SKIP_FOR", lang, what=M.text(SKIP_FOR[info["type"]] + "_THE", lang))
        if w[0] in ("proceed", "skip", "leave", "confirm"):
            return M.text(w[0].upper(), lang)
    except (LookupError, ValueError, TypeError):
        return None
    return None


def distinct(labels: list) -> list:
    """labels still alike after naming (the same card at the same target from two places in the hand, say) get a
    number, in their order: "Skip (1)", "Skip (2)" """
    seen = {}
    for x in labels:
        seen[x] = seen.get(x, 0) + 1
    k, out = {}, []
    for x in labels:
        if seen[x] > 1:
            k[x] = k.get(x, 0) + 1
            out.append(f"{x} ({k[x]})")
        else:
            out.append(x)
    return out
    w = cmd.split()
    cs = gs.get("combat_state") or {}
    try:
        if w[0] == "play":
            s = M.text("PLAY", lang, card=cs["hand"][int(w[1]) - 1]["name"])
            if len(w) > 2:
                s += " -> " + cs["monsters"][int(w[2])]["name"]
            return s
        if w[0] == "end":
            return M.text("END_TURN", lang)
        if w[0] == "potion":
            s = M.text("POTION_USE" if w[1] == "use" else "POTION_DISCARD", lang, potion=gs["potions"][int(w[2])]["name"])
            if len(w) > 3:
                s += " -> " + cs["monsters"][int(w[3])]["name"]
            return s
        if w[0] == "choose":
            what = str((gs.get("choice_list") or [])[int(w[1])])
            if what.startswith("x=") and what[2:].isdigit():
                return M.text("MAP_NODE", lang, x=int(what[2:]) + 1)
            if "C_" + what in M.TABLE:
                return M.text("C_" + what, lang)
            return M.text("CHOOSE", lang, what=what)
        if w[0] in ("proceed", "skip", "leave", "confirm"):
            return M.text(w[0].upper(), lang)
    except (LookupError, ValueError, TypeError):
        return None
    return None
